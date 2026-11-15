"""Curators become configurable objects: a folder scope and a model pick.

Per-folder curators: a curator can be bound to one session folder — it sees
that folder's feed and writes that folder's wiki. The workspace-level
curators keep folder NULL. One curator per (user, wiki kind, folder) replaces
the old one-per-(user, wiki kind). Deleting the folder retires its curator
with it (CASCADE): the scope the curator existed to serve is gone.

Model pick: agents.model_id names a model within the agent's connected
provider (the local provider's credential carries its probe list; the pick
overrides the credential's default model for this agent's runs).

Wiki home: a project's wiki is a file-tree folder (pages only hang off
`folders`), so session_folders grows the link to it — wiki_folder_id — which
the first folder curator of a project creates and every later one reuses.

Every statement here is conditional in shape, not in meaning, because the founder
database carries all four objects from the fork's own chain while its stamp sits
below this revision: unguarded, the first add died with `column
"curator_folder_id" of relation "agents" already exists`. The old index is dropped
`IF EXISTS` because the founder lineage retired it on its own — it is absent
there, which the containment diff against a golden at this base confirms.
`one_curator_per_scope` is dropped before it is re-created rather than created
`IF NOT EXISTS`, so the surviving index is this file's own definition and
Postgres re-validates the scope rule against every curator row it is about to
cover; on the founder copy the measured `pg_get_indexdef` of the fork's index is
this same statement, so the swap is a no-op in content and a violation fails loud
instead of being inherited.

Revision ID: 0261
Revises: 0260
"""

from alembic import op

revision = "0261"
down_revision = "0260"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agents
        ADD COLUMN IF NOT EXISTS curator_folder_id UUID
            REFERENCES session_folders(id) ON DELETE CASCADE,
        ADD COLUMN IF NOT EXISTS model_id TEXT
        """
    )
    op.execute(
        "ALTER TABLE session_folders ADD COLUMN IF NOT EXISTS wiki_folder_id UUID "
        "REFERENCES folders(id) ON DELETE SET NULL"
    )
    op.execute("DROP INDEX IF EXISTS one_curator_per_user_per_wiki")
    op.execute("DROP INDEX IF EXISTS one_curator_per_scope")
    op.execute(
        """
        CREATE UNIQUE INDEX one_curator_per_scope ON agents
        (user_id, curator_wiki,
         COALESCE(curator_folder_id, '00000000-0000-0000-0000-000000000000'::uuid))
        WHERE is_curator
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE session_folders DROP COLUMN wiki_folder_id")
    op.execute("DROP INDEX one_curator_per_scope")
    op.execute(
        "CREATE UNIQUE INDEX one_curator_per_user_per_wiki ON agents (user_id, curator_wiki) WHERE is_curator"
    )
    op.execute("ALTER TABLE agents DROP COLUMN curator_folder_id, DROP COLUMN model_id")
