"""Give the curator's event feed the index its query actually walks.

`_feed_events` reads one owner's history in (`created_at`, `id`) order and stops at
the per-run budget. No index led that way — the owner-scoped indexes lead with
`owner_user_id` alone, or with `session_id` — so every feed read planned as a
parallel sequential scan of the table plus a top-N sort: 80ms over a founder-scale
317k-row window before the feed learned to dedupe repeated events at all.

Dedupe without this index is worse, not better. A `DISTINCT ON` must order the
whole window by the event identity before the oldest-first limit can apply, which
measured 1945ms with a 28MB disk spill. With it, the same question walks the window
in feed order and stops when the budget is filled, reading 724 rows to do it: 27ms
end to end. The backlog aggregate rides the same range — 12ms once the curator is
caught up, 0.74s for the first-ever run over the entire corpus, which is a report
rather than the nightly path.

One index covers feed, gate, and backlog because all three ask the same question of
the same table: this owner's events, after this instant. The `id` column is what
makes the walk's tie order match the feed's `ORDER BY created_at, id`, so the LIMIT
cuts in the right place rather than mid-timestamp.

Revision ID: 0205
Revises: 0204
"""

from alembic import op

revision = "0205"
down_revision = "0204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_events_owner_created "
        "ON history_events(owner_user_id, created_at, id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_history_events_owner_created")
