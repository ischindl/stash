"""A founder-lineage database owes trunk's *data* convergence, not just its columns.

The founder (dogfood) build stamped its database `0209` while trunk's `0203`-`0207`
never ran there. Nine missing columns are the visible half of that gap, and a
schema-only test passes on a database whose shared wikis still hold everything trunk
revoked everywhere else: customer-private pages readable by strangers, live shares, a
published skill built from compiled profiles, and the curator's own Log page full of
customer details. `0218_converge_dogfood_lineage` is the migration that has to undo
all of it, so this asserts the undoing — the revocation, the archival, the watermark
and queue resets — and, just as loudly, that it happens exactly once: the second pass
must not re-archive a live wiki or double-increment a generation counter.

Everything runs through the migration's own `upgrade()`, inside a transaction that is
rolled back, so the seeded pre-convergence schema (dropped columns, dropped table)
cannot leak into any other test in this session.
"""

import importlib
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import unique_name

CONVERGENCE = "backend.migrations.versions.0218_converge_dogfood_lineage"

# What the founder build never ran, taken back off the test schema so the migration
# meets the database it will really meet: trunk 0204-0207's nine columns and trunk
# 0217's table (whose id a database stamped 0209 counts as applied without having
# run it, so 0218 has to create it).
FOUNDER_SCHEMA_SQL = [
    "DROP TABLE IF EXISTS folder_mcp_tokens",
    "ALTER TABLE workspaces DROP COLUMN IF EXISTS curation_generation CASCADE",
    "ALTER TABLE users DROP COLUMN IF EXISTS developer_platform_only CASCADE",
    """ALTER TABLE user_sources
        DROP COLUMN IF EXISTS sync_task_id CASCADE,
        DROP COLUMN IF EXISTS sync_claimed_at CASCADE,
        DROP COLUMN IF EXISTS sync_started_at CASCADE,
        DROP COLUMN IF EXISTS sync_alerted_at CASCADE""",
    """ALTER TABLE drive_documents
        DROP COLUMN IF EXISTS extraction_task_id CASCADE,
        DROP COLUMN IF EXISTS extraction_claimed_at CASCADE,
        DROP COLUMN IF EXISTS extraction_retry_at CASCADE""",
]

# The trunk 0204-0207 columns plus trunk 0217's table: what the schema half of 0218
# owes a founder database, checked by name so a renamed column cannot slip past.
CONVERGED_SCHEMA = [
    ("workspaces", "curation_generation"),
    ("users", "developer_platform_only"),
    ("user_sources", "sync_task_id"),
    ("user_sources", "sync_claimed_at"),
    ("user_sources", "sync_started_at"),
    ("user_sources", "sync_alerted_at"),
    ("drive_documents", "extraction_task_id"),
    ("drive_documents", "extraction_claimed_at"),
    ("drive_documents", "extraction_retry_at"),
]


def _insert(conn, statement, **params) -> str:
    return str(conn.execute(text(statement), params).scalar_one())


def _value(conn, statement, **params):
    return conn.execute(text(statement), params).scalar()


def _seed_founder_world(conn) -> dict:
    """One developer workspace exactly as the founder build left it.

    `share_wiki` is already false for the customer (founder `0203` shipped that
    column and the customer had opted out), but none of trunk's enforcement ran: the
    wiki tree is still public, the skill is still published, the curator still has a
    watermark, and a source sync is still marked in flight. A second workspace with
    no opted-out customer is seeded alongside it, because trunk's revocation is
    scoped to opted-out workspaces and must leave that one alone.
    """
    dev = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Founder Dev') RETURNING id",
        name=unique_name("founder-dev"),
    )
    root = _insert(
        conn,
        "INSERT INTO folders (owner_user_id, created_by, name, is_protected) "
        "VALUES (:dev, :dev, 'External Wiki', true) RETURNING id",
        dev=dev,
    )
    workspace = _insert(
        conn,
        "INSERT INTO workspaces (name, scope_user_id, external_wiki_folder_id) "
        "VALUES ('founder lineage', :dev, :root) RETURNING id",
        dev=dev,
        root=root,
    )
    customer_wiki = _insert(
        conn,
        "INSERT INTO folders (owner_user_id, created_by, name) VALUES (:dev, :dev, 'Customer') "
        "RETURNING id",
        dev=dev,
    )
    customer = _insert(
        conn,
        "INSERT INTO end_users (workspace_id, external_id, name, share_wiki, wiki_folder_id) "
        "VALUES (:workspace, 'customer-1', 'Customer', false, :wiki) RETURNING id",
        workspace=workspace,
        wiki=customer_wiki,
    )
    stranger = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Stranger') RETURNING id",
        name=unique_name("founder-stranger"),
    )

    compiled = _insert(
        conn,
        "INSERT INTO folders (owner_user_id, created_by, name, parent_folder_id, public_permission) "
        "VALUES (:dev, :dev, 'Compiled profiles', :root, 'read') RETURNING id",
        dev=dev,
        root=root,
    )
    page = _insert(
        conn,
        "INSERT INTO pages (owner_user_id, created_by, folder_id, name, content_markdown, "
        "public_permission) VALUES (:dev, :dev, :compiled, 'Profile', 'customer secret', 'read') "
        "RETURNING id",
        dev=dev,
        compiled=compiled,
    )
    table = _insert(
        conn,
        "INSERT INTO tables (owner_user_id, created_by, folder_id, name, public_permission) "
        "VALUES (:dev, :dev, :compiled, 'Derived records', 'read') RETURNING id",
        dev=dev,
        compiled=compiled,
    )
    file = _insert(
        conn,
        "INSERT INTO files (owner_user_id, uploaded_by, folder_id, end_user_id, name, content_type, "
        "size_bytes, storage_key, public_permission) VALUES (:dev, :dev, :compiled, :customer, "
        "'customer secret', 'text/plain', 16, 'convergence-test', 'read') RETURNING id",
        dev=dev,
        compiled=compiled,
        customer=customer,
    )
    skill = _insert(
        conn,
        "INSERT INTO skills (owner_user_id, owner_id, slug, title, folder_id) "
        "VALUES (:dev, :dev, :slug, 'Compiled profiles', :compiled) RETURNING id",
        dev=dev,
        slug=unique_name("founder-skill"),
        compiled=compiled,
    )
    share = _insert(
        conn,
        "INSERT INTO shares (owner_user_id, created_by, object_type, object_id, principal_type, "
        "principal_id, permission) VALUES (:dev, :dev, 'page', :page, 'user', :stranger, 'read') "
        "RETURNING id",
        dev=dev,
        page=page,
        stranger=stranger,
    )
    log = _insert(
        conn,
        "INSERT INTO pages (owner_user_id, created_by, folder_id, name, content_markdown, "
        "public_permission) VALUES (:dev, :dev, :root, 'Log', 'customer detail', 'read') "
        "RETURNING id",
        dev=dev,
        root=root,
    )
    # A shared page that points at the Log page: trunk 0203 strips the reference
    # line and keeps the rest, because customers must not be invited to retrieve a
    # private archive through the developer's credential.
    notes = _insert(
        conn,
        "INSERT INTO pages (owner_user_id, created_by, folder_id, name, content_markdown, "
        "public_permission) VALUES (:dev, :dev, :root, 'Notes', :content, 'read') RETURNING id",
        dev=dev,
        root=root,
        content="Standup notes for the week.\nSee /memory/Log.md for the history.\n",
    )
    curator = _insert(
        conn,
        "INSERT INTO agents (user_id, name, is_curator, curator_wiki, curated_through) "
        "VALUES (:dev, 'Curator', true, 'external', now()) RETURNING id",
        dev=dev,
    )
    source = _insert(
        conn,
        "INSERT INTO user_sources (owner_user_id, source_type, external_ref, display_name, "
        "sync_status) VALUES (:dev, 'notion', 'founder-workspace', 'Docs', 'syncing') "
        "RETURNING id",
        dev=dev,
    )
    document = _insert(
        conn,
        "INSERT INTO drive_documents (owner_user_id, source_id, path, name, extraction_status, "
        "extraction_attempts, locked_at) VALUES (:dev, :source, '/Docs/roadmap', 'roadmap', "
        "'processing', 1, now()) RETURNING id",
        dev=dev,
        source=source,
    )

    bystander = _insert(
        conn,
        "INSERT INTO users (name, display_name) VALUES (:name, 'Bystander') RETURNING id",
        name=unique_name("founder-bystander"),
    )
    bystander_root = _insert(
        conn,
        "INSERT INTO folders (owner_user_id, created_by, name, is_protected) "
        "VALUES (:b, :b, 'External Wiki', true) RETURNING id",
        b=bystander,
    )
    bystander_workspace = _insert(
        conn,
        "INSERT INTO workspaces (name, scope_user_id, external_wiki_folder_id) "
        "VALUES ('never opted out', :b, :root) RETURNING id",
        b=bystander,
        root=bystander_root,
    )
    bystander_page = _insert(
        conn,
        "INSERT INTO pages (owner_user_id, created_by, folder_id, name, content_markdown, "
        "public_permission) VALUES (:b, :b, :root, 'Kept', 'public on purpose', 'read') "
        "RETURNING id",
        b=bystander,
        root=bystander_root,
    )

    return {
        "dev": dev,
        "workspace": workspace,
        "root": root,
        "customer": customer,
        "compiled": compiled,
        "page": page,
        "table": table,
        "file": file,
        "skill": skill,
        "share": share,
        "log": log,
        "notes": notes,
        "curator": curator,
        "source": source,
        "document": document,
        "bystander_workspace": bystander_workspace,
        "bystander_root": bystander_root,
        "bystander_page": bystander_page,
    }


def _converge(conn) -> None:
    """The migration's real `upgrade()` — not a copy of its statements.

    `upgrade()` calls `converge(op.get_bind())`, which is the exact call path an
    `alembic upgrade head` at boot takes.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    convergence = importlib.import_module(CONVERGENCE)
    with Operations.context(MigrationContext.configure(conn)):
        convergence.upgrade()


async def _rolled_back(work) -> None:
    """Run `work(sync_connection)` and put every byte back, schema changes included."""
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            await conn.run_sync(work)
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_founder_database_revokes_shared_wiki_and_archives_curator_log():
    """The whole point of 0218: a founder database ends up as unshared as trunk's."""

    def work(conn):
        for statement in FOUNDER_SCHEMA_SQL:
            conn.execute(text(statement))
        world = _seed_founder_world(conn)
        _converge(conn)

        for table, column in CONVERGED_SCHEMA:
            assert _value(
                conn,
                "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = :table AND column_name = :column",
                table=table,
                column=column,
            ), f"{table}.{column} never arrived"
        assert _value(
            conn,
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() "
            "AND table_name = 'folder_mcp_tokens'",
        ), "trunk 0217's table never arrived, though a 0209 database counts it as applied"

        # Every object in the old tree loses its public read, its shares, and its
        # link to the customer whose data it is.
        for table, object_id in (
            ("folders", world["compiled"]),
            ("pages", world["page"]),
            ("files", world["file"]),
            ("tables", world["table"]),
        ):
            assert (
                _value(conn, f"SELECT public_permission FROM {table} WHERE id = :id", id=object_id)
                == "none"
            ), f"{table} still shares {object_id}"
        assert (
            _value(conn, "SELECT count(*) FROM shares WHERE object_id = :id", id=world["page"]) == 0
        )
        assert _value(conn, "SELECT count(*) FROM skills WHERE id = :id", id=world["skill"]) == 0
        assert (
            _value(conn, "SELECT end_user_id FROM files WHERE id = :id", id=world["file"]) is None
        )

        # The wiki the curator compiled is moved out from under everyone and renamed
        # to what it now is; a fresh protected root takes its place.
        assert (
            _value(
                conn,
                "SELECT name FROM folders WHERE id = :id",
                id=world["root"],
            )
            == f"Shared wiki archive ({world['root']})"
        )
        assert (
            _value(
                conn,
                "SELECT parent_folder_id FROM folders WHERE id = :id",
                id=world["root"],
            )
            is None
        )
        new_root = _value(
            conn,
            "SELECT external_wiki_folder_id FROM workspaces WHERE id = :ws",
            ws=world["workspace"],
        )
        assert new_root is not None and str(new_root) != world["root"]
        assert (
            _value(conn, "SELECT is_protected FROM folders WHERE id = :id", id=new_root) is True
        ), "the rebuilt root has to survive the owner renaming or deleting it"
        assert (
            _value(
                conn,
                "SELECT curation_generation FROM workspaces WHERE id = :id",
                id=world["workspace"],
            )
            == 1
        ), "knowledge compiled before enforcement is only stale if its generation moved"
        assert (
            _value(
                conn,
                "SELECT curated_through FROM agents WHERE id = :id",
                id=world["curator"],
            )
            is None
        ), "the external curator has to re-derive the wiki from scratch"

        # Trunk 0203's half: the curator's Log page is preserved privately, and the
        # shared page that pointed at it stops advertising it.
        assert (
            _value(
                conn,
                "SELECT f.name FROM folders f JOIN pages p ON p.folder_id = f.id WHERE p.id = :id",
                id=world["log"],
            )
            == "Curator log archive"
        )
        assert (
            _value(
                conn,
                "SELECT name || '|' || public_permission || '|' || coalesce(end_user_id::text, 'none') "
                "FROM pages WHERE id = :id",
                id=world["log"],
            )
            == f"Log ({world['log']})|none|none"
        )
        assert (
            _value(conn, "SELECT count(*) FROM shares WHERE object_id = :id", id=world["log"]) == 0
        )
        assert (
            _value(conn, "SELECT content_markdown FROM pages WHERE id = :id", id=world["notes"])
            == "Standup notes for the week.\n"
        ), "only the reference line belongs in the archive"

        # Trunk 0206/0207's half: work marked in flight has no claim row behind it on
        # a database that never had claim columns, so the queues start over.
        assert (
            _value(
                conn,
                "SELECT sync_status FROM user_sources WHERE id = :id",
                id=world["source"],
            )
            == "idle"
        )
        assert (
            _value(
                conn,
                "SELECT extraction_status FROM drive_documents WHERE id = :id",
                id=world["document"],
            )
            == "pending"
        )
        assert (
            _value(
                conn,
                "SELECT locked_at IS NULL FROM drive_documents WHERE id = :id",
                id=world["document"],
            )
            is True
        )

        # A workspace whose customers never opted out keeps its wiki exactly as it is.
        assert (
            str(
                _value(
                    conn,
                    "SELECT external_wiki_folder_id FROM workspaces WHERE id = :id",
                    id=world["bystander_workspace"],
                )
            )
            == world["bystander_root"]
        )
        assert (
            _value(
                conn,
                "SELECT public_permission || '|' || content_markdown FROM pages WHERE id = :id",
                id=world["bystander_page"],
            )
            == "read|public on purpose"
        )

    await _rolled_back(work)


@pytest.mark.asyncio
async def test_convergence_never_archives_the_same_wiki_twice():
    """The second pass must be inert: the column it just added is its own marker.

    Re-running this against a database that already converged — a redeploy, a retry —
    would otherwise archive the wiki the curator is writing into right now and bump
    the generation again, invalidating knowledge that is current.
    """

    def work(conn):
        for statement in FOUNDER_SCHEMA_SQL:
            conn.execute(text(statement))
        world = _seed_founder_world(conn)
        _converge(conn)

        first_root = _value(
            conn,
            "SELECT external_wiki_folder_id FROM workspaces WHERE id = :id",
            id=world["workspace"],
        )
        _converge(conn)

        assert str(
            _value(
                conn,
                "SELECT external_wiki_folder_id FROM workspaces WHERE id = :id",
                id=world["workspace"],
            )
        ) == str(first_root), "the live root was archived a second time"
        assert (
            _value(
                conn,
                "SELECT curation_generation FROM workspaces WHERE id = :id",
                id=world["workspace"],
            )
            == 1
        )
        for pattern in ("Shared wiki archive (%", "Curator log archive"):
            assert (
                _value(conn, "SELECT count(*) FROM folders WHERE name LIKE :p", p=pattern) == 1
            ), f"a second pass created another {pattern}"

    await _rolled_back(work)


@pytest.mark.asyncio
async def test_database_that_ran_trunk_migrations_keeps_its_shares():
    """Prod's shape: nothing to converge, and no permission to revoke anything.

    Trunk `0203`-`0207` already ran here, so whatever a developer has shared since is
    their choice. This is the case a columns-only guard cannot tell apart from the
    founder database, and the reason the data pass is keyed on trunk 0204's column
    rather than on the current state of a tree.
    """

    def work(conn):
        world = _seed_founder_world(conn)
        _converge(conn)

        assert (
            str(
                _value(
                    conn,
                    "SELECT external_wiki_folder_id FROM workspaces WHERE id = :id",
                    id=world["workspace"],
                )
            )
            == world["root"]
        ), "an already-enforced workspace lost the wiki it is sharing"
        assert (
            _value(
                conn,
                "SELECT public_permission FROM folders WHERE id = :id",
                id=world["compiled"],
            )
            == "read"
        )
        assert _value(conn, "SELECT count(*) FROM shares WHERE id = :id", id=world["share"]) == 1, (
            "a share the developer made after enforcement was revoked without them"
        )
        assert _value(conn, "SELECT count(*) FROM skills WHERE id = :id", id=world["skill"]) == 1
        assert _value(
            conn,
            "SELECT coalesce(end_user_id::text, 'none') FROM files WHERE id = :id",
            id=world["file"],
        ) == str(world["customer"])
        assert (
            str(_value(conn, "SELECT folder_id FROM pages WHERE id = :id", id=world["log"]))
            == world["root"]
        )
        assert (
            _value(
                conn,
                "SELECT curation_generation FROM workspaces WHERE id = :id",
                id=world["workspace"],
            )
            == 0
        )
        assert (
            _value(
                conn,
                "SELECT sync_status FROM user_sources WHERE id = :id",
                id=world["source"],
            )
            == "syncing"
        )

    await _rolled_back(work)
