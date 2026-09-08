"""Reconcile the DBs that were stamped by the dogfood image's migration chain.

The founder's stack ran a locally-built image whose migration chain had its own
`0203` (adds `user_agent_credentials.models_json_enc`) and its own `0204`
(session folders `share_wiki`, backfilled opted-in). The DB is therefore stamped
`0204` — but that string refers to *different content* than main's `0204`, so
`alembic upgrade head` skips main's `0204` as "already applied": the widened
run-outcome CHECK never lands, and the first contended curator dispatch would
write `skipped_already_running` into a CHECK that does not allow it.

This revision is the single forward path for both lineage types:

- founder DB (dogfood-stamped): the CHECK is rebuilt with the widened set, which
  is the content main's `0204` was supposed to apply; `models_json_enc` already
  exists, so the column add is a no-op. `share_wiki` and its opted-in
  backfill are dogfood user data — this migration does not touch them.
- fresh/main DB: gains `models_json_enc` (main's routers and `agent_auth` store
  the pi models.json override against it via raw SQL), and the CHECK rebuild is
  a no-op against the set main's `0204` already applied.

Downgrade restores main's pre-STAS-186 six-value set and drops the column; it is
written for the main-side ledger, not for the founder's DB (that one's history
predates this file and is never rewound).

Revision ID: 0206
Revises: 0205
"""

from alembic import op

revision = "0206"
down_revision = "0205"
branch_labels = None
depends_on = None

_WIDENED_SET = """
    'started', 'ran', 'failed',
    'skipped_credits', 'skipped_no_credential', 'skipped_no_changes',
    'skipped_already_running'
"""

_ORIGINAL_SET = """
    'started', 'ran', 'failed',
    'skipped_credits', 'skipped_no_credential', 'skipped_no_changes'
"""


def _replace_check(allowed: str) -> None:
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_last_run_outcome_check")
    op.execute(
        f"ALTER TABLE agents ADD CONSTRAINT agents_last_run_outcome_check "
        f"CHECK (last_run_outcome IN ({allowed}))"
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_agent_credentials ADD COLUMN IF NOT EXISTS models_json_enc bytea NULL"
    )
    _replace_check(_WIDENED_SET)


def downgrade() -> None:
    _replace_check(_ORIGINAL_SET)
    op.execute("ALTER TABLE user_agent_credentials DROP COLUMN IF EXISTS models_json_enc")
