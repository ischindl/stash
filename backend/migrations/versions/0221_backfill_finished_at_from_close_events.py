"""Give every session the close it already has evidence for.

`sessions.finished_at` was written by exactly one place — the demo router — so
every real session row read as in-progress forever, even though agent harnesses
have been sending their end-of-session event through ordinary ingestion all
along. Ingestion now consumes that event (see `session_service.CLOSE_EVENT_TYPE`);
this carries the same evidence onto the rows that acquired it before the server
knew what it meant.

Evidence only. The close comes from a close-named event and nothing else: no
close event, no value. The alternative — "the last event is roughly when it
ended" — is the corruption this column exists to avoid. The transcript importer
mints event-shaped rows with historical times, so closing on recency would
fabricate an end time for every imported session; on founder prod that is 1,287
live sessions with event rows and no close of their own, against 7 `session_end`
rows in the entire database.

Forward-only, so this can never make a row worse: `GREATEST` keeps a later close
that ingestion already stamped, and the WHERE leaves untouched rows untouched —
running it twice changes nothing.

Measured read-only against founder prod before writing this: all 7 `session_end`
rows predate July and none of them joins a `sessions` row, so this moves 0
sessions on that deployment today. It is the repair for the databases where the
close rows do join, and the no-op path is what proves the rule above rather than
a guess about it.

Revision ID: 0221
Revises: 0220
"""

from alembic import op
from sqlalchemy import text

from backend.services.session_service import CLOSE_EVENT_TYPE

revision = "0221"
down_revision = "0220"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE sessions s "
            "SET finished_at = GREATEST(s.finished_at, closes.ended_at) "
            "FROM (SELECT owner_user_id, session_id, MAX(created_at) AS ended_at "
            "      FROM history_events "
            "      WHERE event_type = :close_event AND session_id IS NOT NULL "
            "      GROUP BY owner_user_id, session_id) closes "
            "WHERE closes.owner_user_id = s.owner_user_id "
            "  AND closes.session_id = s.session_id "
            "  AND (s.finished_at IS NULL OR closes.ended_at > s.finished_at)"
        ),
        {"close_event": CLOSE_EVENT_TYPE},
    )


def downgrade() -> None:
    # Nothing to undo. Every value this wrote is derived from a close event that
    # is still in `history_events`, so blanking the column on the way down would
    # also erase the closes ingestion has stamped since, and leave the sessions
    # list with less true information than it had before.
    pass
