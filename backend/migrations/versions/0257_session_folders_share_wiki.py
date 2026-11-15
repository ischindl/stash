"""Per-project clearance for the shared wiki.

A developer's workspace holds sessions from many projects, and until now the
only control over what reached the shared (cross-user) wiki was the per-end-user
`share_wiki` toggle — all of a user's history or none of it. A project is a
session folder, so the folder gains the project-level opt-in: sessions filed in
an opted-in folder are cleared for the shared wiki, everything else stays
personal.

Default FALSE is deliberate — a new project contributes nothing to the shared
wiki until the developer clears it on purpose.

The add is conditional, and only that: the founder database carries this column
from the fork's own chain while its stamp sits below this revision, so
convergence runs the statement against a database that already has it. Unguarded
it died with `column "share_wiki" of relation "session_folders" already exists`.
Postgres reads its own catalog to decide, and the column it creates when it is
missing has the same type, default and NOT NULL as before.

Revision ID: 0257
Revises: 0256
"""

from alembic import op

revision = "0257"
down_revision = "0256"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE session_folders ADD COLUMN IF NOT EXISTS share_wiki BOOLEAN "
        "NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.drop_column("session_folders", "share_wiki")
