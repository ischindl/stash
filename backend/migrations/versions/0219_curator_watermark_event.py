"""The event half of the curator's watermark position.

`curated_through` could name only an instant, and a feed read is ordered
`(created_at, id)`. When one instant held more distinct events than the feed
budget, the watermark had no way to stand *inside* it: the old advance stepped
to `T - 1µs`, which lands behind the whole tie, so every later run re-read the
same first-budget window and the backlog never drained (the tie plateau,
STAS-235). The watermark is therefore a position — the instant plus one event id
at that instant meaning "read through this event" — and this column is its event
half.

NULL is not unset and not a placeholder for zero: it means the instant alone
carries the position, i.e. every event at that instant is behind it. That is
exactly what every stored watermark meant before this column existed, so no
backfill is needed — each existing value is already a complete position under
the new reading, and a data migration would invent information nobody claimed.

The CHECK rejects the pair no code may hold: an event id with no instant is not
a position that bounds nothing, it is a half-written value, and a writer that
persists it has lost the instant it was standing inside. Failing there is why
the two columns can be read as one value everywhere else.

There is deliberately no foreign key to `history_events`. The id is a cursor
marker into one owner's ordering at one instant, not a reference to live
material: history is prunable, and the watermark must stay readable (and the
pair comparable) after the row it names is gone.

Revision ID: 0219
Revises: 0218
"""

from alembic import op

revision = "0219"
down_revision = "0218"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN curated_through_event_id uuid")
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_curated_through_pair_check "
        "CHECK (curated_through IS NOT NULL OR curated_through_event_id IS NULL)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agents DROP CONSTRAINT agents_curated_through_pair_check")
    op.execute("ALTER TABLE agents DROP COLUMN curated_through_event_id")
