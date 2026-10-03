"""Keep inferred feedback separate from human annotations, including abstentions.

Revision ID: 0226
Revises: 0225
"""

from alembic import op

revision = "0226"
down_revision = "0225"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE rm_reward_models ADD COLUMN feedback jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE rm_reward_models DROP COLUMN feedback")
