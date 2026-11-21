"""The identity channel: two headers at the auth boundary, two env vars at the box.

A dispatched run's credential IS its owner's key, so every page write that run
makes looks, to the server, like its owner clicked save. The run's own identity
therefore has to travel with it across the process boundary and back:

    harness child env  STASH_AGENT_SESSION + STASH_AGENT_ID   (sprite_agent_service)
        -> CLI sends   X-Stash-Agent-Session + X-Stash-Agent-Id (cli/client.py)
        -> server resolves the id to the `agents` row's display name and maps the
           pair to a PageActor at auth, where the credential is resolved.

The pair carries ids only, never a display name: a header value must be ASCII-
encodable on the wire, and an agent name (an em dash, a Slovak diacritic) is not.
The name lands in `page_edits.agent_name` because the server reads it out of the
`agents` row, so it arrives byte-exact while nothing non-ASCII ever travels.

Two properties are worth pinning, and they pull in opposite directions:

*The channel must close.* A pair that names a real run of this account has to
arrive at the write, or the whole feature is decorative.

*The channel must not be a privilege.* It is provenance: the id is checked against
the owner-scoped `agents` row (one primary-key lookup) and the run against
`history_events` (one indexed lookup), and page authorization stays entirely in
`_check_content_access` and the owner-scoped `WHERE` clauses. A pair can name
no page the owner check would not already open, and a half pair, a non-uuid, an
id naming no agent, or a fabricated pair is refused where it arrives, with a
message that says what failed.

See docs/page-provenance.md.
"""

import uuid

import pytest
from httpx import AsyncClient

from backend.config import settings
from backend.services import agent_auth, agent_service, sprite_agent_service, sprite_service

from .test_curator import _auth, _push_events, _register
from .test_page_provenance import CURATOR_RUN  # the id shape a curator run really mints

# The window the box below states. Every local endpoint row carries its own, so the
# fixture has to name one: 32768 derives to the shipped 8192 output ceiling, so stating
# it moves the window and nothing else in this file.
WINDOW = 32768

SESSION_HEADER = "X-Stash-Agent-Session"
ID_HEADER = "X-Stash-Agent-Id"
AGENT_SESSION_ENV = "STASH_AGENT_SESSION"
AGENT_ID_ENV = "STASH_AGENT_ID"
AGENT_NAME = "Folder Curator"
# The founder curator lanes' real name shape: a display name that CANNOT cross an
# ASCII header. Written as escapes so this test file stays ASCII.
EM_DASH_NAME = "Wiki curator \u2014 Fusion \u2014 saneca"


async def _folder(client: AsyncClient, key: str, name: str) -> str:
    r = await client.post("/api/v1/me/folders", json={"name": name}, headers=_auth(key))
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _agent(client: AsyncClient, key: str, name: str) -> str:
    """A real `agents` row through the real route; returns its id."""
    r = await client.post("/api/v1/me/agents", json={"name": name}, headers=_auth(key))
    assert r.status_code in (200, 201), r.text
    return str(r.json()["id"])


async def _new_page(client: AsyncClient, key: str, folder_id: str, extra: dict[str, str] = {}):
    return await client.post(
        "/api/v1/me/pages/new",
        json={"name": "attributed.md", "folder_id": folder_id, "content": "v1"},
        headers={**_auth(key), **extra},
    )


async def _attribution(pool, page_id):
    return await pool.fetchrow(
        "SELECT agent_name, edited_by, session_id FROM page_edits WHERE page_id = $1",
        page_id,
    )


@pytest.mark.asyncio
async def test_request_headers_become_the_pages_writer(client: AsyncClient, _db_pool):
    """The closed channel, end to end over the real auth dependency -- with the
    hazard this card killed built in: the agent's display name carries a U+2014
    em dash, which no header value may ever do. The request sends only the run's
    session id and the agent's uuid; the server resolves the name out of the
    `agents` row, and `page_edits.agent_name` still lands byte-exact as that
    em-dash name. `edited_by` keeps naming the owner, because that is what a
    credential proves. This is the regression: before STAS-498 the name rode the
    wire, so an agent with a non-ASCII name could not make ONE attributed call."""
    key, uid = await _register(client)
    folder_id = await _folder(client, key, "wiki")
    agent_id = await _agent(client, key, EM_DASH_NAME)
    await _push_events(
        client,
        key,
        [
            {
                "agent_name": EM_DASH_NAME,
                "event_type": "assistant_message",
                "content": "curating",
                "session_id": CURATOR_RUN,
            }
        ],
    )

    claimed = {SESSION_HEADER: CURATOR_RUN, ID_HEADER: agent_id}
    # Nothing display-shaped may ride the wire: every identity header value has
    # to be ASCII by construction, or a future edit has reintroduced the defect.
    assert all(value.isascii() for value in claimed.values()), claimed

    resp = await _new_page(client, key, folder_id, claimed)
    assert resp.status_code == 201, resp.text

    row = await _attribution(_db_pool, resp.json()["id"])
    assert (row["agent_name"], row["session_id"], row["edited_by"]) == (
        EM_DASH_NAME,
        CURATOR_RUN,
        uid,
    )


@pytest.mark.asyncio
async def test_a_request_with_no_headers_is_a_human(client: AsyncClient, _db_pool):
    """The dashboard's own save sends neither header, and that absence is the
    answer: a person. Not 'unknown', not a 400 -- `agent_name IS NULL`."""
    key, uid = await _register(client)
    folder_id = await _folder(client, key, "notes")

    resp = await _new_page(client, key, folder_id)
    assert resp.status_code == 201, resp.text

    row = await _attribution(_db_pool, resp.json()["id"])
    assert (row["agent_name"], row["session_id"], row["edited_by"]) == (None, None, uid)


@pytest.mark.asyncio
async def test_one_header_alone_is_refused_and_says_which_one_is_missing(
    client: AsyncClient, _db_pool
):
    """A pair can arrive halved only through a broken injection, and a write
    credited to a session nobody can name is worse than a refused request. The
    400 names the missing header AND the present one, so the operator reading a
    field 400 needs the injector's source, not a debugger -- the likeliest real
    sender is a box still running a CLI from the name-pair era."""
    key, _uid = await _register(client)
    folder_id = await _folder(client, key, "notes")

    only_session = await _new_page(client, key, folder_id, {SESSION_HEADER: CURATOR_RUN})
    assert only_session.status_code == 400
    assert ID_HEADER in only_session.text and SESSION_HEADER in only_session.text

    only_id = await _new_page(client, key, folder_id, {ID_HEADER: str(uuid.uuid4())})
    assert only_id.status_code == 400
    assert SESSION_HEADER in only_id.text and ID_HEADER in only_id.text

    # Refused means refused: nothing reached the history table.
    assert await _db_pool.fetchval("SELECT count(*) FROM page_edits") == 0


@pytest.mark.asyncio
async def test_an_id_that_names_no_agent_of_this_account_is_refused(client: AsyncClient, _db_pool):
    """The first lookup is a primary-key read of `agents` scoped to the calling
    account: a syntactically valid uuid with no row behind it is refused naming
    the table, and a claim that is not even a uuid (the old lane-name spelling is
    the one a stale injector would send) is refused naming the header and quoting
    the value it would not parse -- the injector guarantees a uuid, so anything
    else is fabricated or stale."""
    key, _uid = await _register(client)
    folder_id = await _folder(client, key, "notes")

    ghost = await _new_page(
        client, key, folder_id, {SESSION_HEADER: CURATOR_RUN, ID_HEADER: str(uuid.uuid4())}
    )
    assert ghost.status_code == 400
    assert "agents" in ghost.text

    not_a_uuid = await _new_page(
        client, key, folder_id, {SESSION_HEADER: CURATOR_RUN, ID_HEADER: AGENT_NAME}
    )
    assert not_a_uuid.status_code == 400
    assert ID_HEADER in not_a_uuid.text
    assert AGENT_NAME in not_a_uuid.text, "the refusal quotes the value it refused"


@pytest.mark.asyncio
async def test_a_pair_naming_no_run_of_this_account_is_refused_with_its_own_cause(
    client: AsyncClient, _db_pool
):
    """A claim is checked against `history_events`, the one table that knows which
    runs an account had. A fabricated pair is refused with the query's own terms --
    table, resolved agent name, session id -- because the day a history-pruning job
    starts turning legitimate claims into 400s, that sentence is the only thing
    that will say why. The id here names a real agent of the account, so the refusal
    can only come from the run half."""
    key, _uid = await _register(client)
    folder_id = await _folder(client, key, "notes")
    agent_id = await _agent(client, key, AGENT_NAME)

    resp = await _new_page(
        client,
        key,
        folder_id,
        {SESSION_HEADER: "agent-curate-never-ran", ID_HEADER: agent_id},
    )
    assert resp.status_code == 400
    assert "history_events" in resp.text
    assert "agent-curate-never-ran" in resp.text and AGENT_NAME in resp.text
    assert await _db_pool.fetchval("SELECT count(*) FROM page_edits") == 0


@pytest.mark.asyncio
async def test_a_stolen_pair_buys_nothing(client: AsyncClient, _db_pool):
    """The claim is provenance, not a credential. User B presenting user A's real
    id+session pair cannot write into A's scope and cannot read A's page: the owner
    check is the same one that ran before this card existed. What B can do with A's
    pair is fail the owner-scoped `agents` lookup -- and be told so -- because the
    row it names belongs to someone else, and the refusal quotes only the id B
    itself supplied, never whose account the lookup actually keyed on."""
    key_a, uid_a = await _register(client)
    key_b, _uid_b = await _register(client)
    agent_id_a = await _agent(client, key_a, AGENT_NAME)
    await _push_events(
        client,
        key_a,
        [
            {
                "agent_name": AGENT_NAME,
                "event_type": "assistant_message",
                "content": "curating",
                "session_id": CURATOR_RUN,
            }
        ],
    )
    folder_a = await _folder(client, key_a, "private-wiki")
    page_a = await _new_page(client, key_a, folder_a)
    assert page_a.status_code == 201

    claimed = {SESSION_HEADER: CURATOR_RUN, ID_HEADER: agent_id_a}
    folder_b = await _folder(client, key_b, "b-wiki")

    write = await _new_page(client, key_b, folder_b, claimed)
    assert write.status_code == 400, "A's pair names no agent of B's"
    assert str(uid_a) not in write.text, "the refusal must not leak A's account id"

    read = await client.get(
        f"/api/v1/pages/{page_a.json()['id']}",
        headers={**_auth(key_b), **claimed},
    )
    assert read.status_code == 400, "the claim is checked before it can name anything"

    # And with the unusable claim dropped, it is the owner check that keeps A's
    # page private -- the same check that existed before this channel did. The
    # canonical read answers 404 for anything it will not open.
    plain = await client.get(f"/api/v1/pages/{page_a.json()['id']}", headers=_auth(key_b))
    assert plain.status_code == 404


@pytest.mark.asyncio
async def test_an_anonymous_request_that_claims_a_run_is_refused(client: AsyncClient):
    """`get_current_user_optional`'s unauthenticated branch installs an actor too,
    so no request leaves the auth boundary undefined -- and an anonymous caller that
    claims a run is refused rather than silently recorded as a person. Anonymous
    routes cannot reach this far: `/api/v1/demo/*` has no auth dependency at all,
    which is exactly why it declares its own actor instead."""
    resp = await client.get(
        "/api/v1/pages/00000000-0000-0000-0000-000000000000",
        headers={SESSION_HEADER: CURATOR_RUN, ID_HEADER: str(uuid.uuid4())},
    )
    assert resp.status_code == 400
    assert SESSION_HEADER in resp.text and ID_HEADER in resp.text


async def _capturing_exec(monkeypatch, captured: list[dict]):
    """Record every exec the turn performs -- argv and the environment handed to it --
    and let the `sprite_exec` fixture's canned reply answer as normal."""
    inner = sprite_service.exec_stream

    async def capture(sprite, argv, *, env, cwd=None):
        captured.append({"argv": argv, "env": env})
        async for event in inner(sprite, argv, env=env, cwd=cwd):
            yield event

    monkeypatch.setattr(sprite_service, "exec_stream", capture)


def _harness_exec(captured: list[dict]) -> dict:
    """The turn's own exec, out of everything the turn ran.

    Setting a turn up also execs `curl` on the box (the credential/MCP config
    write), so "the first exec" is not the harness run. The harness argv is the one
    that is not a curl, and there is exactly one per turn."""
    runs = [c for c in captured if c["argv"][0] != "curl"]
    assert len(runs) == 1, f"expected one harness exec, saw {[c['argv'][0] for c in captured]}"
    return runs[0]


@pytest.mark.asyncio
async def test_a_turn_hands_the_harness_child_its_own_identity(
    client: AsyncClient, sprite_exec, monkeypatch, _db_pool
):
    """The other half of the channel: the run's session id and the id of the agent
    row it runs go into the environment the harness is exec'd with, so the `stash`
    CLI inside the turn can name the run on the way back -- and the old name
    variable is gone, so no child can spell a display name onto a header.

    Asserted below `_run_harness` -- on the env dict `exec_stream` receives -- because
    that is the boundary the CLI actually reads, not the kwargs some helper was
    handed. The streaming lane is driven here (an interactive chat turn), since the
    two lanes build their turn environment at different points."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    captured: list[dict] = []
    await _capturing_exec(monkeypatch, captured)
    key, uid = await _register(client)
    session_id = "agent-attribution-chat"
    # The route resolves the default agent when the chat sends no agent_id.
    default_agent = await agent_service.get_or_create_default(uid)

    resp = await client.post(
        "/api/v1/me/agent-chat",
        json={"message": "attribute me", "session_id": session_id},
        headers=_auth(key),
    )
    assert resp.status_code == 200, resp.text

    run = _harness_exec(captured)
    env = run["env"]
    assert env[AGENT_SESSION_ENV] == session_id
    assert env[AGENT_ID_ENV] == str(default_agent["id"])
    assert ("STASH_AGENT_" + "NAME") not in env, "the name never goes on the wire"
    # The provider key is still there: the identity is added to the environment,
    # not swapped for it.
    assert env["ANTHROPIC_API_KEY"]


@pytest.mark.asyncio
async def test_a_pinned_provider_turn_carries_the_same_identity(
    client: AsyncClient, sprite_exec, monkeypatch, _db_pool
):
    """A curator pinned to a self-hosted box dials no Anthropic key, and the CLI
    inside it still has to say which run wrote the page. The two names are injected
    after `agent_auth.resolve` returns, so no provider, box, or credential shape
    can drop them -- and the pinned local endpoint is precisely the lane the
    founder's own curator runs."""
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    captured: list[dict] = []
    await _capturing_exec(monkeypatch, captured)

    key, uid = await _register(client)
    box = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret("http://rozvrh-box:11434/v1", "qwen", WINDOW),
        name="rozvrh-box",
    )
    agent_id = await _agent(client, key, AGENT_NAME)
    session_id = "agent-curate-77777777-20261006120000"

    await sprite_agent_service.run_chat(
        uid,
        "Owner",
        uid,
        session_id,
        "curate the wiki",
        model_provider="local",
        model_id="qwen",
        credential_id=box,
        agent_name=AGENT_NAME,
        agent_id=agent_id,
    )

    run = _harness_exec(captured)
    env = run["env"]
    assert env[AGENT_SESSION_ENV] == session_id
    assert env[AGENT_ID_ENV] == agent_id
    assert ("STASH_AGENT_" + "NAME") not in env, "the name never goes on the wire"
    # A run on its own box is still a run: the identity travels next to whatever
    # the box's own endpoint plumbing needs.
    assert "ANTHROPIC_API_KEY" not in env


@pytest.mark.asyncio
async def test_the_run_identity_is_never_a_redaction_candidate(sprite_exec, monkeypatch):
    """The turn redacts what it thinks are secrets out of harness output by
    replacing every value in the environment whose NAME contains KEY. A session id
    that got redacted out of a transcript would be worse than useless -- it would
    name a run that the Sessions list then cannot find -- so the two names must stay
    out of that net by construction."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    captured: list[dict] = []
    await _capturing_exec(monkeypatch, captured)
    agent_id = str(uuid.uuid4())
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-secret",
        AGENT_SESSION_ENV: CURATOR_RUN,
        AGENT_ID_ENV: agent_id,
    }
    line = f"stash page write --session {CURATOR_RUN} as agent {agent_id}"

    assert sprite_agent_service._redact(line, env) == line
