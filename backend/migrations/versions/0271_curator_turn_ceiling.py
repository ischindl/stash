"""The run-time ceiling becomes a property of the curator, not of the process.

`AGENT_TURN_TIMEOUT_SECONDS` (default 600) is one env value shared by every
curator, so a lane whose model answers slowly — a founder box serving 45 s per
response — dies at the same wall as a lane whose model answers in 4 s, and the
only lever an operator had changed it for everyone and needed a restart.

The ceiling moves onto the curator as `turn_timeout_seconds`, NULL meaning "the
deployment default applies", so every row that exists today keeps running
exactly as it runs now and no backfill is needed.

NULL is the whole default and it is deliberate: a sentinel (0, -1, a copy of
600) would put the deployment number into row data, and the number would then
stop tracking the deployment that set it.

The same number fences the run twice — it is the `asyncio.wait_for` wall and
the TTL of the `scoped-curator-{id}` single-flight lock (migration 0258). Those
two must never be two different numbers: a lock keyed on a shorter value expires
mid-run, the next dispatch walks into the run still in flight, and both write the
same wiki from different snapshots. That coupling is why this is one column read
once per run rather than two knobs an operator could set apart.

Revision ID: 0271
Revises: 0270
"""

from alembic import op

revision = "0271"
down_revision = "0270"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agents ADD COLUMN turn_timeout_seconds INTEGER "
        "CHECK (turn_timeout_seconds IS NULL OR turn_timeout_seconds > 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP COLUMN turn_timeout_seconds")
