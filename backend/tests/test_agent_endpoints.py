"""Multiple local model endpoints per user: append, name, pin, resolve.

The founder runs several local model servers at once — a workstation box, a
rack box, a tunnelled laptop — and each connect used to overwrite the previous
one because the credential table keyed on (user_id, provider). Endpoints are
boxes now: independent named rows that APPEND, addressed by a stable id. An
agent's `credential_id` names WHICH box to dial; with no pin, 'local' means the
oldest connected box — exactly the single row a one-endpoint account already
had, which is why today's behaviour is byte-preserved for him.

These are the service-layer tests. The API tests below pin the HTTP shape:
the probe-first connect, the endpoint listing (live-probed models, never a
key), the delete reference guard, and the workspace mirror.
"""

import asyncio
import json
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from backend.config import settings
from backend.services import agent_auth, agent_service, sprite_agent_service

from .test_curator import _register
from .test_developer_platform import _developer
from .test_permissions import _auth

BOX_ONE = "http://box-one:11434/v1"
BOX_TWO = "https://ollama.lan:11434/v1"
SECRET = "sk-local-abcdef"


@pytest.fixture(autouse=True)
def _fernet(monkeypatch):
    """Credential storage is Fernet-encrypted; CI has no INTEGRATIONS_ENCRYPTION_KEY."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_endpoint_name_is_the_box_host():
    """The host is the part of the URL that says which machine he means."""
    assert agent_auth.endpoint_name(BOX_ONE) == "box-one"
    assert agent_auth.endpoint_name(BOX_TWO) == "ollama.lan"
    with pytest.raises(ValueError):
        agent_auth.endpoint_name("not-a-url")


@pytest.mark.asyncio
async def test_connecting_a_second_box_appends(client: AsyncClient, _db_pool):
    """Two POSTs are two boxes, not one box retyped: distinct ids, distinct
    hostname-derived names, both rows present."""
    _key, uid = await _register(client)
    first = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_ONE, "llama"),
        name=agent_auth.endpoint_name(BOX_ONE),
    )
    second = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_TWO, "qwen", SECRET),
        name=agent_auth.endpoint_name(BOX_TWO),
    )
    assert first != second

    rows = await _db_pool.fetch(
        "SELECT id, name, kind FROM user_agent_credentials "
        "WHERE user_id = $1 AND provider = 'local' ORDER BY created_at, id",
        uid,
    )
    assert [(r["name"], r["kind"]) for r in rows] == [
        ("box-one", "endpoint"),
        ("ollama.lan", "endpoint"),
    ]


@pytest.mark.asyncio
async def test_key_provider_reconnect_still_overwrites(client: AsyncClient, _db_pool):
    """The append ruling is about boxes. An Anthropic key stays one row: a
    reconnect replaces the secret in place, exactly as before."""
    _key, uid = await _register(client)
    first = await agent_auth.store_credential(
        uid, "anthropic", "api_key", "sk-ant-one", name="anthropic"
    )
    again = await agent_auth.store_credential(
        uid, "anthropic", "api_key", "sk-ant-two", name="anthropic"
    )
    assert first == again
    count = await _db_pool.fetchval(
        "SELECT count(*) FROM user_agent_credentials WHERE user_id = $1 AND provider = 'anthropic'",
        uid,
    )
    assert count == 1
    cred = await agent_auth._get_credential(uid, "anthropic")
    assert cred["secret"] == "sk-ant-two"


@pytest.mark.asyncio
async def test_local_default_is_the_oldest_box(client: AsyncClient, _db_pool):
    """No pin means 'the oldest connected local endpoint' — by created_at, not
    by insertion or id order: aged the second box to be older than the first
    and the resolution must follow the age."""
    _key, uid = await _register(client)
    one = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    two = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_TWO, "qwen"), name="box-two"
    )
    cred = await agent_auth._get_credential(uid, "local")
    assert cred["id"] == one  # first connected is the default

    # Age the second box into the past — resolution must follow created_at,
    # proving it is not storage order (a Seq Scan would keep picking box-one).
    await _db_pool.execute(
        "UPDATE user_agent_credentials SET created_at = created_at - interval '1 day' WHERE id = $1",
        two,
    )
    cred = await agent_auth._get_credential(uid, "local")
    assert cred["id"] == two

    # No provider at all: the oldest credential of any kind — this is the
    # workspace curator's resolution, shared with every NULL-model agent.
    await agent_auth.store_credential(uid, "anthropic", "api_key", "sk-ant", name="anthropic")
    cred = await agent_auth._get_credential(uid)
    assert cred["id"] == two  # box-two is still the oldest row overall


@pytest.mark.asyncio
async def test_resolve_pins_the_named_box_and_picks_the_model_within_it(
    client: AsyncClient, monkeypatch
):
    """`credential_id` answers WHICH box; `model_id` answers which model on it.
    Pinning the newer box must not silently ride the oldest one."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    _key, uid = await _register(client)
    await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    pinned = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_TWO, "qwen"), name="box-two"
    )

    auth = await agent_auth.resolve(uid, "local", credential_id=pinned, model_id="qwen3:32b")
    assert auth.endpoint == BOX_TWO
    assert auth.model == "qwen3:32b"

    # Unpinned 'local' still lands on the oldest box.
    auth = await agent_auth.resolve(uid, "local")
    assert auth.endpoint == BOX_ONE


@pytest.mark.asyncio
async def test_resolve_fails_loud_on_a_pin_that_is_not_ones_own_box(
    client: AsyncClient, monkeypatch
):
    """A pin is a reference; a broken reference is a bug to fix, not a user
    action waiting to happen — RuntimeError, never a silent fallback to some
    other box, and never a stranger's box."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    _key, uid = await _register(client)
    other_key, other_uid = await _register(client)
    foreign = await agent_auth.store_credential(
        other_uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_ONE, "llama"),
        name="box-one",
    )
    with pytest.raises(RuntimeError, match="not a credential of this user"):
        await agent_auth.resolve(uid, "local", credential_id=foreign)

    # A key provider's row is addressable by id too — pinning it is nonsense.
    key_id = await agent_auth.store_credential(
        uid, "anthropic", "api_key", "sk-ant", name="anthropic"
    )
    with pytest.raises(RuntimeError, match="not a local endpoint"):
        await agent_auth.resolve(uid, None, credential_id=key_id)

    # A pin cannot be dressed as a different provider.
    pinned = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_TWO, "qwen"), name="box-two"
    )
    with pytest.raises(ValueError, match="cannot run as anthropic"):
        await agent_auth.resolve(uid, "anthropic", credential_id=pinned)


@pytest.mark.asyncio
async def test_list_connected_says_local_once(client: AsyncClient):
    """Three boxes are still one connected provider — 'local' repeated is a
    lie about what the user has."""
    _key, uid = await _register(client)
    for base_url in (BOX_ONE, BOX_TWO, "http://box-three:8000/v1"):
        await agent_auth.store_credential(
            uid,
            "local",
            "endpoint",
            agent_auth.local_endpoint_secret(base_url, "llama"),
            name=agent_auth.endpoint_name(base_url),
        )
    await agent_auth.store_credential(uid, "anthropic", "api_key", "sk-ant", name="anthropic")
    assert sorted(await agent_auth.list_connected(uid)) == ["anthropic", "local"]


@pytest.mark.asyncio
async def test_disconnect_by_provider_name_refuses_local(client: AsyncClient, _db_pool):
    """With several boxes, a provider name no longer names a row to remove;
    guessing the oldest would delete a box he did not point at."""
    _key, uid = await _register(client)
    pinned = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    with pytest.raises(ValueError, match="disconnected by id"):
        await agent_auth.delete_credential(uid, "local")
    # Nothing was deleted on the way to the refusal.
    assert await agent_auth.get_local_endpoint(uid, pinned) is not None

    # Key providers keep the old by-name disconnect.
    await agent_auth.store_credential(uid, "anthropic", "api_key", "sk-ant", name="anthropic")
    await agent_auth.delete_credential(uid, "anthropic")
    assert await agent_auth._get_credential(uid, "anthropic") is None


@pytest.mark.asyncio
async def test_delete_endpoint_takes_exactly_one_box(client: AsyncClient, _db_pool):
    """Disconnect is by id and scoped to 'local': the surviving box keeps its
    own secret; an id that names somebody else's row deletes nothing."""
    _key, uid = await _register(client)
    one = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    two = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_TWO, "qwen", SECRET),
        name="box-two",
    )
    await agent_auth.delete_endpoint(uid, one)
    assert await agent_auth.get_local_endpoint(uid, one) is None
    surviving = await agent_auth.get_local_endpoint(uid, two)
    assert surviving["base_url"] == BOX_TWO

    # The id is owner-scoped: another user's delete cannot reach this row.
    _other_key, other_uid = await _register(client)
    await agent_auth.delete_endpoint(other_uid, two)
    assert await agent_auth.get_local_endpoint(uid, two) is not None


@pytest.mark.asyncio
async def test_the_pin_covers_every_turn_of_a_run(client: AsyncClient, monkeypatch):
    """A run has one endpoint, not one endpoint per phase: a writer-shaped
    call and a digest-shaped call naming the same pin dial the same box, and
    only the model differs between them."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    _key, uid = await _register(client)
    await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    pinned = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_TWO, "qwen"), name="box-two"
    )

    writer = await agent_auth.resolve(uid, "local", credential_id=pinned, model_id="qwen3:32b")
    digest = await agent_auth.resolve(uid, "local", credential_id=pinned, model_id="qwen3:4b")
    assert writer.endpoint == digest.endpoint == BOX_TWO
    assert writer.model == "qwen3:32b"
    assert digest.model == "qwen3:4b"


@pytest.mark.asyncio
async def test_local_exec_mode_honors_the_pin(client: AsyncClient, monkeypatch):
    """The AGENT_EXEC_MODE=local branch dials the pinned box too — the pin is
    not a sprite-only affordance."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")
    _key, uid = await _register(client)
    await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    pinned = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_TWO, "qwen"), name="box-two"
    )
    auth = await agent_auth.resolve(uid, "local", credential_id=pinned)
    assert auth.endpoint == BOX_TWO
    # The turn runs against the simulated box's home, never this machine's.
    assert auth.env["HOME"].endswith(".stash-dev-sprite")


@pytest.mark.asyncio
async def test_models_json_override_targets_the_default_box(client: AsyncClient, _db_pool):
    """The models.json override rides on the DEFAULT (oldest) box's row: a
    provider='local' predicate would stamp one box's override onto every box,
    and a turn dialing a pinned box must never inherit another box's pi config."""
    _key, uid = await _register(client)
    one = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_ONE, "llama"), name="box-one"
    )
    two = await agent_auth.store_credential(
        uid, "local", "endpoint", agent_auth.local_endpoint_secret(BOX_TWO, "qwen"), name="box-two"
    )
    models = json.dumps({"providers": {"local": {"id": "llama"}}})
    await agent_auth.save_local_models_json(uid, models)

    enc = {
        r["id"]: r["models_json_enc"]
        for r in await _db_pool.fetch(
            "SELECT id, models_json_enc FROM user_agent_credentials WHERE user_id = $1", uid
        )
    }
    assert enc[one] is not None  # default row stamped
    assert enc[two] is None  # the other box untouched

    eff = await agent_auth.get_local_models_json(uid)
    assert eff["stored"] is True

    await agent_auth.reset_local_models_json(uid)
    enc = {
        r["id"]: r["models_json_enc"]
        for r in await _db_pool.fetch(
            "SELECT id, models_json_enc FROM user_agent_credentials WHERE user_id = $1", uid
        )
    }
    assert enc[one] is None and enc[two] is None


@pytest.mark.asyncio
async def test_endpoint_listing_never_carries_the_key(client: AsyncClient, monkeypatch):
    """GET-shape entries: id, name, base_url, and models probed LIVE from the
    box — the api key stays inside the encrypted doc, reachable only by a turn
    dialing the box. A box that is down reports models: [] plus a probe_error
    on its own entry instead of blanking the listing."""
    _key, uid = await _register(client)
    await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_ONE, "llama", SECRET),
        name="box-one",
    )

    async def probe(base_url, api_key):
        return {"ok": True, "http_status": 200, "models": ["llama", "llama:8b"]}

    monkeypatch.setattr(agent_auth, "probe_local_endpoint", probe)
    entries = await agent_auth.list_local_endpoints(uid)
    assert entries == [
        {
            "id": entries[0]["id"],
            "name": "box-one",
            "base_url": BOX_ONE,
            "models": ["llama", "llama:8b"],
        }
    ]
    assert SECRET not in json.dumps(entries, default=str)
    assert SECRET not in json.dumps(
        await agent_auth.get_local_endpoint(uid, entries[0]["id"]), default=str
    )

    async def down(base_url, api_key):
        return {"ok": False, "http_status": None, "error_detail": "connection refused"}

    monkeypatch.setattr(agent_auth, "probe_local_endpoint", down)
    (entry,) = await agent_auth.list_local_endpoints(uid)
    assert entry["models"] == []
    assert entry["probe_error"] == "connection refused"


@pytest.mark.asyncio
async def test_endpoint_listing_probes_all_boxes_at_once(client: AsyncClient, monkeypatch):
    """The STAS-203 Settings view cannot pay per-box probe timeouts in series:
    with N unreachable boxes (VPN off, box powered down) the listing must cost
    the SLOWEST box, not the SUM — so every probe is in flight at the same
    instant, each entry still owning its own probe_error in oldest-first order.
    """
    _key, uid = await _register(client)
    for i in range(3):
        await agent_auth.store_credential(
            uid,
            "local",
            "endpoint",
            agent_auth.local_endpoint_secret(f"{BOX_ONE}-{i}", "llama", SECRET),
            name=f"box-{i}",
        )

    in_flight = 0
    peak = 0

    async def slow_down_probe(base_url, api_key):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return {"ok": False, "http_status": None, "error_detail": f"down: {base_url}"}

    monkeypatch.setattr(agent_auth, "probe_local_endpoint", slow_down_probe)
    entries = await agent_auth.list_local_endpoints(uid)
    assert peak == 3
    assert [entry["probe_error"] for entry in entries] == [f"down: {BOX_ONE}-{i}" for i in range(3)]


# --- The HTTP surface: probe-first connect, listing, delete guard, mirror ---


def _probe_ok(*models: str):
    async def probe(base_url, api_key):
        return {"ok": True, "http_status": 200, "models": list(models)}

    return probe


def _scope_headers(api_key: str, scope_user_id: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "X-Stash-Scope": scope_user_id}


@pytest.mark.asyncio
async def test_http_connect_appends_and_lists_both_boxes(client: AsyncClient, monkeypatch):
    """Two POSTs are two boxes in the Settings list, each with its own id and
    hostname-derived name and the models the probe saw. The listing entries
    never carry the api_key — it stays where only a dialing turn can read it."""
    monkeypatch.setattr(agent_auth, "probe_local_endpoint", _probe_ok("llama", "mistral"))
    key, _uid = await _register(client)

    one = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": BOX_ONE, "model": "llama"},
        headers=_auth(key),
    )
    two = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": BOX_TWO, "model": "qwen", "api_key": SECRET},
        headers=_auth(key),
    )
    assert one.status_code == 200 and two.status_code == 200, (one.text, two.text)
    assert one.json()["id"] != two.json()["id"]

    body = (await client.get("/api/v1/me/agent-credentials", headers=_auth(key))).json()
    assert body["connected"] == ["local"]
    assert [(e["name"], e["base_url"]) for e in body["endpoints"]] == [
        ("box-one", BOX_ONE),
        ("ollama.lan", BOX_TWO),
    ]
    assert all(e["models"] == ["llama", "mistral"] for e in body["endpoints"])
    assert SECRET not in json.dumps(body["endpoints"], default=str)


@pytest.mark.asyncio
async def test_http_connect_probes_first_and_stores_nothing(client: AsyncClient, monkeypatch):
    """A dead box never enters the list: the 400 carries the endpoint's own
    words (LiteLLM's token_not_found_in_db is the reason he would recognize),
    and zero rows were written on the way to the refusal."""

    async def down(base_url, api_key):
        return {"ok": False, "http_status": 401, "error_detail": "token_not_found_in_db"}

    monkeypatch.setattr(agent_auth, "probe_local_endpoint", down)
    key, uid = await _register(client)
    r = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": BOX_ONE, "model": "llama"},
        headers=_auth(key),
    )
    assert r.status_code == 400
    assert "token_not_found_in_db" in r.text
    body = (await client.get("/api/v1/me/agent-credentials", headers=_auth(key))).json()
    assert body["endpoints"] == [] and body["connected"] == []
    # Shape validation still runs BEFORE the dial — a malformed URL never gets
    # to the network.
    r = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": "not-a-url", "model": "llama"},
        headers=_auth(key),
    )
    assert r.status_code == 400
    assert "token_not_found" not in r.text


@pytest.mark.asyncio
async def test_http_delete_of_a_pinned_endpoint_409s_listing_its_agent(
    client: AsyncClient, monkeypatch, _db_pool
):
    """The founder's curator pinned to a box, and the box pulled out from
    under it: refused, with the curator named — then, once the pin is gone,
    the disconnect succeeds."""
    monkeypatch.setattr(agent_auth, "probe_local_endpoint", _probe_ok("llama"))
    key, uid = await _register(client)
    r = await client.post(
        "/api/v1/me/agent-credentials",
        json={"provider": "local", "base_url": BOX_ONE, "model": "llama"},
        headers=_auth(key),
    )
    endpoint_id = r.json()["id"]

    curator = await agent_service.get_or_create_curator(uid)
    await _db_pool.execute(
        "UPDATE agents SET credential_id = $2 WHERE id = $1",
        UUID(curator["id"]),
        UUID(endpoint_id),
    )

    r = await client.delete(
        f"/api/v1/me/agent-credentials/endpoints/{endpoint_id}", headers=_auth(key)
    )
    assert r.status_code == 409, r.text
    listing = r.json()["detail"]["agents"]
    assert listing == [{"id": curator["id"], "name": curator["name"]}]

    # Nothing deleted on the way to the refusal.
    body = (await client.get("/api/v1/me/agent-credentials", headers=_auth(key))).json()
    assert [e["id"] for e in body["endpoints"]] == [endpoint_id]

    await _db_pool.execute(
        "UPDATE agents SET credential_id = NULL WHERE id = $1", UUID(curator["id"])
    )
    r = await client.delete(
        f"/api/v1/me/agent-credentials/endpoints/{endpoint_id}", headers=_auth(key)
    )
    assert r.status_code == 200, r.text
    body = (await client.get("/api/v1/me/agent-credentials", headers=_auth(key))).json()
    assert body["endpoints"] == []


@pytest.mark.asyncio
async def test_http_delete_local_by_provider_name_points_at_the_id_route(client: AsyncClient):
    """The old by-name disconnect cannot name one row anymore; the 400 says so
    and shows the way instead of guessing which box to delete."""
    key, _uid = await _register(client)
    r = await client.delete("/api/v1/me/agent-credentials/local", headers=_auth(key))
    assert r.status_code == 400
    assert "/api/v1/me/agent-credentials/endpoints/" in r.text


@pytest.mark.asyncio
async def test_workspace_credentials_route_mirrors_the_endpoint_api(
    client: AsyncClient, monkeypatch
):
    """The console's Local model section gets the same shapes as the personal
    route: probe-first append with an id, a listed entry with probed models —
    and here not even the personal variant's own-doc key exists, so the whole
    body must be secret-free."""
    monkeypatch.setattr(agent_auth, "probe_local_endpoint", _probe_ok("llama", "qwen"))
    api_key, _dev, workspace = await _developer(client)
    scope = workspace["scope_user_id"]

    r = await client.post(
        "/api/v1/me/developer/agent-credentials",
        json={"base_url": BOX_ONE, "model": "llama", "api_key": SECRET},
        headers=_scope_headers(api_key, scope),
    )
    assert r.status_code == 200, r.text
    endpoint_id = r.json()["id"]

    r = await client.get(
        "/api/v1/me/developer/agent-credentials", headers=_scope_headers(api_key, scope)
    )
    body = r.json()
    assert body["connected"] == ["local"]
    assert [(entry["name"], entry["models"]) for entry in body["endpoints"]] == [
        ("box-one", ["llama", "qwen"])
    ]
    assert SECRET not in r.text

    r = await client.delete(
        f"/api/v1/me/developer/agent-credentials/endpoints/{endpoint_id}",
        headers=_scope_headers(api_key, scope),
    )
    assert r.status_code == 200, r.text
    body = (
        await client.get(
            "/api/v1/me/developer/agent-credentials", headers=_scope_headers(api_key, scope)
        )
    ).json()
    assert body["endpoints"] == []


@pytest.mark.asyncio
async def test_workspace_by_name_disconnect_is_refused_with_the_pointer(
    client: AsyncClient, monkeypatch
):
    """The console gets the personal route's loud refusal too, not a bare 405:
    the name 'local' does not name one box to delete."""
    monkeypatch.setattr(agent_auth, "probe_local_endpoint", _probe_ok("llama"))
    api_key, _dev, workspace = await _developer(client)
    scope = workspace["scope_user_id"]
    await client.post(
        "/api/v1/me/developer/agent-credentials",
        json={"base_url": BOX_ONE, "model": "llama"},
        headers=_scope_headers(api_key, scope),
    )
    r = await client.delete(
        "/api/v1/me/developer/agent-credentials/local", headers=_scope_headers(api_key, scope)
    )
    assert r.status_code == 400
    assert "/api/v1/me/developer/agent-credentials/endpoints/" in r.text
    # The box survived the refused attempt.
    body = (
        await client.get(
            "/api/v1/me/developer/agent-credentials", headers=_scope_headers(api_key, scope)
        )
    ).json()
    assert [entry["base_url"] for entry in body["endpoints"]] == [BOX_ONE]


# --- Run plumbing: the row's pin and model reach every turn's resolve ---


async def _two_boxes(uid: UUID) -> tuple[UUID, UUID]:
    one = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_ONE, "llama", None),
        name="box-one",
    )
    two = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_TWO, "qwen", None),
        name="box-two",
    )
    return one, two


async def _pin_curator(_db_pool, uid: UUID, **fields) -> dict:
    sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(fields))
    row = await _db_pool.fetchrow(
        f"UPDATE agents SET {sets} WHERE id = (SELECT id FROM agents WHERE user_id = $1 "
        "AND is_curator AND curator_folder_id IS NULL LIMIT 1) RETURNING id",
        uid,
        *fields.values(),
    )
    return await agent_service.get_agent_by_id(row["id"])


def _spy_resolve(monkeypatch) -> list[dict]:
    """Capture resolve args for the run's turns and abort before any turn runs.

    Returns (calls, undo): undo restores the real resolver for the follow-up
    assertion that the REAL resolve dials the box the row names."""
    calls: list[dict] = []
    real = agent_auth.resolve

    async def spy(user_id, prefer_provider=None, model_id=None, credential_id=None):
        calls.append({"prefer": prefer_provider, "model_id": model_id, "cred": credential_id})
        raise agent_auth.NeedsAuth

    monkeypatch.setattr(agent_auth, "resolve", spy)

    def undo():
        monkeypatch.setattr(agent_auth, "resolve", real)

    return calls, undo


@pytest.mark.asyncio
async def test_a_pinned_curators_run_resolves_through_the_pin(
    client: AsyncClient, monkeypatch, _db_pool
):
    """The founder's curator pinned to the NEWER of two boxes: the scheduled
    run hands the pin to resolve — and the real resolver dials exactly that
    box, proving the wiring reaches RunAuth.endpoint, not just the kwargs."""
    _key, uid = await _register(client)
    _one, two = await _two_boxes(uid)
    agent = await _pin_curator(
        _db_pool, uid, model_provider="local", model_id="qwen", credential_id=two
    )

    calls, undo = _spy_resolve(monkeypatch)
    with pytest.raises(sprite_agent_service.NeedsAuth):
        await sprite_agent_service.run_scheduled(agent, "202601011200")
    assert calls == [{"prefer": "local", "model_id": "qwen", "cred": two}]

    undo()
    auth = await agent_auth.resolve(uid, "local", model_id="qwen", credential_id=two)
    assert auth.endpoint == BOX_TWO


@pytest.mark.asyncio
async def test_an_unpinned_curator_run_still_dials_the_oldest_box(
    client: AsyncClient, monkeypatch, _db_pool
):
    """Default-model equivalence survives the plumbing: an unpinned curator
    passes no pin, and the real resolver keeps the oldest-box answer."""
    _key, uid = await _register(client)
    one, _two = await _two_boxes(uid)
    agent = await _pin_curator(_db_pool, uid, model_provider="local", credential_id=None)

    calls, undo = _spy_resolve(monkeypatch)
    with pytest.raises(sprite_agent_service.NeedsAuth):
        await sprite_agent_service.run_scheduled(agent, "202601011200")
    assert calls == [{"prefer": "local", "model_id": None, "cred": None}]

    undo()
    auth = await agent_auth.resolve(uid, "local")
    assert auth.endpoint == BOX_ONE


@pytest.mark.asyncio
async def test_the_digest_phase_carries_the_pin_only_for_a_local_box(
    client: AsyncClient, monkeypatch, _db_pool
):
    """Curator pins box B, digest on a key provider: the digest turn must NOT
    inherit the pin (it would fail loud as a provider mismatch — correctly,
    but it's the wrong pairing, not a typo), while a local digest DOES inherit
    the box. Only prefer_provider/model_id swap between the phases."""
    _key, uid = await _register(client)
    _one, two = await _two_boxes(uid)

    agent = await _pin_curator(
        _db_pool,
        uid,
        model_provider="local",
        credential_id=two,
        digest_provider="anthropic",
        digest_model_id="haiku",
    )
    calls, _undo = _spy_resolve(monkeypatch)
    with pytest.raises(sprite_agent_service.NeedsAuth):
        await sprite_agent_service.run_scheduled(agent, "202601011200")
    assert calls == [{"prefer": "anthropic", "model_id": "haiku", "cred": None}]

    agent = await _pin_curator(
        _db_pool,
        uid,
        model_provider="local",
        credential_id=two,
        digest_provider="local",
        digest_model_id="qwen-mini",
    )
    calls, _undo = _spy_resolve(monkeypatch)
    with pytest.raises(sprite_agent_service.NeedsAuth):
        await sprite_agent_service.run_scheduled(agent, "202601011200")
    assert calls == [{"prefer": "local", "model_id": "qwen-mini", "cred": two}]


@pytest.mark.asyncio
async def test_a_pinned_two_phase_curator_dials_one_box_for_both_turns(
    client: AsyncClient, monkeypatch, _db_pool
):
    """Both turns of one run resolve against the SAME endpoint row.

    Named boundary: a two-phase curator's run is `run_scheduled` handing both
    turns to `run_chat`, and `run_chat` is what calls the resolver — so the
    stubbed turn captures each phase's selection and the REAL resolver then
    answers it. The pin names the newer box: the digest turn must not ride the
    oldest one, and the two phases differ only in which model on that box they
    ask for."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    _key, uid = await _register(client)
    _one, pinned = await _two_boxes(uid)
    agent = await _pin_curator(
        _db_pool,
        uid,
        model_provider="local",
        model_id="qwen",
        credential_id=pinned,
        digest_provider="local",
        digest_model_id="qwen-fast",
    )

    turns: list[dict] = []

    async def fake_run_chat(user_id, owner_name, agent_uid, session_id, message, **fields):
        turns.append(fields)
        return "EXTRACT: two sessions changed" if len(turns) == 1 else "LOG: wiki written"

    monkeypatch.setattr(sprite_agent_service, "run_chat", fake_run_chat)
    assert await sprite_agent_service.run_scheduled(agent, "202601021200") == "LOG: wiki written"
    assert len(turns) == 2

    auths = [
        await agent_auth.resolve(
            uid,
            fields["model_provider"],
            credential_id=fields["credential_id"],
            model_id=fields["model_id"],
        )
        for fields in turns
    ]
    assert [auth.endpoint for auth in auths] == [BOX_TWO, BOX_TWO]
    assert [auth.model for auth in auths] == ["qwen-fast", "qwen"]
    assert BOX_ONE not in {auth.endpoint for auth in auths}
