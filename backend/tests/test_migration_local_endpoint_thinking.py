"""Data-level test for the thinking-level backfill (migration `local_endpoint_thinking`).

`local_endpoint_thinking` adds a required key INSIDE an encrypted document, which is
the shape of migration a schema-only check cannot see at all: no column changes, no
table appears, and `alembic upgrade head` on an empty database has no row to
backfill. The consequence of getting it wrong is not a red test but a founder whose
next curator turn dies on "local credential is missing thinking", because the reader
now refuses a doc without the level rather than inventing one.

So this test seeds the pre-migration docs on an isolated database (two local endpoint
rows — one keyless, one keyed — plus a key provider's row, plus a local row whose
owner had already chosen a level), runs the chain, and asserts exactly the four
things that matter:

    - every local doc arrives with `thinking: "off"`, the level Stash used before
      the setting existed, so no endpoint changes behavior when this lands;
    - `base_url`, `model` and `api_key` come back equal to what was seeded — the
      migration touches one key and must not touch the address, the model, or a key;
    - the local ciphertext CHANGED (the doc gained a key, so it was re-encrypted)
      while the key provider's row is byte-for-byte untouched: a backfill scoped by
      `provider = 'local'` must not rewrite a credential it has no business opening;
    - a level a user had already chosen survives, because overwriting a chosen level
      with `off` would silently undo his setting.
"""

import json
import os
import subprocess
import sys
import uuid

import asyncpg
from cryptography.fernet import Fernet

from backend.tests.migration_ids import down_revision_of_slug

_BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://stash:stash@localhost:5432/stash_test",
)
_ADMIN_URL = _BASE_URL.rsplit("/", 1)[0] + "/postgres"
_MIG_DB = "stash_mig_thinking_" + uuid.uuid4().hex[:12]
_MIG_URL = _BASE_URL.rsplit("/", 1)[0] + "/" + _MIG_DB

# The keyring the seeded rows are encrypted under, and therefore the one the
# backfill needs in order to read an endpoint doc out of them.
_KEY = Fernet.generate_key().decode()
_BOX = Fernet(_KEY.encode())
# The stamp to seed pre-migration rows at: whatever the migration under test is
# parented on, looked up rather than remembered, so a renumber cannot move it here
# in silence.
_PREVIOUS_REVISION = down_revision_of_slug("local_endpoint_thinking")

# The world before the level existed: three keys, and only three.
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
# A row written after the level shipped, to prove the backfill leaves it alone.
_LEVEL_SET_DOC = {
    "base_url": "http://box-three.lan:8000/v1",
    "model": "qwen",
    "api_key": None,
    "thinking": "low",
}
_LEGACY_KEY_SECRET = _BOX.encrypt(b"sk-ant-legacy")


def _encrypt(doc: dict) -> bytes:
    return _BOX.encrypt(json.dumps(doc).encode())


def _alembic(target: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = _MIG_URL
    env["INTEGRATIONS_ENCRYPTION_KEY"] = _KEY
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
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


async def _seed_rows() -> uuid.UUID:
    """Endpoint docs in the pre-level format, one already carrying a level, plus a
    key provider's row that must come out byte-identical."""
    conn = await asyncpg.connect(_MIG_URL)
    user = await conn.fetchval(
        "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id", "mig-thinking"
    )
    for name, secret in (
        ("box-one", _encrypt(_LEGACY_KEYLESS_DOC)),
        ("box-two", _encrypt(_LEGACY_KEYED_DOC)),
        ("box-three", _encrypt(_LEVEL_SET_DOC)),
        ("anthropic", _LEGACY_KEY_SECRET),
    ):
        provider = "local" if name.startswith("box-") else "anthropic"
        kind = "endpoint" if provider == "local" else "api_key"
        await conn.execute(
            "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
            "VALUES ($1, $2, $3, $4, $5)",
            user,
            provider,
            kind,
            secret,
            name,
        )
    await conn.close()
    return user


async def test_local_endpoint_docs_gain_the_level_without_losing_anything_else():
    await _run()


async def _run():
    await _create_db()
    try:
        _alembic(_PREVIOUS_REVISION)
        user = await _seed_rows()
        _alembic("head")

        conn = await asyncpg.connect(_MIG_URL)
        try:
            rows = await conn.fetch(
                "SELECT provider, name, secret_enc FROM user_agent_credentials WHERE user_id = $1",
                user,
            )
            by_name = {r["name"]: r for r in rows}
            assert set(by_name) == {"box-one", "box-two", "box-three", "anthropic"}

            # Both pre-level endpoint docs arrive with the level Stash always asked
            # for, and with the three values that were already there unchanged —
            # including the key, which this migration has no reason to open and no
            # right to alter.
            for name, seeded in (
                ("box-one", _LEGACY_KEYLESS_DOC),
                ("box-two", _LEGACY_KEYED_DOC),
            ):
                doc = json.loads(_BOX.decrypt(bytes(by_name[name]["secret_enc"])))
                assert doc == {**seeded, "thinking": "off"}

            # The ciphertext moved for the rows that gained a key (Fernet's random
            # IV means a re-encryption could never equal the original anyway, so
            # what this proves is that the row was rewritten at all) ...
            assert bytes(by_name["box-one"]["secret_enc"]) != _encrypt(_LEGACY_KEYLESS_DOC)
            # ... and did not for the credential that is not an endpoint: a key
            # provider's secret has no thinking level, and a backfill that rewrote
            # it would be rewriting data it never read.
            assert bytes(by_name["anthropic"]["secret_enc"]) == _LEGACY_KEY_SECRET

            # A level somebody chose is his, not the backfill's to reset.
            chosen = json.loads(_BOX.decrypt(bytes(by_name["box-three"]["secret_enc"])))
            assert chosen == _LEVEL_SET_DOC
        finally:
            await conn.close()
    finally:
        await _drop_db()
