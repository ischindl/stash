"""Enable reward models only for accounts created after this rollout.

Revision ID: 0224
Revises: 0223
"""

from alembic import op

revision = "0224"
down_revision = "0223"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN reward_models_enabled boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE users ALTER COLUMN reward_models_enabled SET DEFAULT true")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN reward_models_enabled")
