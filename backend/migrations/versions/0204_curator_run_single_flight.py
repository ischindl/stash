"""Teach the run-outcome check the fourth designed skip.

`last_run_outcome` is a closed set: the column's CHECK constraint is the
allowlist, so a scheduler gate that resolves a tick without running has to name
an outcome the database already knows. The live curator was dispatching twice
per agent — two turns overlapping on one agent, each writing a watermark
computed from the snapshot it read at start, so the slower run discarded the
faster run's finished curation. A dispatch that arrives while an agent's run is
in flight now resolves as `skipped_already_running` instead of stacking a second
turn onto it, and that outcome needs to be storable.

The existing values stay untouched; this only widens the set by one.

Revision ID: 0204
Revises: 0203
"""

from alembic import op

revision = "0204"
down_revision = "0203"
branch_labels = None
depends_on = None

_OLD_SET = """
    'started', 'ran', 'failed',
    'skipped_credits', 'skipped_no_credential', 'skipped_no_changes'
"""

_NEW_SET = """
    'started', 'ran', 'failed',
    'skipped_credits', 'skipped_no_credential', 'skipped_no_changes',
    'skipped_already_running'
"""


def _replace_check(allowed: str) -> None:
    op.execute("ALTER TABLE agents DROP CONSTRAINT agents_last_run_outcome_check")
    op.execute(
        f"ALTER TABLE agents ADD CONSTRAINT agents_last_run_outcome_check "
        f"CHECK (last_run_outcome IN ({allowed}))"
    )


def upgrade() -> None:
    _replace_check(_NEW_SET)


def downgrade() -> None:
    _replace_check(_OLD_SET)
