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

Revision ID: 0214
Revises: 0213
"""

from alembic import op

revision = "0214"
down_revision = "0213"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agents
        ADD COLUMN curator_folder_id UUID REFERENCES session_folders(id) ON DELETE CASCADE,
        ADD COLUMN model_id TEXT
        """
    )
    op.execute(
        "ALTER TABLE session_folders ADD COLUMN wiki_folder_id UUID "
        "REFERENCES folders(id) ON DELETE SET NULL"
    )
    op.execute("DROP INDEX one_curator_per_user_per_wiki")
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
