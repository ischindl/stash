"""Two-phase curator runs: a digest model reads the feed, the curator writes the wiki.

agents.digest_provider / digest_model_id name a second, usually faster and
cheaper model for the read half of a curator run. When digest_provider is set,
a run is two phases: the digest model consumes the raw delta and reports
extracts, and the curator's own model curates the wiki from those extracts
instead of rereading transcripts. When it is NULL the curator runs the single
phase it always has. The split pays on a long window — the expensive model
sees compressed extracts, not 500 raw events.

Revision ID: 0208
Revises: 0207
"""

from alembic import op

revision = "0208"
down_revision = "0207"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN digest_provider TEXT, ADD COLUMN digest_model_id TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN digest_model_id, DROP COLUMN digest_provider")
