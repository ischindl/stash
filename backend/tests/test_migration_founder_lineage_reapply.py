"""The founder-lineage databases can run the curator/credential segment twice; a
legacy database still gets it exactly once.

The founder database carries `share_wiki`, the curator columns, the credential
reshape and the watermark pair from this repository's own pre-renumber chain,
while its `alembic_version` row sits below every revision that created them
(STAS-440 measured both facts on the live database and on a faithful copy of it).
Convergence therefore runs `0257`-`0266` against a database where those objects
already exist, and five of those nodes were written for a database that lacked
them: each died with `already exists` and took the boot down with it.

`test_migrations.py` cannot see this at all — it upgrades an empty database,
where every one of those statements meets an absent object and succeeds. So this
module builds both lineages on isolated databases and runs the segment against
each:

Lineage 1 — the founder shape. Build the chain to the end of the segment, seed
the fork's world on top of it (a curator agent with a folder scope, a model
pick, a digest pair and a populated watermark; a project cleared for the shared
wiki; two credential rows already named under an `id` primary key), then rewind
the version row alone to the state before the segment and run the segment again,
three times over. Every seeded value must be byte-identical afterwards, and the
whole run carries a keyring that is valid but is NOT the one the seeded rows are
encrypted under: any node that still tried to read a named row's secret would
die with `InvalidToken`, so exit 0 is the proof that the guards skipped the work
rather than that nothing checked.

Lineage 2 — the legacy shape. Upgrade to the state before the segment, seed the
row shape that has no `id` and no `name` and at most one credential per provider,
then run the segment once with the keyring the rows ARE under, and assert the
objects arrive with the definitions this repository's files spell: the reshape
happened, the endpoint row was named from the host inside its own encrypted
document, the ciphertext bytes did not move, a second box is storable while a
second key for one provider is refused, and the watermark pair CHECK rejects an
event with no instant.

`0260`, `0264` and `0265` sit inside the re-applied range and are deliberately
left alone by STAS-440: the first already widens a CHECK by `DROP CONSTRAINT IF
EXISTS` and adds its column `IF NOT EXISTS`, the second is a
`CREATE TABLE IF NOT EXISTS`, and the third reads its own convergence out of the
catalog (`0265`'s `_has_column`) and returns before any data work when the
database already converged. `0268`, `0269` and `0270` sit outside the range and
are idempotent by their own predicates (`WHERE finished_at IS NULL`, an
`IS NOT NULL` guard, and a one-time insert the founder lineage runs exactly
once). Lineage 1 proves all of that by re-running them, not by asserting it.

Both lineages run on their own scratch database. Neither touches `stash_test`'s
schema beyond creating and dropping its own databases, and neither can reach any
other database: the revision endpoints are resolved from `migration_ids.py` by
slug, so a renumber moves this module with the chain instead of silently
pointing it at an unrelated revision.
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

# The segment STAS-440 measured as the collision range, stated by slug.
# `_SEGMENT_START` is the revision the first colliding node is parented on — the
# stamp a founder-lineage database can honestly be sitting at while holding the
# objects — and `_SEGMENT_END` is the revision the node AFTER the range is
# parented on, i.e. one past the last colliding node.
_SEGMENT_START = down_revision_of_slug("session_folders_share_wiki")
_SEGMENT_END = down_revision_of_slug("widen_history_events_session_id")

_FOUNDER_DB = "stash_mig_founder_lineage_" + uuid.uuid4().hex[:12]
_LEGACY_DB = "stash_mig_legacy_segment_" + uuid.uuid4().hex[:12]

# The keyring the seeded rows are encrypted under, and a second valid keyring
# that no seeded row can be read with.
_FORK_KEYRING = Fernet.generate_key().decode()
_FOREIGN_KEYRING = Fernet.generate_key().decode()
_BOX = Fernet(_FORK_KEYRING.encode())

_ENDPOINT_DOC = {"base_url": "http://box-one.lan:11434/v1", "model": "qwen2.5:7b", "api_key": None}
_ENDPOINT_SECRET = _BOX.encrypt(json.dumps(_ENDPOINT_DOC).encode())
_KEY_SECRET = _BOX.encrypt(b"sk-ant-legacy")

# The objects the segment owns. Both lineages must end holding exactly these, and
# `one_curator_per_user_per_wiki` must be gone in both: `0261` drops it because
# the scope rule moved to the folder, and the founder lineage already dropped it
# with its own fork work — which is why that drop has to be `IF EXISTS`.
_SEGMENT_INDEXES = (
    "one_curator_per_user_per_wiki",
    "one_curator_per_scope",
    "one_key_credential_per_user_provider",
    "user_agent_credentials_user",
)
_SEGMENT_COLUMNS = (
    ("agents", ("credential_id", "curated_through_event_id", "curator_folder_id", "model_id")),
    ("session_folders", ("share_wiki", "wiki_folder_id")),
    ("user_agent_credentials", ("id", "name")),
)


def _db_url(database: str) -> str:
    return _BASE_URL.rsplit("/", 1)[0] + "/" + database


def _alembic(database: str, keyring: str, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = _db_url(database)
    env["INTEGRATIONS_ENCRYPTION_KEY"] = keyring
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} on {database} failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


async def _create_db(database: str) -> None:
    conn = await asyncpg.connect(_ADMIN_URL)
    await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
    await conn.execute(f'CREATE DATABASE "{database}"')
    await conn.close()
    conn = await asyncpg.connect(_db_url(database))
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.close()


async def _drop_db(database: str) -> None:
    conn = await asyncpg.connect(_ADMIN_URL)
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = $1 AND pid <> pg_backend_pid()",
        database,
    )
    await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
    await conn.close()


async def _lines(database: str, query: str) -> list[str]:
    conn = await asyncpg.connect(_db_url(database))
    try:
        return [str(row[0]) for row in await conn.fetch(query)]
    finally:
        await conn.close()


async def _seed_founder_world(database: str) -> None:
    """The world a founder-lineage database already holds when convergence meets it.

    Every value here is one the convergence run must leave untouched, so the
    assertions read this seed back rather than restate it.
    """
    conn = await asyncpg.connect(_db_url(database))
    try:
        user = await conn.fetchval(
            "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id", "mig-founder"
        )
        wiki = await conn.fetchval(
            "INSERT INTO folders (name, created_by, owner_user_id) VALUES ('curated wiki', $1, $1) "
            "RETURNING id",
            user,
        )
        project = await conn.fetchval(
            "INSERT INTO session_folders (owner_user_id, name, slug, share_wiki, wiki_folder_id) "
            "VALUES ($1, 'Product', 'product', true, $2) RETURNING id",
            user,
            wiki,
        )
        credential = await conn.fetchval(
            "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
            "VALUES ($1, 'local', 'endpoint', $2, 'box-one.lan') RETURNING id",
            user,
            _ENDPOINT_SECRET,
        )
        await conn.execute(
            "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
            "VALUES ($1, 'anthropic', 'api_key', $2, 'anthropic')",
            user,
            _KEY_SECRET,
        )
        await conn.execute(
            "INSERT INTO agents (user_id, name, is_curator, curator_wiki, curator_folder_id, "
            "model_id, digest_provider, digest_model_id, credential_id, curated_through, "
            "curated_through_event_id) "
            "VALUES ($1, 'wiki curator', true, 'product', $2, "
            "'models/gemini-flash', 'openai', 'gpt-4o-mini', $3, now() - interval '1 day', $4)",
            user,
            project,
            credential,
            uuid.uuid4(),
        )
    finally:
        await conn.close()


async def _seed_legacy_rows(database: str) -> uuid.UUID:
    """The shape `0256` leaves behind: no id, no name, one credential per provider."""
    conn = await asyncpg.connect(_db_url(database))
    try:
        user = await conn.fetchval(
            "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id", "mig-legacy"
        )
        await conn.execute(
            "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc) "
            "VALUES ($1, 'local', 'endpoint', $2)",
            user,
            _ENDPOINT_SECRET,
        )
        await conn.execute(
            "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc) "
            "VALUES ($1, 'anthropic', 'api_key', $2)",
            user,
            _KEY_SECRET,
        )
        return user
    finally:
        await conn.close()


async def _seeded_rows(database: str) -> str:
    """Every seeded value as one comparable block; the ciphertext is fingerprinted.

    `secret_enc` is hashed rather than carried: convergence must not rewrite it,
    and a Fernet blob is both unwieldy and a secret.
    """
    return "\n".join(
        [
            *(
                await _lines(
                    database,
                    "SELECT 'agent|'||name||'|'||is_curator::text"
                    "||'|'||coalesce(curator_wiki, '\\N')"
                    "||'|'||coalesce(curator_folder_id::text, '\\N')"
                    "||'|'||coalesce(model_id, '\\N')"
                    "||'|'||coalesce(digest_provider, '\\N')"
                    "||'|'||coalesce(digest_model_id, '\\N')"
                    "||'|'||coalesce(credential_id::text, '\\N')"
                    "||'|event_set='||(curated_through_event_id IS NOT NULL)::text"
                    "||'|instant_set='||(curated_through IS NOT NULL)::text"
                    " FROM agents ORDER BY name",
                )
            ),
            *(
                await _lines(
                    database,
                    "SELECT 'folder|'||name||'|'||slug||'|'||share_wiki::text"
                    "||'|'||coalesce(wiki_folder_id::text, '\\N')"
                    " FROM session_folders ORDER BY name",
                )
            ),
            *(
                await _lines(
                    database,
                    "SELECT 'credential|'||provider||'|'||kind||'|'||name"
                    "||'|'||md5(secret_enc::text)"
                    " FROM user_agent_credentials ORDER BY provider",
                )
            ),
        ]
    )


async def _catalog(database: str) -> list[str]:
    """The segment's objects, as the catalog describes them."""
    columns = " OR ".join(
        f"(table_name = '{table}' AND column_name = '{column}')"
        for table, names in _SEGMENT_COLUMNS
        for column in names
    )
    indexes = ", ".join(f"'{name}'" for name in _SEGMENT_INDEXES)
    return await _lines(
        database,
        "SELECT 'column:'||table_name||'.'||column_name||'|null='||is_nullable"
        " FROM information_schema.columns"
        f" WHERE table_schema = current_schema() AND ({columns})"
        " UNION ALL SELECT 'index:'||indexname FROM pg_indexes"
        f" WHERE schemaname = current_schema() AND indexname IN ({indexes})"
        " UNION ALL SELECT 'constraint:'||conname FROM pg_constraint"
        " WHERE connamespace = current_schema()::regnamespace"
        " AND conname IN ('agents_curated_through_pair_check', 'user_agent_credentials_pkey')"
        " UNION ALL SELECT 'primary_key:'||a.attname FROM pg_index i"
        " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
        " WHERE i.indrelid = 'user_agent_credentials'::regclass AND i.indisprimary"
        " ORDER BY 1",
    )


async def test_founder_lineage_reapplies_the_segment_without_touching_its_data():
    await _create_db(_FOUNDER_DB)
    try:
        # The chain reaches the end of the segment the way any database gets
        # there; then the fork's own objects are seeded on top of it. From here
        # on the keyring handed to alembic is the FOREIGN one, so a run that
        # decrypts any seeded row cannot survive its own migration.
        _alembic(_FOUNDER_DB, _FOREIGN_KEYRING, "upgrade", _SEGMENT_END)
        await _seed_founder_world(_FOUNDER_DB)
        before = await _seeded_rows(_FOUNDER_DB)
        assert "wiki curator" in before, "the founder seed did not land"
        catalog_before = await _catalog(_FOUNDER_DB)

        for _ in range(3):
            # Only the version row moves: the objects stay, the stamp sits below
            # them. That is exactly the founder database's condition.
            _alembic(_FOUNDER_DB, _FOREIGN_KEYRING, "stamp", _SEGMENT_START)
            _alembic(_FOUNDER_DB, _FOREIGN_KEYRING, "upgrade", _SEGMENT_END)
            assert await _seeded_rows(_FOUNDER_DB) == before
            assert await _catalog(_FOUNDER_DB) == catalog_before

        # The segment's end state, named rather than inferred from a clean exit.
        # `one_curator_per_user_per_wiki` is absent because the founder lineage
        # dropped it with its own fork work and `0261` drops it `IF EXISTS`.
        assert catalog_before == [
            "column:agents.credential_id|null=YES",
            "column:agents.curated_through_event_id|null=YES",
            "column:agents.curator_folder_id|null=YES",
            "column:agents.model_id|null=YES",
            "column:session_folders.share_wiki|null=NO",
            "column:session_folders.wiki_folder_id|null=YES",
            "column:user_agent_credentials.id|null=NO",
            "column:user_agent_credentials.name|null=NO",
            "constraint:agents_curated_through_pair_check",
            "constraint:user_agent_credentials_pkey",
            "index:one_curator_per_scope",
            "index:one_key_credential_per_user_provider",
            "index:user_agent_credentials_user",
            "primary_key:id",
        ], "the segment's objects are not the ones this repository's files spell"
    finally:
        await _drop_db(_FOUNDER_DB)


async def test_legacy_database_receives_the_segment_exactly_as_trunk_spells_it():
    await _create_db(_LEGACY_DB)
    try:
        _alembic(_LEGACY_DB, _FORK_KEYRING, "upgrade", _SEGMENT_START)
        user = await _seed_legacy_rows(_LEGACY_DB)
        _alembic(_LEGACY_DB, _FORK_KEYRING, "upgrade", _SEGMENT_END)

        conn = await asyncpg.connect(_db_url(_LEGACY_DB))
        try:
            rows = {
                row["provider"]: row
                for row in await conn.fetch(
                    "SELECT provider, id, name, secret_enc FROM user_agent_credentials"
                )
            }
            # The reshape ran, and the endpoint row got the name its owner would
            # use for it — the host inside its own encrypted document, which only
            # the backfill's decrypt can produce.
            assert rows["local"]["name"] == "box-one.lan"
            assert rows["anthropic"]["name"] == "anthropic"
            pk = await conn.fetch(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'user_agent_credentials'::regclass AND i.indisprimary"
            )
            assert [p["attname"] for p in pk] == ["id"]

            # The segment stops before `local_endpoint_thinking`, which rewrites
            # every local document and moves its ciphertext by design. Within the
            # segment the backfill touches the `name` column alone, so BOTH rows
            # must still hold the exact bytes they arrived with.
            assert bytes(rows["local"]["secret_enc"]) == _ENDPOINT_SECRET
            assert bytes(rows["anthropic"]["secret_enc"]) == _KEY_SECRET

            # A second box is storable; a second key for one provider is not.
            await conn.execute(
                "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
                "VALUES ($1, 'local', 'endpoint', $2, 'box-two')",
                user,
                _BOX.encrypt(
                    json.dumps({**_ENDPOINT_DOC, "base_url": "http://box-two:8000/v1"}).encode()
                ),
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM user_agent_credentials"
                    " WHERE user_id = $1 AND provider = 'local'",
                    user,
                )
                == 2
            )
            threw = False
            try:
                await conn.execute(
                    "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
                    "VALUES ($1, 'anthropic', 'api_key', $2, 'anthropic')",
                    user,
                    _KEY_SECRET,
                )
            except asyncpg.UniqueViolationError:
                threw = True
            assert threw, "a second key row for one provider must violate the partial index"

            # The watermark pair rule is enforced, not merely declared: an event
            # half with no instant half is the state `0266` exists to forbid.
            threw = False
            try:
                await conn.execute(
                    "INSERT INTO agents (user_id, name, curated_through_event_id) "
                    "VALUES ($1, 'half a watermark', $2)",
                    user,
                    uuid.uuid4(),
                )
            except asyncpg.CheckViolationError:
                threw = True
            assert threw, "a watermark event with no instant must violate the pair CHECK"
        finally:
            await conn.close()

        catalog = await _catalog(_LEGACY_DB)
        assert "index:one_curator_per_scope" in catalog
        assert "index:one_curator_per_user_per_wiki" not in catalog
        assert "primary_key:id" in catalog
    finally:
        await _drop_db(_LEGACY_DB)
