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

Measured again on that same database on 2026-10-14, this time for cost rather
than for row count. The identity these numbers belong to is trunk revision
`0248`, slug `backfill_finished_at_from_close_events` — what this file, its
`revision` assignment, and its header lines below all agree on. Saying so is not
ceremony: the renumbering in `3670aa97` moved this backfill off the id it was
reviewed under, and `0221` — the id the work that needed measuring was proposed
as — names an unrelated migration now that origin/main is absorbed (`0221` is
`automatic_jev_evaluation` there; our `folder_mcp_tokens` sits on `0244`). Tests reach this
revision by slug for exactly that reason. None of the figures below describes
`0221`.

The database is the founder dogfood stack — container `stash-postgres-1`,
database `stash`, stamped `0225` — at `history_events` 4,427,213 estimated rows
and 18 GB of total relation size (8,418 MB of heap) against `sessions` 1,905
rows and 2,584 kB, with `shared_buffers` at 128 MB: the table being scanned
cannot be resident.

Method: the shipped aggregate's own `SELECT`, verbatim, under one read-only
`EXPLAIN (ANALYZE, BUFFERS)` at 2026-10-14 07:52:31Z — never this revision's
`UPDATE`, and run exactly once because the host was under curator load.
Planning 5.268 ms, execution 4032.928 ms (of which JIT 270.646 ms), plan
`GroupAggregate <- Sort (quicksort, 25 kB) <- Gather <- Parallel Seq Scan on
history_events`, workers planned 2 and launched 2, 1,485,985 rows removed by the
filter per worker at loops=3 (~4.46 M rows touched), and buffers
shared hit=117 read=1,077,437 — the entire 8.4 GB of heap read off disk. The
filter is `event_type` alone in practice; `session_id IS NOT NULL` is vacuous on
this schema, where that column is NOT NULL.

That figure is a read-only aggregate, not a boot window, and it is a floor. The
statement this revision ships is an `UPDATE`, and PostgreSQL does not hand a
top-level data-modifying statement to parallel workers, so the same scan costs
one process what the leader and two workers took here — between roughly 4 s if
the read stays IO-saturated and roughly 12 s if it turns reader-limited — before
a single row is written. And because the aggregate matched 0 rows on this
database, 4032.928 ms is a lower bound from a run that did no work at all; it is
not a size guarantee for a database where the joins are real.

No boot-time window for this revision was ever captured on that database, and
none can be while things stand: the deployed image's alembic head (`0225`) equals
the database stamp (`0225`), so `init_db()` finds nothing to apply and the boot
on the current pin printed no `Running upgrade` line at all. The
only `Running upgrade 0224 -> 0225` line recorded anywhere is from a developer
worktree's uvicorn against a throwaway database, and its alembic lines carry no
timestamps — it proves the revision runs, not how long it took. A genuine boot
window exists only in a not-yet-migrated environment.

Re-verify the duration there before a full-data deploy, meaning hosted prod
(`joinstash.ai`, Neon `stash-prod`): the multi-user database where close rows do
join sessions rows, which deploys on every merge to `main` and runs migrations
inside `init_db()` under `pg_advisory_lock(715551)` before the connection pool
exists. There, the duration of this aggregate IS cold-start time for the live
service — the backend serves nothing while it runs. Repeat this same read-only
`EXPLAIN (ANALYZE, BUFFERS)` against the shipped `SELECT` before that release.

Index decision, recorded as a decision: `history_events` carries no index on
`event_type`, and none of its four btrees can serve a close-event filter, so the
sequential scan above is not a planner preference but the only plan available. A
~4 s full read is tolerable for a revision that moved 0 rows on the only database
it has run on, so no partial index ships with this revision. Add one on
`(owner_user_id, session_id, created_at)` restricted to close events once a
measured scan on close-bearing data no longer fits the cold-start budget of the
deployment it would stall — not before.

Revision ID: 0248
Revises: 0247
"""

from alembic import op
from sqlalchemy import text

from backend.services.session_service import CLOSE_EVENT_TYPE

revision = "0248"
down_revision = "0247"
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
