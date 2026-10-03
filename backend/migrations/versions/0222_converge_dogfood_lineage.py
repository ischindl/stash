"""Converge a founder-lineage database onto the trunk schema and data.

The founder (dogfood) image shipped its own chain `0203`-`0209`, which are
re-numbered copies of trunk's `0210`-`0216`. A database it left stamped `0209`
therefore resolved trunk's revisions under founder ids — except for trunk
`0203`-`0207`, which that build never had a number for and so never ran. Nothing
else re-adds what those five migrations did, so the founder instance ran for
weeks against a schema missing nine columns and against shared wikis that trunk
had since moved private data out of. Measured founder-at-`0209` versus
trunk-at-`0216` is exactly this migration's scope:

  * ``workspaces.curation_generation``   (trunk 0204)
  * ``users.developer_platform_only``    (trunk 0205)
  * ``user_sources`` sync claim columns  (trunk 0206)
  * ``drive_documents`` extraction claim  (trunk 0207)
  * the curator-log archival trunk 0203 performed on data, not schema

The nine columns and trunk `0217`'s table arrive as idempotent DDL. The data effects are carried
verbatim from the originals and run only when the database has not had them:
``workspaces.curation_generation`` is trunk 0204's fingerprint and trunk 0203
always ran one revision earlier, so the column being absent *before this
migration adds it* proves the database never saw either. A database that ran
trunk `0203`-`0207` the normal way — production, or a fresh `upgrade head` from
the base — takes the schema no-op path and does no data work at all, which is
what `test_migration_data_convergence` asserts in both shapes.

Two properties of the carried originals are kept on purpose. Trunk 0205's
two-step default (add with `false`, then flip the default to `true`) is what
leaves pre-existing accounts with both surfaces while new signups get the
developer platform only. And trunk 0203 raises on an archive reference found in
a non-markdown page rather than skipping it: that aborts this upgrade loudly,
exactly as it would have aborted trunk's, instead of quietly leaving private
page ids in customer-readable text.

Beware when reading the ids below: `0203`-`0207` are trunk's, and the founder
build's `0203`-`0207` are unrelated (`session_folders_share_wiki` through
`curator_scope_and_model`). `shipped_revisions.json` keys every shipped
revision by `(id, slug)` for the same reason.

Each trunk revision the founder stamp owes is carried or excused, per trunk
revision:

  * `0203`, `0204` — carried here. The founder build had no number for either, so
    its databases have neither the log archival nor `curation_generation`.
  * `0205`-`0208` — already applied under the founder build's own numbers, so
    their data work must not run twice. `0208` was never stamped by anything.
  * `0209`, `0210`-`0216` — the founder chain's own content, which the absorb
    node's ancestry accounts for.

It is numbered `0218` rather than `0217` because trunk booked `0217` for
`folder_mcp_tokens` while this convergence was in flight, and that collision is
not cosmetic: `0209_local_endpoint_endpoints` parents onto `0217`, so alembic
reads `0217` as applied on a database that never ran it. Its table therefore
arrives here too — `SCHEMA_SQL` is the one place allowed to know that a revision
can be an ancestor without having been executed.

Revision ID: 0218
Revises: 0209
"""

import hashlib
import re

from alembic import op
from sqlalchemy import text

revision = "0222"
down_revision = "0221"
branch_labels = None
depends_on = None

# The nine columns trunk 0204/0205/0206/0207 added, plus trunk 0217's table.
# `IF NOT EXISTS` is what makes this list a no-op on a database that already ran
# them, which the founder path never did.
SCHEMA_SQL = [
    # Carried for trunk 0217, which a database stamped 0209 counts as applied
    # without ever having run it — see the header.
    """CREATE TABLE IF NOT EXISTS folder_mcp_tokens (
        folder_id UUID PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
        token VARCHAR(64) NOT NULL UNIQUE,
        created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS curation_generation "
    "integer NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS developer_platform_only "
    "boolean NOT NULL DEFAULT false",
    "ALTER TABLE users ALTER COLUMN developer_platform_only SET DEFAULT true",
    """ALTER TABLE user_sources
        ADD COLUMN IF NOT EXISTS sync_task_id text,
        ADD COLUMN IF NOT EXISTS sync_claimed_at timestamptz,
        ADD COLUMN IF NOT EXISTS sync_started_at timestamptz,
        ADD COLUMN IF NOT EXISTS sync_alerted_at timestamptz""",
    """ALTER TABLE drive_documents
        ADD COLUMN IF NOT EXISTS extraction_task_id text,
        ADD COLUMN IF NOT EXISTS extraction_claimed_at timestamptz,
        ADD COLUMN IF NOT EXISTS extraction_retry_at timestamptz NOT NULL DEFAULT now()""",
]

# Trunk 0204, verbatim. It only touches workspaces whose wiki tree is still
# shared, and rebuilds each one behind a fresh protected root, which is why
# converge() runs the archival ahead of it.
CURATOR_WIKI_REBUILD_SQL = """
    DO $$
    DECLARE ws RECORD; new_root uuid; old_ids uuid[];
    BEGIN
      FOR ws IN SELECT w.* FROM workspaces w
        WHERE w.external_wiki_folder_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM end_users e WHERE e.workspace_id = w.id
                    AND NOT e.share_wiki)
      LOOP
        WITH RECURSIVE tree AS (
          SELECT id FROM folders WHERE id = ws.external_wiki_folder_id
          UNION ALL SELECT f.id FROM folders f JOIN tree t ON f.parent_folder_id = t.id
        ) SELECT array_agg(id) INTO old_ids FROM tree;
        UPDATE folders SET public_permission = 'none' WHERE id = ANY(old_ids);
        UPDATE pages SET public_permission = 'none' WHERE folder_id = ANY(old_ids);
        UPDATE files SET public_permission = 'none' WHERE folder_id = ANY(old_ids);
        UPDATE tables SET public_permission = 'none' WHERE folder_id = ANY(old_ids);
        DELETE FROM shares WHERE object_id = ANY(old_ids)
          OR object_id IN (SELECT id FROM pages WHERE folder_id = ANY(old_ids))
          OR object_id IN (SELECT id FROM files WHERE folder_id = ANY(old_ids))
          OR object_id IN (SELECT id FROM tables WHERE folder_id = ANY(old_ids));
        DELETE FROM skills WHERE folder_id = ANY(old_ids);
        UPDATE files SET end_user_id = NULL WHERE folder_id = ANY(old_ids);
        UPDATE folders SET name = 'Shared wiki archive (' || id || ')', parent_folder_id = NULL
          WHERE id = ws.external_wiki_folder_id;
        INSERT INTO folders (owner_user_id, created_by, name, is_protected)
          VALUES (ws.scope_user_id, ws.scope_user_id, 'External Wiki', true)
          RETURNING id INTO new_root;
        UPDATE workspaces SET external_wiki_folder_id = new_root,
          curation_generation = curation_generation + 1 WHERE id = ws.id;
        UPDATE agents SET curated_through = NULL
          WHERE user_id = ws.scope_user_id AND curator_wiki = 'external';
      END LOOP;
    END $$;
"""

# Trunk 0206 and 0207's queue resets, verbatim. Rows still marked in flight have
# no claim column value on a database that never ran those migrations, so the
# worker discards and re-reconciles them; the new claim columns arrive NULL and
# `extraction_retry_at` defaults to now(), which is what makes skipping these
# UPDATEs on an already-converged database safe rather than merely tolerated.
SOURCE_QUEUE_RESET_SQL = """
    UPDATE user_sources SET sync_status = 'idle', next_sync_at = now()
    WHERE sync_status = 'syncing'
"""

DRIVE_QUEUE_RESET_SQL = """
    UPDATE drive_documents SET extraction_status =
        CASE WHEN extraction_attempts >= 3 THEN 'failed' ELSE 'pending' END,
        locked_at = NULL
    WHERE extraction_status = 'processing'
"""


def upgrade() -> None:
    converge(op.get_bind())


def downgrade() -> None:
    raise NotImplementedError(
        "Restoring curator logs and shared-wiki content that this migration archived "
        "would re-expose private data in customer-readable wikis"
    )


def converge(bind) -> None:
    """Bring a database up to what trunk `0203`-`0207` leave behind, once.

    `bind` is a parameter rather than `op.get_bind()` so the test can run this
    exact function — not a copy of its statements — inside a transaction it
    rolls back.
    """
    converged_already = _has_column(bind, "workspaces", "curation_generation")

    for statement in SCHEMA_SQL:
        bind.execute(text(statement))

    if converged_already:
        return

    archive_curator_logs(bind)
    bind.execute(text(CURATOR_WIKI_REBUILD_SQL))
    bind.execute(text(SOURCE_QUEUE_RESET_SQL))
    bind.execute(text(DRIVE_QUEUE_RESET_SQL))


def _has_column(bind, table: str, column: str) -> bool:
    found = bind.execute(
        text(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first()
    return found is not None


def archive_curator_logs(bind) -> None:
    """Trunk `0203`'s body, carried verbatim (see that file for the rationale).

    Both the current Log and the older _system/changelog contain private
    details: preserve them in an unshared folder, delete their shares, and strip
    reference lines out of the pages that stayed shared.
    """
    workspaces = (
        bind.execute(
            text(
                "SELECT scope_user_id, external_wiki_folder_id FROM workspaces "
                "WHERE external_wiki_folder_id IS NOT NULL"
            )
        )
        .mappings()
        .all()
    )
    for workspace in workspaces:
        pages = (
            bind.execute(
                text(
                    "WITH RECURSIVE tree AS ("
                    "SELECT id, ''::text AS path FROM folders WHERE id = :root "
                    "UNION ALL SELECT f.id, tree.path || f.name || '/' FROM folders f "
                    "JOIN tree ON f.parent_folder_id = tree.id"
                    ") SELECT p.id, p.name, p.content_type, p.content_markdown, p.deleted_at, "
                    "tree.path FROM pages p JOIN tree ON p.folder_id = tree.id"
                ),
                {"root": workspace["external_wiki_folder_id"]},
            )
            .mappings()
            .all()
        )
        logs = [p for p in pages if p["name"].lower() in ("log", "changelog")]
        if not logs:
            continue
        archive_id = bind.execute(
            text(
                "INSERT INTO folders (owner_user_id, created_by, name, public_permission) "
                "VALUES (:owner, :owner, 'Curator log archive', 'none') RETURNING id"
            ),
            {"owner": workspace["scope_user_id"]},
        ).scalar_one()
        log_ids = {p["id"] for p in logs}
        for page in logs:
            bind.execute(
                text(
                    "UPDATE pages SET folder_id = :archive, name = :name, "
                    "public_permission = 'none', end_user_id = NULL, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"archive": archive_id, "name": f"{page['name']} ({page['id']})", "id": page["id"]},
            )
            bind.execute(
                text("DELETE FROM shares WHERE object_type = 'page' AND object_id = :id"),
                {"id": page["id"]},
            )
        # Drop reference lines, not a redirect: customers must not be invited
        # to retrieve a private archive through the developer's credential.
        references = [str(page_id) for page_id in log_ids]
        references.extend(f"/memory/{p['path']}{p['name']}.md" for p in logs)
        refs = re.compile("|".join(re.escape(ref) for ref in references))
        for page in pages:
            if page["id"] in log_ids or page["deleted_at"] is not None:
                continue
            content = page["content_markdown"]
            if not refs.search(content):
                continue
            if page["content_type"] != "markdown":
                raise ValueError(f"Archive reference in non-markdown page {page['id']}")
            cleaned = "".join(
                line for line in content.splitlines(keepends=True) if not refs.search(line)
            )
            bind.execute(
                text(
                    "UPDATE pages SET content_markdown = :content, content_hash = :hash, "
                    "embedding = NULL, embed_stale = TRUE, updated_at = now() WHERE id = :id"
                ),
                {
                    "content": cleaned,
                    "hash": hashlib.sha256(cleaned.encode()).hexdigest(),
                    "id": page["id"],
                },
            )
