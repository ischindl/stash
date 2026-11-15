"""Give a history drain its own resumable position, separate from the watermark.

Revision ID: 0274
Revises: 0273
"""

from alembic import op

revision = "0274"
down_revision = "0273"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN drain_through timestamptz")
    op.execute("ALTER TABLE agents ADD COLUMN drain_cursor jsonb NOT NULL DEFAULT '{}'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN drain_cursor")
    op.execute("ALTER TABLE agents DROP COLUMN drain_through")
