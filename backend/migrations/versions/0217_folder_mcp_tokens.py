"""One durable MCP handle per shared folder.

An MCP URL that names a folder has to survive everything the product does
around it: the client is configured once and then left alone for weeks, so the
URL cannot rotate when the folder is renamed, re-shared with a different list
of people, or republished as a Skill. What an owner expects to kill the URL is
the switch they already have — "Stop sharing" — and nothing else.

That is the opposite of the slug, which is a *name*: it is stable while the
folder is published and disappears when it is unpublished. Names are the right
handle for Skills, whose whole identity is their slug, and the wrong one for
folders, which stay private forever and would then be reachable by guessing.
A folder's handle is therefore an unguessable secret handed out once: 256 bits
of `secrets.token_urlsafe`, kept in its own table so the credential has one
home and one meaning, and never derived from anything a user can enumerate.

One row per folder (the folder is the primary key) because re-minting would
silently break the client every owner already configured. Revocation lives in
`permission_service.get_visibility`, not here: the row keeps the handle alive,
the share switch decides whether it is honoured.

Revision ID: 0217
Revises: 0216
"""

from alembic import op

revision = "0217"
down_revision = "0216"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS folder_mcp_tokens (
    folder_id UUID PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
    token VARCHAR(64) NOT NULL UNIQUE,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS folder_mcp_tokens")
