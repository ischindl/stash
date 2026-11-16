"""A founder-lineage database arrives at trunk's DEFINITIONS, not just at head.

The founder island carries many of the objects that revisions `0258..0266`
create — its own chain made them — while its stamp sits below those revisions.
Convergence therefore meets: columns with their inline FKs already in place,
a `one_curator_per_scope` index, credential rows already named by their owner,
the widened run-outcome check, and NO `one_curator_per_user_per_wiki` at all
(the founder lineage retired it on its own). The 2026-11 census measured every
one of those island objects byte-identical to trunk's definitions; this test
keeps them that way through any re-run, and it is written to stay red if a
guard regresses into a silent skip.

What existing tests cannot see: `test_migrations.py` only proves an empty
database reaches head; `test_migration_local_endpoint_credentials.py` seeds the
PRE-reshape world; `test_migration_founder_lineage_reapply.py` proves a second
run is quiet. None of them can catch an object that ARRIVES WITH THE WRONG
DEFINITION (`IF NOT EXISTS` keeps whatever was already there, wrong or not), a
credential name the owner chose being overwritten by a backfill, or a drop that
crashes on the one lineage that lacks the object. So this test seeds the fork
shape — deliberately stronger than measured where measurement says "identical"
and in the inverse shape where measurement says "absent" — and asserts
DEFINITIONS converge and DATA survives:

    - `one_curator_per_scope` ends with trunk's COALESCE definition even though
      it was seeded with the older pre-folder rule,
    - `agents_curated_through_pair_check` and the widened
      `agents_last_run_outcome_check` end with trunk's definitions even though
      the pair check was seeded weaker and the outcome check was seeded absent,
    - the run survives the ABSENT `one_curator_per_user_per_wiki` it must drop,
    - a credential whose name is a value no backfill could invent keeps exactly
      that value, while an unnamed row is still named from its host,
    - the three FKs that ride on colliding columns equal trunk's definitions.

The seed is the MEASURED founder shape (FKs included, names set, outcome check
absent, old index retired); the two places it is stronger than measurement are
named in the seed SQL. An AC4 demonstration reddens each assertion by a named
temporary perturbation of the guard it pins; see STAS-443's runbook section.
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
_MIG_DB = "stash_mig_forkconv_" + uuid.uuid4().hex[:12]
_MIG_URL = _BASE_URL.rsplit("/", 1)[0] + "/" + _MIG_DB

# Seed at whatever revision 0258 is parented on — looked up, not remembered, so
# a renumber cannot move the test's premise in silence.
_PREVIOUS_REVISION = down_revision_of_slug("curator_run_single_flight")

_KEY = Fernet.generate_key().decode()
_BOX = Fernet(_KEY.encode())

# A name no host-derived backfill could invent.
_CHOSEN_NAME = "the good box \u0161est\u00e1"
_DOC_KEPT = {"base_url": "http://box-kept.lan:11434/v1", "model": "qwen2.5:7b", "api_key": None}
_DOC_FRESH = {"base_url": "http://box-fresh.lan:11434/v1", "model": "llama3:8b", "api_key": None}
_SECRET_KEPT = _BOX.encrypt(json.dumps(_DOC_KEPT).encode())
_SECRET_FRESH = _BOX.encrypt(json.dumps(_DOC_FRESH).encode())
_SECRET_ANTHROPIC = _BOX.encrypt(b"sk-ant-founder-personal")

# The exact definitions trunk's statements produce, measured from `pg_get_constraintdef`
# / `pg_get_indexdef` on the founder-island copy and on the golden built by the chain.
_TRUNK_SCOPE_INDEX = (
    "CREATE UNIQUE INDEX one_curator_per_scope ON public.agents USING btree "
    "(user_id, curator_wiki, "
    "COALESCE(curator_folder_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE is_curator"
)
_TRUNK_PAIR_CHECK = "CHECK (((curated_through IS NOT NULL) OR (curated_through_event_id IS NULL)))"
_TRUNK_OUTCOME_CHECK = (
    "CHECK ((last_run_outcome = ANY (ARRAY["
    "'started'::text, 'ran'::text, 'failed'::text, "
    "'skipped_credits'::text, 'skipped_no_credential'::text, 'skipped_no_changes'::text, "
    "'skipped_already_running'::text])))"
)
_TRUNK_CURATOR_FK = (
    "FOREIGN KEY (curator_folder_id) REFERENCES session_folders(id) ON DELETE CASCADE"
)
_TRUNK_WIKI_FOLDER_FK = "FOREIGN KEY (wiki_folder_id) REFERENCES folders(id) ON DELETE SET NULL"
_TRUNK_CREDENTIAL_FK = "FOREIGN KEY (credential_id) REFERENCES user_agent_credentials(id)"


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


async def _seed_fork_shape() -> uuid.UUID:
    """The founder island's own schema at this stamp, replayed statement for statement.

    Columns arrive WITH their inline FKs exactly as the census measured them on the
    island; the credential rows arrive named as their owner named them. Two places
    are deliberately stronger than the measurement: `one_curator_per_scope` is
    seeded with the pre-folder rule (measured identical to trunk, but a seed that
    equals the target could never show a skip), and the pair check is seeded weaker
    (likewise). The widened outcome check is seeded ABSENT — the inverse class the
    IF EXISTS guard exists for: on a lineage that never had it, the unguarded drop
    crashed. `one_curator_per_user_per_wiki`, which the chain itself created at
    or below 0225, is retired here exactly as the founder chain retired it
    (measured: absent on the island), so 0261 meets the absence the island hands
    it and must not die on it.
    """
    conn = await asyncpg.connect(_MIG_URL)
    user = await conn.fetchval(
        "INSERT INTO users (name, display_name) VALUES ($1, $1) RETURNING id", "mig-forkconv"
    )
    await conn.execute(
        "INSERT INTO folders (name, created_by, owner_user_id) VALUES ($1, $2, $2)",
        "founder-wiki-home",
        user,
    )
    # A live curator on the columns the 0258-era schema already has; the fork's
    # own columns are added under it, exactly as the founder chain did.
    await conn.execute(
        "INSERT INTO agents (user_id, name, is_curator, curator_wiki, curated_through) "
        "VALUES ($1, 'curator-uno', true, 'wiki', to_timestamp(1700000000))",
        user,
    )

    await conn.execute(
        """
        ALTER TABLE agents
            ADD COLUMN curator_folder_id UUID REFERENCES session_folders(id) ON DELETE CASCADE,
            ADD COLUMN model_id TEXT,
            ADD COLUMN digest_provider TEXT,
            ADD COLUMN digest_model_id TEXT,
            ADD COLUMN curated_through_event_id uuid;
        ALTER TABLE session_folders
            ADD COLUMN wiki_folder_id UUID REFERENCES folders(id) ON DELETE SET NULL;
        ALTER TABLE agents DROP CONSTRAINT agents_last_run_outcome_check;
        ALTER TABLE agents ADD CONSTRAINT agents_curated_through_pair_check
            CHECK (curated_through_event_id IS NULL);
        DROP INDEX one_curator_per_user_per_wiki;
        CREATE UNIQUE INDEX one_curator_per_scope ON agents (user_id, curator_wiki)
            WHERE is_curator;
        """
    )

    await conn.execute(
        """
        ALTER TABLE user_agent_credentials
            ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid(),
            ADD COLUMN name text,
            ADD COLUMN models_json_enc bytea NULL;
        ALTER TABLE user_agent_credentials DROP CONSTRAINT user_agent_credentials_pkey;
        ALTER TABLE user_agent_credentials ADD PRIMARY KEY (id);
        CREATE UNIQUE INDEX one_key_credential_per_user_provider
            ON user_agent_credentials (user_id, provider) WHERE provider <> 'local';
        CREATE INDEX user_agent_credentials_user
            ON user_agent_credentials (user_id, provider, created_at);
        """
    )
    # The agents pin needs the credential id to exist first — same dependency 0263 orders by.
    await conn.execute(
        "ALTER TABLE agents ADD COLUMN credential_id UUID REFERENCES user_agent_credentials(id)"
    )
    await conn.execute(
        "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
        "VALUES ($1, 'local', 'endpoint', $2, $3)",
        user,
        _SECRET_KEPT,
        _CHOSEN_NAME,
    )
    await conn.execute(
        "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc, name) "
        "VALUES ($1, 'anthropic', 'api_key', $2, 'personal-anthropic')",
        user,
        _SECRET_ANTHROPIC,
    )
    await conn.execute(
        "INSERT INTO user_agent_credentials (user_id, provider, kind, secret_enc) "
        "VALUES ($1, 'local', 'endpoint', $2)",
        user,
        _SECRET_FRESH,
    )
    await conn.close()
    return user


async def _definition(conn, table: str, constraint: str) -> str | None:
    return await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = $1::regclass AND conname = $2",
        table,
        constraint,
    )


async def test_fork_shape_converges_to_trunk_definitions():
    await _create_db()
    try:
        _alembic(_PREVIOUS_REVISION)
        user = await _seed_fork_shape()
        _alembic("head")

        conn = await asyncpg.connect(_MIG_URL)
        try:
            # 1. A same-named index that arrived with the WRONG definition must end
            #    with trunk's: drop-then-rebuild, not IF NOT EXISTS inheriting the seed.
            scope_def = await conn.fetchval(
                "SELECT pg_get_indexdef('one_curator_per_scope'::regclass)"
            )
            assert scope_def == _TRUNK_SCOPE_INDEX

            # 2. CHECK constraints converge the same way, from either a weaker
            #    predecessor (pair check) or an absent one (outcome check).
            pair = await _definition(conn, "agents", "agents_curated_through_pair_check")
            assert pair == _TRUNK_PAIR_CHECK
            outcome = await _definition(conn, "agents", "agents_last_run_outcome_check")
            assert outcome == _TRUNK_OUTCOME_CHECK

            # 3. The absent retired index did not kill the run and did not come back.
            retired = await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = 'one_curator_per_user_per_wiki'"
            )
            assert retired == 0

            # 4. A name the owner chose is exactly preserved, whatever it is; an
            #    unnamed row is still named from the host in its own ciphertext.
            rows = await conn.fetch(
                "SELECT provider, name, secret_enc FROM user_agent_credentials WHERE user_id = $1",
                user,
            )
            by_name = {r["name"]: r for r in rows}
            assert _CHOSEN_NAME in by_name, f"owner's rename was overwritten: {sorted(by_name)}"
            assert "personal-anthropic" in by_name
            assert "box-fresh.lan" in by_name, (
                f"the unnamed row was not named from its host: {sorted(by_name)}"
            )
            # The kept row's secret was rewritten only by the two local-doc rewrites,
            # 0272's `thinking` key and 0275's `context_window`; the number stamped is the
            # synthesized-default history record, because this row has no models.json
            # override of its own to be read. The key provider's bytes must not move at all
            # (Fernet IV makes any re-encryption differ, so byte-identity proves no rewrite
            # happened — and proves both rewrites are local-provider-scoped).
            assert by_name["personal-anthropic"]["secret_enc"] == _SECRET_ANTHROPIC
            assert json.loads(_BOX.decrypt(bytes(by_name[_CHOSEN_NAME]["secret_enc"]))) == {
                **_DOC_KEPT,
                "thinking": "off",
                "context_window": 131072,
            }

            # 5. The FKs that ride on colliding columns equal trunk's definitions:
            #    guarded column adds must not ship founder configuration FK-less.
            curator_fk = await _definition(conn, "agents", "agents_curator_folder_id_fkey")
            assert curator_fk == _TRUNK_CURATOR_FK
            wiki_fk = await _definition(
                conn, "session_folders", "session_folders_wiki_folder_id_fkey"
            )
            assert wiki_fk == _TRUNK_WIKI_FOLDER_FK
            credential_fk = await _definition(conn, "agents", "agents_credential_id_fkey")
            assert credential_fk == _TRUNK_CREDENTIAL_FK
        finally:
            await conn.close()
    finally:
        await _drop_db()
