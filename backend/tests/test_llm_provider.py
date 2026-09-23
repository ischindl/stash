"""Whichever provider the deployment configured is the one that answers.

The completion layer used to speak only Anthropic, so every backend-side
completion — including the external-wiki curation that is the point of a
self-hosted box — was dead on exactly the stacks that have no cloud key by
design. This suite is the pair that discriminates the fix: with no key and an
endpoint the console stored, a completion is served by that endpoint and the
Anthropic client is never constructed; with the key, the key answers first and
the box sees no request at all. Neither half is a fallback for the other, which
is why both are asserted here rather than left to a run that happened to work.

The local box is a real HTTP server on a real socket, not a fake client: what
the local route promises is bytes — the `/chat/completions` suffix, the bearer
header, the chat-shaped body, the reply mapped back into Anthropic blocks — and
a fake client would let any one of those drift unnoticed.
"""

import http.server
import json
import socket
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet

from backend.config import settings
from backend.services import agent_auth, agent_service, llm
from backend.services import scoped_curation_service as curation

from .conftest import unique_name

# The curator suite's workspace fixture, imported under its own name so the
# shared workspace, end users, and shared/private documents are not rebuilt here.
from .test_scoped_curation import dataset as scoped_dataset  # noqa: F401

BOX_MODEL = "qwen2.5-coder"


def _stopped(text: str) -> dict:
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def _called(calls: list[tuple[str, str, dict]]) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                        for call_id, name, arguments in calls
                    ],
                },
            }
        ]
    }


class _Handler(http.server.BaseHTTPRequestHandler):
    """Answers `/models` as a box being connected would and chat completions as
    a box mid-run would — with whatever this test needs it to get wrong."""

    def do_GET(self):
        self.server.box.models_requests += 1
        self._reply(200, json.dumps({"data": [{"id": BOX_MODEL}], "object": "list"}))

    def do_POST(self):
        box = self.server.box
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        box.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        status, text = box.reply(body)
        self._reply(status, text)

    def _reply(self, status: int, text: str):
        payload = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class _Box:
    """One OpenAI-compatible endpoint under test.

    `answers` scripts one conversation in order; `policy` answers per request
    instead, for a run that issues several completions at once; `raw` with
    `status` stands in for the proxy that answers 502 with nginx's own words."""

    def __init__(self, answers=(), policy=None, status=200, raw=None):
        self.answers = list(answers)
        self.policy = policy
        self.status = status
        self.raw = raw
        self.requests: list[dict] = []
        self.models_requests = 0
        self.base_url = ""

    def reply(self, conversation: dict) -> tuple[int, str]:
        if self.raw is not None:
            return self.status, self.raw
        answer = self.policy(conversation) if self.policy else self.answers.pop(0)
        return self.status, json.dumps(answer)


@pytest.fixture
def local_box():
    servers: list[http.server.ThreadingHTTPServer] = []

    def serve(**kwargs) -> _Box:
        box = _Box(**kwargs)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.box = box
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        # The shape an operator types into the console, version suffix included.
        box.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        return box

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _credentials_can_be_stored(monkeypatch):
    """A connected credential is stored encrypted, and the test stack ships no
    key of its own — the console generates one per deployment."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _never_anthropic():
    raise AssertionError("a local-only stack must never build the Anthropic client")


def _closed_endpoint() -> str:
    """A base URL on a port nothing will answer on — the box that is off."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}/v1"


def _turn(tier, system, messages, tools, max_tokens) -> dict:
    """One round, called the way the curator's loop calls it."""
    return {
        "tier": tier,
        "system": system,
        "messages": messages,
        "tools": tools,
        "max_tokens": max_tokens,
    }


async def _complete(box: _Box, **kwargs) -> llm.Turn:
    call = _turn(llm.ModelTier.QUALITY, None, [], [], 1024)
    call.update(kwargs)
    return await llm.local_route(box.base_url, BOX_MODEL, "box-key").complete(**call)


@pytest.mark.asyncio
async def test_the_local_route_asks_the_box_the_chat_question(local_box):
    box = local_box(answers=[_stopped("Curated.")])

    turn = await _complete(
        box,
        system="RULES",
        messages=[{"role": "user", "content": "Curate the permitted documents."}],
        tools=[
            {
                "name": "search_documents",
                "description": "Search allowed documents.",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }
        ],
        max_tokens=16384,
    )

    assert (turn.stop_reason, turn.text) == ("end_turn", "Curated.")
    (request,) = box.requests
    assert request["path"] == "/v1/chat/completions", "the suffix is joined once, not doubled"
    assert request["authorization"] == "Bearer box-key"
    body = request["body"]
    assert body["model"] == BOX_MODEL, "a local endpoint has the one model its credential stores"
    assert body["max_tokens"] == 16384
    assert body["messages"] == [
        {"role": "system", "content": "RULES"},
        {"role": "user", "content": "Curate the permitted documents."},
    ]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_documents",
                "description": "Search allowed documents.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ], "the curator declares Anthropic tools; the box is owed the function form"


@pytest.mark.asyncio
async def test_a_keyless_box_is_still_addressed(local_box):
    # Ollama and its friends authenticate nobody on a private network. The dummy
    # bearer is the one codepath, not a branch on whether a secret exists.
    box = local_box(answers=[_stopped("ok")])

    await llm.local_route(box.base_url, BOX_MODEL, None).complete(
        **_turn(llm.ModelTier.QUALITY, None, [], [], 64)
    )

    assert box.requests[0]["authorization"] == "Bearer local"
    assert box.requests[0]["body"]["messages"] == [], "no system prompt means no empty system role"
    assert "tools" not in box.requests[0]["body"]


@pytest.mark.asyncio
async def test_the_tool_loop_history_survives_the_chat_form(local_box):
    """A tool loop closes only if the answer round-trips: the assistant's own
    tool call has to come back as history, and the tool's result has to reach
    the model that is waiting for it."""
    box = local_box(answers=[_stopped("Done.")])

    await _complete(
        box,
        messages=[
            {"role": "user", "content": "Curate."},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading it now."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "read_document",
                        "input": {"document_id": "doc"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": '{"content":"PAGE"}',
                    }
                ],
            },
        ],
    )

    assert box.requests[0]["body"]["messages"] == [
        {"role": "user", "content": "Curate."},
        {
            "role": "assistant",
            "content": "Reading it now.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_document", "arguments": '{"document_id": "doc"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"content":"PAGE"}'},
    ]


@pytest.mark.asyncio
async def test_a_history_the_box_cannot_be_told_is_not_guessed_at(local_box):
    for blocks in (
        [{"type": "thinking", "thinking": "private reasoning"}],
        [{"type": "document", "source": {"data": "..."}}],
    ):
        box = local_box(answers=[_stopped("ok")])
        with pytest.raises(RuntimeError, match="has no local-endpoint form"):
            await _complete(box, messages=[{"role": "user", "content": blocks}])
        assert box.requests == [], "a block dropped here is a model resuming as if it had read it"


@pytest.mark.asyncio
async def test_a_tool_call_from_the_box_arrives_as_an_anthropic_turn(local_box):
    asked = {"page_id": None, "title": "Scheduling", "content": "Tuesdays."}
    box = local_box(
        answers=[_called([("call_9", "write_page", asked)]), _stopped("Curated one page.")]
    )
    route = llm.local_route(box.base_url, BOX_MODEL, "box-key")

    turn = await route.complete(**_turn(llm.ModelTier.QUALITY, None, [], [], 1024))

    assert turn.stop_reason == "tool_use"
    assert turn.text == "", "a turn that only called tools has not said anything yet"
    assert turn.tool_calls == [llm.ToolCall(id="call_9", name="write_page", input=asked)]
    assert turn.blocks == [
        {"type": "tool_use", "id": "call_9", "name": "write_page", "input": asked}
    ]

    second = await route.complete(
        **_turn(
            llm.ModelTier.QUALITY, None, [{"role": "assistant", "content": turn.blocks}], [], 1024
        )
    )

    assert (second.stop_reason, second.text) == ("end_turn", "Curated one page.")
    assert second is not None
    assert box.requests[1]["body"]["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "write_page", "arguments": json.dumps(asked)},
                }
            ],
        }
    ], "content is null, not an empty string, which a server reads as an answer"


@pytest.mark.asyncio
async def test_an_answer_cut_off_keeps_the_server_s_own_word(local_box):
    box = local_box(
        answers=[{"choices": [{"finish_reason": "length", "message": {"content": "half a"}}]}]
    )

    turn = await _complete(box)

    assert turn.stop_reason == "length", (
        "a truncated page reported as end_turn would be published as finished"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        ({"choices": []}, "no choice"),
        ({"choices": [{"finish_reason": "stop"}]}, "no message"),
        ({"choices": [{"message": {"content": "hi"}}]}, "no finish_reason"),
        (
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"tool_calls": [{"function": {"name": "x", "arguments": "{}"}}]},
                    }
                ]
            },
            "needs an id",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {"id": "1", "function": {"name": "x", "arguments": "{oops"}}
                            ]
                        },
                    }
                ]
            },
            "not JSON",
        ),
    ],
)
async def test_an_answer_nobody_can_execute_names_the_box(local_box, answer, refusal):
    box = local_box(answers=[answer])

    with pytest.raises(RuntimeError, match=f"local model endpoint .*{refusal}"):
        await _complete(box)


@pytest.mark.asyncio
async def test_a_proxy_is_reported_with_its_own_words(local_box):
    # Ollama, vLLM, and a captive-portal proxy all answer 200, and the bytes are
    # the only text in the failure that says which of them answered.
    nginx = local_box(
        status=502, raw="<html>\n  <head>\n    <title>502 Bad Gateway</title>\n  </head>\n</html>"
    )
    with pytest.raises(RuntimeError, match=r"returned 502: <html> <head> <title>502 Bad Gateway"):
        await _complete(nginx)

    not_json = local_box(raw="OK")
    with pytest.raises(RuntimeError, match="did not answer JSON: OK"):
        await _complete(not_json)


# --- which provider answers ----------------------------------------------------


async def _self_hosted_user(pool) -> UUID:
    return await pool.fetchval(
        "INSERT INTO users (id, name, display_name) VALUES ($1, $2, $2) RETURNING id",
        uuid4(),
        unique_name("selfhost"),
    )


async def _connect_box(user: UUID, box: _Box, model: str = BOX_MODEL, api_key="box-key") -> UUID:
    """Store the endpoint the console stores it — the same doc and secret form."""
    return await agent_auth.store_credential(
        user,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(box.base_url, model, api_key),
        agent_auth.endpoint_name(box.base_url),
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_the_backend_key_answers_before_any_box_is_consulted(pool, local_box, monkeypatch):
    """Hosted behaviour, unchanged: a key that is set is never second-guessed by
    a box that happens to be connected too. The box below is reachable and would
    answer — if resolution ever asked it, the probe and the request would show."""
    user = await _self_hosted_user(pool)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-hosted-key")
    box = local_box(answers=[_stopped("never asked")])
    await _connect_box(user, box)
    asked: list[dict] = []

    class _Messages:
        async def create(self, **kwargs):
            asked.append(kwargs)
            block = SimpleNamespace(type="text", text="hosted answer", model_dump=dict)
            return SimpleNamespace(stop_reason="end_turn", content=[block])

    monkeypatch.setattr(llm, "_get_client", lambda: SimpleNamespace(messages=_Messages()))

    turn = await (await curation.require_route(user)).complete(
        **_turn(llm.ModelTier.QUALITY, "RULES", [], [], 1024)
    )

    assert turn.text == "hosted answer"
    assert asked[0]["system"] == "RULES", "the hosted call keeps the system parameter, not a role"
    assert asked[0]["model"] == settings.ANTHROPIC_MODEL
    assert (box.requests, box.models_requests) == ([], 0), (
        "the key is the answer; the box is never probed, asked, or fallen back to"
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_no_key_and_no_endpoint_names_both_providers(pool, monkeypatch):
    user = await _self_hosted_user(pool)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    with pytest.raises(agent_auth.ProviderNotConfigured) as refused:
        await curation.require_route(user)

    message = str(refused.value)
    assert "ANTHROPIC_API_KEY" in message, "the self-hoster is told the knob he skipped"
    assert "local model endpoint" in message, "and the alternative he could connect instead"


@pytest.mark.db
@pytest.mark.asyncio
async def test_a_box_that_is_not_answering_is_refused_with_its_own_error(pool, monkeypatch):
    # The run is refused before a completion is attempted: a curator that
    # dispatched onto a powered-off box would burn the lane's one metered run.
    user = await _self_hosted_user(pool)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    off = _closed_endpoint()
    await agent_auth.store_credential(
        user,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(off, BOX_MODEL, None),
        "box that is off",
    )

    with pytest.raises(agent_auth.ProviderNotConfigured) as refused:
        await curation.require_route(user)

    message = str(refused.value)
    assert off in message, "the refusal names the box that did not answer, not some box"
    assert "is not answering: " in message, "and carries the transport's own reason"
    assert message.split("is not answering: ")[1].strip(), (
        "carries an actual reason, not an empty tail"
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_the_row_pins_the_endpoint_and_may_override_only_its_model(
    pool, local_box, monkeypatch
):
    """The console's model picker writes `agents.model`, which for a local
    runtime is a model id on the box named by the row — never a URL or a key,
    which stay whatever the connect stored."""
    user = await _self_hosted_user(pool)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    unchosen = local_box(answers=[_stopped("wrong box")])
    pinned = local_box(answers=[_stopped("right box")])
    await _connect_box(user, unchosen, "model-one", "key-one")
    pinned_id = await _connect_box(user, pinned, "model-two", "key-two")

    route = await curation.require_route(
        user, {"credential_id": str(pinned_id), "model_id": "qwen3:32b"}
    )
    turn = await route.complete(**_turn(llm.ModelTier.QUALITY, None, [], [], 64))

    assert turn.text == "right box"
    assert (unchosen.requests, unchosen.models_requests) == ([], 0), (
        "the other endpoint stays untouched"
    )
    assert pinned.models_requests == 1, "the box that answers is probed once, before the run"
    assert pinned.requests[0]["body"]["model"] == "qwen3:32b"
    assert pinned.requests[0]["authorization"] == "Bearer key-two", (
        "the row chooses which box; the credential decides how to reach it"
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_a_row_pinned_to_a_key_cannot_be_moved_onto_a_box(pool, monkeypatch):
    user = await _self_hosted_user(pool)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    cloud_key = await agent_auth.store_credential(
        user, "anthropic", "api_key", "sk-ant-pinned-claude", name="anthropic"
    )

    with pytest.raises(RuntimeError, match="is a anthropic key, not a local endpoint"):
        await curation.require_route(user, {"credential_id": str(cloud_key)})


# --- the acceptance proof ------------------------------------------------------


@pytest.mark.db
@pytest.mark.asyncio
async def test_the_external_wiki_curator_completes_on_the_box(
    scoped_dataset,  # noqa: F811 — this parameter is the imported fixture, injected by pytest
    pool,
    local_box,
    monkeypatch,
):
    """The founder's stuck curator, on his own stack.

    No backend key, one connected endpoint, and the external wiki — the lane
    whose feed mixes every end user's transcripts, which is why it may never
    reach a shell-equipped sprite. Here the completion is served locally while
    the tool loop stays the scoped one: the backend still selects the
    documents, and the Anthropic client is never constructed."""

    def answer_run(conversation: dict) -> dict:
        # Each scope opens by listing its documents and closes on its summary,
        # however many scopes this run is fanning out to at once.
        if any(message["role"] == "tool" for message in conversation["messages"]):
            return _stopped("Curated one page from the local endpoint.")
        return _called([("call_search", "search_documents", {"query": "", "offset": 0})])

    box = local_box(policy=answer_run)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    await _connect_box(scoped_dataset.owner, box)
    monkeypatch.setattr(llm, "_get_client", _never_anthropic)
    curator_id = UUID(
        (await agent_service.get_or_create_curator(scoped_dataset.owner, wiki="external"))["id"]
    )
    # A row seeded to a backfill point has a watermark; this run is the bootstrap
    # from real history, which is the state a founder's first local run is in.
    await pool.execute(
        "UPDATE agents SET curated_through = NULL, curated_through_event_id = NULL WHERE id = $1",
        curator_id,
    )
    curator = await agent_service.get_curator_by_id(curator_id)

    outcome = await curation.run(curator, scoped_dataset.workspace, "20260923000000")

    assert outcome == "Scoped curation completed.", outcome
    assert box.requests, "the run asked the box, not the cloud"
    assert {request["path"] for request in box.requests} == {"/v1/chat/completions"}
    assert {request["body"]["model"] for request in box.requests} == {BOX_MODEL}, (
        "one run, one provider: no scope of it silently answered somewhere else"
    )
    closed = [
        request
        for request in box.requests
        if any(message["role"] == "tool" for message in request["body"]["messages"])
    ]
    assert closed, "the scoped tool loop closed: the backend's own tool result reached the model"
    assert json.loads(closed[0]["body"]["messages"][-1]["content"])["documents"], (
        "and what reached it was the documents the backend selected, not the model's own guess"
    )
    watermark = await pool.fetchval("SELECT curated_through FROM agents WHERE id = $1", curator_id)
    assert watermark is not None, "a run that curated advances the watermark it was stuck on"
