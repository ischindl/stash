"""Multiple local model endpoints per user: append, name, pin, resolve.

The founder runs several local model servers at once — a workstation box, a
rack box, a tunnelled laptop — and each connect used to overwrite the previous
one because the credential table keyed on (user_id, provider). Endpoints are
boxes now: independent named rows that APPEND, addressed by a stable id. An
agent's `credential_id` names WHICH box to dial; with no pin, 'local' means the
oldest connected box — exactly the single row a one-endpoint account already
had, which is why today's behaviour is byte-preserved for him.

These are the service-layer tests. The HTTP shape (probe-first connect, the
endpoint listing, the delete reference guard) is pinned in the API tests.
"""

import json

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from backend.config import settings
from backend.services import agent_auth

from .test_curator import _register

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
async def test_endpoint_listing_never_carries_the_key(client: AsyncClient):
    """GET-shape entries: id, name, base_url, models — the api key stays
    inside the encrypted doc, reachable only by a turn dialing the box."""
    _key, uid = await _register(client)
    await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(BOX_ONE, "llama", SECRET),
        name="box-one",
    )
    entries = await agent_auth.list_local_endpoints(uid)
    assert entries == [
        {"id": entries[0]["id"], "name": "box-one", "base_url": BOX_ONE, "models": ["llama"]}
    ]
    assert SECRET not in json.dumps(entries, default=str)
    assert SECRET not in json.dumps(await agent_auth.get_local_endpoint(uid, entries[0]["id"]), default=str)
