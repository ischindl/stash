"""The local route speaks the dialect a box actually answers.

The completion layer used to speak only Anthropic, so a deployment whose whole
point is that it has no cloud key could not run any backend-side completion at
all. The local box here is a real HTTP server on a real socket, not a fake
client: what the local route promises is bytes — the `/chat/completions`
suffix, the bearer header, the chat-shaped body, the reply mapped back into
Anthropic blocks — and a fake client would let any one of those drift
unnoticed.

Scoped curation is not asserted here: it runs on the provider the backend's
configuration names (`scoped_curation_service.require_configured`), so the
question this suite answers is only what a configured local endpoint sounds
like on the wire.
"""

import http.server
import json
import threading

import pytest

from backend.services import llm

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


def _turn(tier, system, messages, tools, max_tokens) -> dict:
    """One round, called the way a route's caller calls it."""
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
