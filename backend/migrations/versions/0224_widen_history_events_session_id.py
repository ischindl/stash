"""Widen ``history_events.session_id`` so two-phase curator digest runs fit.

The digest phase of a two-phase curator run mints its session id as
``scheduled_session_prefix(agent) + run_stamp + "-digest"``. The prefix is
``agent-curate-`` plus the curator's UUID plus a dash — 50 characters — so the
nightly beat's minute stamp brings the id to 69 and the manual dispatch's
second-precision stamp (``agent_schedules.py`` runs ``run_scheduled`` with
``%Y%m%d%H%M%S`` precisely so a manual run never shares a session with the
minute's scheduled run) brings it to 71: 50 + 14 + 7 = 71 > 64. The column was
VARCHAR(64), so every digest-phase write died in ``memory_service.push_event``
with asyncpg's ``StringDataRightTruncationError`` before the digest ever read
the feed — every two-phase schedule on the founder stack has been dead since
the digest feature landed (``444f123c``, STAS-263).

Shortening the id instead is not an option: the *non-digest* session id of a
manual run is already exactly 64 characters, so any suffix at all overflows
64, and shortening the digest's stamp would collide with the minute-precision
beat stamp — breaking the documented invariant that a manual run never shares
a session with the minute's scheduled run. Widening the column is the one
change that keeps every id, the prefix, and the runs API's grouping by
``scheduled_session_prefix()`` byte-identical.

128 matches the existing width precedent on this table (``tool_name``) and
leaves headroom for the whole scheme. PostgreSQL widens a varchar typmod as
metadata only — no table rewrite, no data touched — and the NOT NULL
constraint from 0121 is unaffected. The public ingest model
(``HistoryEventCreateRequest.session_id``) is aligned to the same 128 so the
API can never accept what the column would reject;
``test_curator_session_id_width.py`` binds the two limits together.

Downgrade shortens the column back: if any stored session id exceeds 64
characters, PostgreSQL raises ``value too long`` and the downgrade fails loud
rather than silently truncating run history.

Revision ID: 0220
Revises: 0219
"""

from alembic import op

revision = "0224"
down_revision = "0223"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE history_events ALTER COLUMN session_id TYPE VARCHAR(128)")


def downgrade() -> None:
    op.execute("ALTER TABLE history_events ALTER COLUMN session_id TYPE VARCHAR(64)")
