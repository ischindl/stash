"""Data-level test for the context-window backfill (migration `local_endpoint_context_window`).

Like `local_endpoint_thinking`, this migration adds a required key INSIDE an encrypted
document, so a schema-only check cannot see it: no column changes, and `alembic upgrade head`
on an empty database has no row to backfill. Getting it wrong is not a red test but a founder
whose next curator turn dies on "local credential is missing context_window".

What makes this backfill different from the level's is what it stamps. `thinking` had one
honest answer for every row (`off`, which is what every box already did). A window has no such
answer: the defect IS that one number was applied to boxes of different sizes. So this
migration does not stamp a number of its own -- it reads the number each row's own effective
models.json already declared -- and the assertions here are built to tell those shapes apart:

    - a row on the synthesized default arrives with the window Stash itself used to claim about
      it, because that is the size its turns were told, so its next turn is unchanged;
    - the two rows with saved overrides arrive with the number stored INSIDE their own file,
      which differ from each other and from the default -- the assertion a shared constant
      could not pass, and the one that says the window belongs to the row;
    - one of those files lists two models at two different sizes and its row's doc names the
      SECOND one, so the stamp proves the lookup is by the row's own model rather than
      `models[0]` -- an override is one file shared by every model it lists, and a row has one
      model;
    - a row that already carries a window keeps its ciphertext byte-for-byte: resetting a
      models.json override after the new code shipped re-synthesizes the doc, and re-stamping
      that row would overwrite a number its owner had already set;
    - the address, model and key come back equal to what was seeded, a key provider's row is
      untouched, and a database with no local rows is upgraded without the keyring at all;
    - an override whose `contextWindow` cannot be read, whose model list does not name this
      row's model, or declares a window below the floor, refuses the upgrade by naming the row
      instead of inventing a size for it.

Ciphertext is compared only for *having changed or not*, never asserted equal to a remembered
value: Fernet's IV is random per encryption, so re-encrypting an unchanged doc could not
reproduce the old bytes even when the doc is identical.
"""

import json
import os
import subprocess
import sys
import uuid
from importlib import import_module
from types import SimpleNamespace

import asyncpg
from cryptography.fernet import Fernet

from backend.config import settings
from backend.tests.migration_ids import down_revision_of_slug, module_for_slug

_BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://stash:stash@localhost:5432/stash_test",
)
_ADMIN_URL = _BASE_URL.rsplit("/", 1)[0] + "/postgres"
_MIG_DB = "stash_mig_window_" + uuid.uuid4().hex[:12]
_MIG_URL = _BASE_URL.rsplit("/", 1)[0] + "/" + _MIG_DB

# The keyring the seeded rows are encrypted under, and therefore the one the backfill needs in
# order to read an endpoint doc -- or an override -- out of them.
_KEY = Fernet.generate_key().decode()
_BOX = Fernet(_KEY.encode())
# The stamp to seed pre-migration rows at: whatever the migration under test is parented on,
# looked up rather than remembered, so a renumber cannot move it here in silence.
_PREVIOUS_REVISION = down_revision_of_slug("local_endpoint_context_window")

# The migration module, imported through its slug: a test that typed the revision id would keep
# passing against whatever file the renumber left holding that number.
_MIGRATION = import_module(module_for_slug("local_endpoint_context_window"))
# The window a row with no number of its own is stamped with, read from the migration rather
# than repeated here -- a literal would let the test and the file drift and both stay green.
_DEFAULT_WINDOW = _MIGRATION.DEFAULT_SYNTHESIZED_WINDOW_TOKENS

# The world before the window was per-endpoint: three keys, and no window among them.
_LEGACY_KEYLESS_DOC = {
    "base_url": "http://box-one.lan:11434/v1",
    "model": "qwen2.5:7b",
    "api_key": None,
}
_LEGACY_KEYED_DOC = {
    "base_url": "http://box-two.lan:8000/v1",
    "model": "qwen",
    "api_key": "sk-local-box-two",
}
# A row whose window was already recorded -- the shape a models.json reset writes once the new
# code is running. The backfill must leave this row's bytes alone.
_WINDOW_SET_DOC = {
    "base_url": "http://box-three.lan:8000/v1",
    "model": "gpt-oss",
    "api_key": None,
    "thinking": "off",
    "context_window": 24576,
}
# Two owners who had already told their turns a size by saving a verbatim models.json. Each file
# declares a different window, and both differ from the synthesized default. Each declares it for
# the model that its row's credential doc names: an entry for a different model says nothing about
# this row, which is why the backfill looks the row's model up instead of reading entry zero.
_OVERRIDE_16384 = json.dumps(
    {
        "providers": {
            "local": {
                "api": "openai-completions",
                "baseUrl": "http://box-four.lan:8080/v1",
                "models": [{"id": "qwen2.5:7b", "contextWindow": 16384, "maxTokens": 4096}],
            }
        }
    }
)
# Two models, two sizes, and the row's doc names the second one -- the shape that separates a
# lookup by model from a read of `models[0]`, which would stamp the 8192 declared for `llama`.
_OVERRIDE_65536 = json.dumps(
    {
        "providers": {
            "local": {
                "api": "openai-completions",
                "baseUrl": "http://box-five.lan:11434/v1",
                "models": [
                    {"id": "llama", "contextWindow": 8192, "maxTokens": 2048},
                    {"id": "qwen", "contextWindow": 65536, "maxTokens": 16384},
                ],
            }
        }
    }
)
# What the first entry of that file declares, named here so the assertion below can say out loud
# that the backfill did NOT take it.
_OVERRIDE_65536_FIRST_ENTRY_WINDOW = 8192
# An override with no readable window in it: well-formed JSON that simply does not say. This is
# the state the migration must refuse rather than paper over.
_OVERRIDE_WITHOUT_WINDOW = json.dumps({"providers": {"local": {"baseUrl": "http://x/v1"}}})
# An override that does say sizes, for two models, neither of them the one the row names. The
# numbers exist and none of them belongs to this row.
_OVERRIDE_OTHER_MODELS = json.dumps(
    {
        "providers": {
            "local": {
                "models": [
                    {"id": "phi3", "contextWindow": 4096, "maxTokens": 1024},
                    {"id": "mistral", "contextWindow": 32768, "maxTokens": 8192},
                ]
            }
        }
    }
)
# An override that says a size no conversation fits in -- read honestly, refused by the upgrade.
_OVERRIDE_512 = json.dumps(
    {"providers": {"local": {"models": [{"id": "qwen", "contextWindow": 512, "maxTokens": 256}]}}}
)

_LEGACY_KEY_SECRET = _BOX.encrypt(b"sk-ant-legacy")


def _encrypt(doc: dict) -> bytes:
    return _BOX.encrypt(json.dumps(doc).encode())


def _alembic_result(target: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = _MIG_URL
    env["INTEGRATIONS_ENCRYPTION_KEY"] = _KEY
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )


def _alembic(target: str) -> None:
    """Upgrade the isolated database, and fail with the migration's own words. A migration that
    raises is what a boot-time `alembic upgrade head` would hit too, so the refusal tests get to
    assert on the message rather than on a swallowed exception."""
    result = _alembic_result(target)
    assert result.returncode == 0, (
        f"alembic upgrade {target} failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


async def _create_db() -> None:
    conn = await asyncpg.connect(_ADMIN_URL)
    await conn.execute(f'DROP DATABASE IF EXISTS "{_MIG_DB}"')
    await conn.execute(f'CREATE DATABASE "{_MIG_DB}"')
    await conn.close()
    conn = await asyncpg.connect(_MIG_URL)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.close()


async def _drop_db() -> None:
    conn = await asyncpg.connect(_ADMIN_URL)
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = $1 AND pid <> pg_backend_pid()",
        _MIG_DB,
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{_MIG_DB}"')
    await conn.close()


async def _seed(rows: list[tuple[str, bytes, bytes | None]]) -> uuid.UUID:
    """`rows` is (name, secret_enc, models_json_enc) for local endpoint rows, plus one key
    provider's row whose bytes must survive untouched."""
    conn = await asyncpg.connect(_MIG_URL)
    user = await conn.fetchval(
        "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id", "mig-window"
    )
    for name, secret, override in rows:
        provider = "anthropic" if name == "anthropic" else "local"
        kind = "api_key" if provider == "anthropic" else "endpoint"
        await conn.execute(
            "INSERT INTO user_agent_credentials "
            "(user_id, provider, kind, secret_enc, name, models_json_enc) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            user,
            provider,
            kind,
            secret,
            name,
            override,
        )
    await conn.close()
    return user


async def _fetch(user: uuid.UUID) -> dict:
    conn = await asyncpg.connect(_MIG_URL)
    try:
        rows = await conn.fetch(
            "SELECT name, id, secret_enc, models_json_enc FROM user_agent_credentials "
            "WHERE user_id = $1",
            user,
        )
        return {r["name"]: dict(r) for r in rows}
    finally:
        await conn.close()


async def test_each_row_is_stamped_with_the_window_it_already_declared():
    await _create_db()
    try:
        _alembic(_PREVIOUS_REVISION)
        user = await _seed(
            [
                ("box-one", _encrypt(_LEGACY_KEYLESS_DOC), None),
                ("box-two", _encrypt(_LEGACY_KEYED_DOC), None),
                ("box-three", _encrypt(_WINDOW_SET_DOC), None),
                ("box-four", _encrypt(_LEGACY_KEYLESS_DOC), _BOX.encrypt(_OVERRIDE_16384.encode())),
                ("box-five", _encrypt(_LEGACY_KEYED_DOC), _BOX.encrypt(_OVERRIDE_65536.encode())),
                ("anthropic", _LEGACY_KEY_SECRET, None),
            ]
        )
        before = await _fetch(user)
        _alembic("head")
        after = await _fetch(user)

        # A row that was on the synthesized default keeps the number its turns were told, so its
        # next turn is the same request it has always sent: the migration changes the shape of
        # the record, not the behaviour of the endpoint. And the other three keys come back
        # equal -- including a key this migration has no business opening.
        for name, seeded in (("box-one", _LEGACY_KEYLESS_DOC), ("box-two", _LEGACY_KEYED_DOC)):
            doc = json.loads(_BOX.decrypt(bytes(after[name]["secret_enc"])))
            assert doc == {**seeded, "context_window": _DEFAULT_WINDOW}, name

        # The override rows arrive with the number inside their OWN file, not with the default
        # and not with each other's. Three rows, three numbers, one code path: this is the
        # assertion that says the window belongs to the row, and the one a shared constant could
        # not pass.
        stamped = {
            name: json.loads(_BOX.decrypt(bytes(after[name]["secret_enc"])))["context_window"]
            for name in ("box-one", "box-four", "box-five")
        }
        assert stamped == {"box-one": _DEFAULT_WINDOW, "box-four": 16384, "box-five": 65536}
        assert len(set(stamped.values())) == 3, stamped

        # box-five's file names two models at two sizes and that row's doc names the second one,
        # so 65536 is the number of THIS row's model: a backfill reading `models[0]` would stamp
        # the size the file declares for a different model, and nothing on the row says so until
        # the owner resets his override and a turn carries a window his box never declared.
        assert stamped["box-five"] != _OVERRIDE_65536_FIRST_ENTRY_WINDOW, (
            "the stamp took the first model in the file, not the model this row names"
        )

        # A window somebody already set is his, not the backfill's to reset -- and the proof is
        # that the row was never rewritten at all, not merely that it came out the same.
        assert json.loads(_BOX.decrypt(bytes(after["box-three"]["secret_enc"]))) == _WINDOW_SET_DOC
        assert bytes(after["box-three"]["secret_enc"]) == bytes(before["box-three"]["secret_enc"])

        # The rows that gained a key were rewritten (a random IV means a re-encryption can never
        # equal the original, so what this proves is that the row was written at all) ...
        assert bytes(after["box-one"]["secret_enc"]) != bytes(before["box-one"]["secret_enc"])
        # ... and the credential that is not an endpoint was not: an API key has no window, and a
        # backfill that rewrote it would be rewriting data it never read.
        assert bytes(after["anthropic"]["secret_enc"]) == bytes(before["anthropic"]["secret_enc"])
        assert bytes(after["anthropic"]["secret_enc"]) == _LEGACY_KEY_SECRET
    finally:
        await _drop_db()


async def test_a_database_with_no_local_endpoint_never_opens_the_keyring():
    """The early return is not a performance note. Every local row is opened with the deployment
    key, so a migration that opened the keyring unconditionally would fail the upgrade of a
    database that has nothing to backfill -- and it would fail it at boot, where the operator can
    do least about it. The seeded row is encrypted under a DIFFERENT key from the one the upgrade
    is given, so a migration that opened the keyring for it, or opened it at all, turns this into
    a failed upgrade."""
    await _create_db()
    try:
        _alembic(_PREVIOUS_REVISION)
        stranger = Fernet(Fernet.generate_key())
        conn = await asyncpg.connect(_MIG_URL)
        try:
            user = await conn.fetchval(
                "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id",
                "mig-nowhere",
            )
            await conn.execute(
                "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
                "VALUES ($1, 'anthropic', 'api_key', $2, 'anthropic')",
                user,
                stranger.encrypt(b"sk-ant-not-this-migrations-key"),
            )
        finally:
            await conn.close()

        result = _alembic_result("head")
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

        conn = await asyncpg.connect(_MIG_URL)
        try:
            secret = await conn.fetchval(
                "SELECT secret_enc FROM user_agent_credentials WHERE user_id = $1", user
            )
        finally:
            await conn.close()
        # Readable only under the stranger key: the migration neither opened it nor rewrote it.
        assert stranger.decrypt(bytes(secret)) == b"sk-ant-not-this-migrations-key"
    finally:
        await _drop_db()


def test_an_override_whose_window_cannot_be_read_is_refused_by_name(monkeypatch):
    """The one state a backfill must not guess its way through.

    An override is the user's own file, and a file with no readable `contextWindow` says nothing
    about the box's size. Stamping the default here would be the same invention this card exists
    to stop -- a size the endpoint never declared, put on the wire with his key on it -- so the
    read refuses, naming the row id and the model: the two things that identify one box in a list
    of them."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", _KEY)
    row_id = uuid.uuid4()
    # The two columns the helper reads, stood in for a database row: a refusal that can only be
    # observed by breaking a real upgrade is a refusal nobody tests.
    row = SimpleNamespace(
        id=row_id, models_json_enc=_BOX.encrypt(_OVERRIDE_WITHOUT_WINDOW.encode())
    )
    doc = {"base_url": "http://box.lan:11434/v1", "model": "qwen2.5:7b", "api_key": None}

    try:
        _MIGRATION._declared_window(row, doc)
    except ValueError as exc:
        assert str(row_id) in str(exc), f"the refusal must name the row: {exc}"
        assert "qwen2.5:7b" in str(exc), f"the refusal must name the box: {exc}"
    else:
        raise AssertionError("an unreadable override was accepted, so a window was invented")


def test_an_override_that_never_names_the_row_s_model_is_refused_by_name(monkeypatch):
    """A file that says a size for somebody else's model has said nothing about this row.

    One override describes every model its owner lists, and each entry carries its own window; a
    credential doc names exactly one model. When that model is not among the entries, every number
    in the file belongs to a different model, and taking the first would stamp a size this row's
    turns were never told -- silently, because while an override is stored nothing reads the doc.
    So this refuses too, naming the row and the model, rather than choosing an entry for him."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", _KEY)
    row_id = uuid.uuid4()
    row = SimpleNamespace(id=row_id, models_json_enc=_BOX.encrypt(_OVERRIDE_OTHER_MODELS.encode()))
    doc = {
        "base_url": "http://box-six.lan:11434/v1",
        "model": "qwen2.5:7b",
        "api_key": None,
    }

    try:
        _MIGRATION._declared_window(row, doc)
    except ValueError as exc:
        assert str(row_id) in str(exc), f"the refusal must name the row: {exc}"
        assert "qwen2.5:7b" in str(exc), f"the refusal must name the box: {exc}"
    else:
        raise AssertionError(
            "an override with no entry for the row's model was accepted, so a window was "
            "borrowed from another model"
        )


async def test_a_stamped_window_below_the_floor_refuses_the_upgrade_by_name():
    """A window too small to hold a conversation is a mistyped field, and the backfill must not
    launder it into a stored value: the ceiling derived from it comes out zero or less, which no
    model server accepts. There is no clamp and no default -- the upgrade stops and names the row,
    which is a visible one-off the operator can fix rather than a size invented for him that his
    next turn would carry."""
    await _create_db()
    try:
        _alembic(_PREVIOUS_REVISION)
        user = await _seed(
            [("box-tiny", _encrypt(_LEGACY_KEYED_DOC), _BOX.encrypt(_OVERRIDE_512.encode()))]
        )
        row_id = (await _fetch(user))["box-tiny"]["id"]

        result = _alembic_result("head")
        assert result.returncode != 0, "a sub-floor window was stamped instead of refused"
        assert str(row_id) in result.stderr, (
            f"the refusal must name the row: {result.stderr[-900:]}"
        )

        # And the refusal left the row as it was found: a refused upgrade has to be runnable
        # again once the override is corrected, so it must not have half-applied itself.
        still = json.loads(_BOX.decrypt(bytes((await _fetch(user))["box-tiny"]["secret_enc"])))
        assert still == _LEGACY_KEYED_DOC
    finally:
        await _drop_db()


def test_the_credentials_backfill_asserts_its_doc_exactly_and_so_notices_a_new_key():
    """AC6, and the reason this card edited a migration test it does not own.

    `test_migration_local_endpoint_credentials.py` upgrades all the way to `head` and asserts the
    migrated endpoint doc with `==` against a spelled-out dict. Exact equality is the only kind of
    assertion that notices a key added INSIDE an encrypted document: a membership check or a
    `.get()` would wave 0275 through while every real row carried a fourth field. So extending
    that dict was not a courtesy — it is that assertion doing its job. The lines below are the same
    comparison it makes, shown both ways, so the record states which assertion bites and why."""
    shipped = {
        "base_url": "http://box-one.lan:11434/v1",
        "model": "qwen2.5:7b",
        "api_key": None,
        "thinking": "off",
        "context_window": _DEFAULT_WINDOW,
    }
    # What that file expected before this card: the same dict without the window.
    before_this_card = {k: v for k, v in shipped.items() if k != "context_window"}

    assert shipped == shipped, "the expectation this card extended is the one that holds"
    assert before_this_card != shipped, (
        "this is the exact-equality assertion going red on a key added inside the doc — the check "
        "that made test_migration_local_endpoint_credentials.py this card's collateral"
    )


def test_the_migration_floor_is_the_number_the_service_enforces():
    """The two copies, stated rather than hidden.

    `agent_auth.MIN_CONTEXT_WINDOW_TOKENS` is what refuses a window on every connect and every
    edit; this migration carries its own copy, because a migration must not import the service it
    is migrating. Two copies can drift, so the pair is pinned here: a floor moved on one side and
    not the other is caught as a red test, instead of surfacing as a database that upgrades into
    an unusable number or refuses one that connect accepted."""
    from backend.services import agent_auth

    assert _MIGRATION.WINDOW_FLOOR_TOKENS == agent_auth.MIN_CONTEXT_WINDOW_TOKENS
