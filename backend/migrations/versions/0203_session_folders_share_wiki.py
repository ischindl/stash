"""Per-project clearance for the shared wiki.

A developer's workspace holds sessions from many projects, and until now the
only control over what reached the shared (cross-user) wiki was the per-end-user
`share_wiki` toggle — all of a user's history or none of it. A project is a
session folder, so the folder gains the project-level opt-in: sessions filed in
an opted-in folder are cleared for the shared wiki, everything else stays
personal.

Default FALSE is deliberate — a new project contributes nothing to the shared
wiki until the developer clears it on purpose.

Revision ID: 0203
Revises: 0202
"""

import sqlalchemy as sa
from alembic import op

revision = "0203"
down_revision = "0202"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_folders",
        sa.Column("share_wiki", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("session_folders", "share_wiki")
