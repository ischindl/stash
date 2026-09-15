"""Data-level test for the multi-endpoint credential migration (0215 -> 0216).

0216 reshapes `user_agent_credentials` while it holds live data: the
(user_id, provider) primary key becomes an `id` primary key plus a partial
unique index, and every pre-existing row has to arrive with a name its owner
will recognise. `test_migrations.py` only proves `alembic upgrade head` does not
error on an empty database — it cannot see a mis-named row or an index that now
enforces the wrong thing.

So this test seeds the legacy shape on an isolated database (one encrypted local
endpoint row + one anthropic key row), runs the chain, and asserts:
    - the id arrived and IS the primary key,
    - an endpoint row is named from the HOST inside its own encrypted base_url
      (the founder's word for a box) and a key row from its provider,
    - the secret bytes carried over untouched — same ciphertext, one decrypt,
      the same doc (Fernet's random IV makes a fresh encryption of the same text
      differ, so an untouched row is provable byte-for-byte),
    - a SECOND local row now inserts while a second anthropic row is refused:
      the partial index keeps one-key-per-provider and drops it for endpoints,
      exactly and only.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid

import asyncpg
from cryptography.fernet import Fernet

_BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://stash:stash@localhost:5432/stash_test",
)
_ADMIN_URL = _BASE_URL.rsplit("/", 1)[0] + "/postgres"
_MIG_DB = "stash_mig_endpoints_" + uuid.uuid4().hex[:12]
_MIG_URL = _BASE_URL.rsplit("/", 1)[0] + "/" + _MIG_DB

# The keyring the seeded rows are encrypted under, and therefore the one the
# backfill needs in order to read a base_url out of them.
_KEY = Fernet.generate_key().decode()
_BOX_ONE = Fernet(_KEY.encode())
_PREVIOUS_REVISION = "0215"

_LEGACY_DOC = {"base_url": "http://box-one.lan:11434/v1", "model": "qwen2.5:7b", "api_key": None}
# Encrypted ONCE, here, so the post-migration bytes can be compared to it.
_LEGACY_SECRET = _BOX_ONE.encrypt(json.dumps(_LEGACY_DOC).encode())
_LEGACY_KEY_SECRET = _BOX_ONE.encrypt(b"sk-ant-legacy")


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


async def _seed_legacy_rows() -> uuid.UUID:
    """The world exactly as 0215 left it: at most one row per provider, the
    local one an encrypted endpoint doc, no id and no name column."""
    conn = await asyncpg.connect(_MIG_URL)
    user = await conn.fetchval(
        "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id", "mig-endpoints"
    )
    await conn.execute(
        "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc) "
        "VALUES ($1, 'local', 'endpoint', $2)",
        user,
        _LEGACY_SECRET,
    )
    await conn.execute(
        "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc) "
        "VALUES ($1, 'anthropic', 'api_key', $2)",
        user,
        _LEGACY_KEY_SECRET,
    )
    await conn.close()
    return user


def test_local_endpoint_rows_survive_the_reshape_with_names_and_ids():
    asyncio.run(_run())


async def _run():
    await _create_db()
    try:
        _alembic(_PREVIOUS_REVISION)
        user = await _seed_legacy_rows()
        _alembic("head")

        conn = await asyncpg.connect(_MIG_URL)
        try:
            rows = await conn.fetch(
                "SELECT id, provider, name, secret_enc FROM user_agent_credentials "
                "WHERE user_id = $1",
                user,
            )
            by_provider = {r["provider"]: r for r in rows}
            assert set(by_provider) == {"local", "anthropic"}

            # The id arrived, and it is the primary key the old composite one was
            # replaced by — the reason a second endpoint can exist at all.
            assert all(r["id"] is not None for r in rows)
            pk = await conn.fetch(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'user_agent_credentials'::regclass AND i.indisprimary"
            )
            assert [p["attname"] for p in pk] == ["id"]

            # Named the way its owner would name it: the box's host, the key's
            # provider. The host is inside the ENCRYPTED doc, so this only passes
            # if the backfill decrypted it.
            assert by_provider["local"]["name"] == "box-one.lan"
            assert by_provider["anthropic"]["name"] == "anthropic"

            # The secret carried over untouched — same ciphertext bytes (Fernet's
            # random IV means a re-encryption could never equal this), and still
            # exactly one decrypt away from the doc the user typed.
            assert by_provider["local"]["secret_enc"] == _LEGACY_SECRET
            assert by_provider["anthropic"]["secret_enc"] == _LEGACY_KEY_SECRET
            assert (
                json.loads(_BOX_ONE.decrypt(bytes(by_provider["local"]["secret_enc"])))
                == _LEGACY_DOC
            )

            # A second box is now storable.
            await conn.execute(
                "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
                "VALUES ($1, 'local', 'endpoint', $2, 'box-two')",
                user,
                _BOX_ONE.encrypt(
                    json.dumps({**_LEGACY_DOC, "base_url": "http://box-two:8000/v1"}).encode()
                ),
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM user_agent_credentials "
                    "WHERE user_id = $1 AND provider = 'local'",
                    user,
                )
                == 2
            )

            # A second key for one provider is still refused — the rule survives
            # as a partial index and only steps aside for 'local'.
            threw = False
            try:
                await conn.execute(
                    "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
                    "VALUES ($1, 'anthropic', 'api_key', $2, 'anthropic')",
                    user,
                    _LEGACY_KEY_SECRET,
                )
            except asyncpg.UniqueViolationError:
                threw = True
            assert threw, "a second anthropic row must violate the partial unique index"

            # An agent can pin one specific box.
            box_two = await conn.fetchval(
                "SELECT id FROM user_agent_credentials "
                "WHERE user_id = $1 AND provider = 'local' AND name = 'box-two'",
                user,
            )
            await conn.execute(
                "INSERT INTO agents (user_id, name, credential_id) VALUES ($1, 'pinned', $2)",
                user,
                box_two,
            )
            assert (
                await conn.fetchval(
                    "SELECT credential_id FROM agents WHERE user_id = $1 AND name = 'pinned'", user
                )
                == box_two
            )
        finally:
            await conn.close()
    finally:
        await _drop_db()
