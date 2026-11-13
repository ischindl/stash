"""Stop the curator credit counters reporting runs that never happened (STAS-412).

The monthly curator allowance is one counter per `agents` row, and until this
card every beat tick charged it BEFORE any gate ran: `mark_run` incremented
`month_run_count` on its way in, and the tick that then resolved as
`skipped_credits`, `skipped_no_credential`, or `skipped_no_changes` wrote only an
outcome and refunded nothing. A lane whose feed was empty, or whose allowance had
just been spent, therefore kept buying ticks it never worked — the counter grew
while the platform did no curation and spent no tokens.

The code is fixed in the same card (the beat now charges only the run it
dispatches, and one predicate decides whether a run is metered at all), so the
defect stops here. What the code cannot do is repair the numbers it already
recorded, and the surface this card adds makes those numbers visible: the curator
endpoints now hand the client the count of runs spent and Settings prints
"N of M free runs used this month." Printing a number that overstates the work is
the same class of lie this card exists to remove.

Measured on a copy of production (schema 0270, November window, read-only) before
this file shipped: of 33 curator rows, 28 sat at or past the allowance with an
anchor naming the current month, and 27 of those sit above their own evidence —
272 runs claimed that the month's run sessions do not show. 26 of the 27 belong
to synthetic proof and fixture addresses (`example.invalid`, `proof.local`,
`p.local`, `test.local`, `dogfood.stas254.local`, `example.com`, and two rows with
no address at all) whose lanes opened no run session this month; the one real row
is the founder's `rozvrh` lane, counted at 22 against the 19 distinct run stamps
November holds. The founder's other lanes run the other way — `RunFusion` is
counted at 16 against 32 stamps — and are left exactly as they are, because this
pass never raises a count. Whether a lane's refusal is even in force depends on
deployed settings this copy cannot show (`INTERNAL_DOMAINS_FREE_PRO`,
`FREE_CURATOR_RUNS_PER_MONTH`), so no claim is made here about who was blocked;
what is measured is that the counters state more work than the sessions record.

The repair is evidence, and the evidence is the one per-run record the schema
holds: a run writes its own session under this lane's prefix, and a tick that
never dispatched writes nothing at all (`sprite_agent_service.scheduled_session_prefix`
+ a run stamp, the same pairing migration 0269 reads). That is the source of truth
this pass counts against, stated rather than assumed: `sessions` is the table whose
row IS a run, while `history_events` is the event grain underneath it — migration
0269 is the precedent that pairs them (`e.session_id = s.session_id`) precisely
because it needs the events to tell a finished run from an abandoned one. A credit
repair needs the run grain, not the event grain, so it counts one session per run
stamp and folds the digest phase's own session back into its writer's stamp.

So, per curator row whose anchor names the CURRENT month:

* lower the counter to the number of distinct run stamps the month holds, and
* only when the stored counter is both past the allowance and above that evidence.

Never raise a counter — an allowance nobody earned is not this pass's to give, and
a lane whose sessions were deleted loses only the claim that overstates it, never
one it can prove. Never touch a row whose anchor is from a past month: the reader
(`agent_service.month_runs_used`) already reports that period as zero spent, so a
stale row blocks nobody and prints nothing. Never touch a row at or below the
allowance: it is not refused by the gate, and this pass exists to unblock and to
un-lie, not to re-audit honest counts.

The allowance itself is read from `settings.FREE_CURATOR_RUNS_PER_MONTH` rather
than frozen here, because a clamp pinned to a numeral the operator may have raised
would refuse runs against a cap this deployment does not hold. Pro, internal, and
enterprise accounts are not special-cased: their plan lifts the gate, and the
predicate above is evidence-based, so a Pro lane that really did work 22 runs
keeps all 22 and a Pro lane that did not is corrected the same way as any other.

The prefix is frozen as a literal for the same reason 0269 freezes its own: a
migration is a dated artifact, and the test seeds through the service function, so
a spelling change reddens the test instead of quietly stopping this from matching.

The pass is forward-only and idempotent: after the write the stored counter is at
or below the evidence, so a second run of this file changes no row.

Revision ID: 0273
Revises: 0272
"""

from alembic import op
from sqlalchemy import text

from backend.config import settings

revision = "0273"
down_revision = "0272"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE agents a SET month_run_count = worked.runs_recorded "
            "FROM ("
            "  SELECT a2.id,"
            "         COUNT(DISTINCT regexp_replace("
            "             substr(s.session_id, LENGTH('agent-curate-' || a2.id::text || '-') + 1),"
            "             '-digest$', '')) AS runs_recorded"
            "    FROM agents a2"
            "    LEFT JOIN sessions s"
            "      ON s.session_id LIKE 'agent-curate-' || a2.id::text || '-%'"
            "     AND s.started_at >= date_trunc('month', now())"
            "   WHERE a2.is_curator"
            "   GROUP BY a2.id"
            ") worked "
            "WHERE a.id = worked.id "
            "  AND a.month_run_anchor >= date_trunc('month', now())::date "
            f"  AND a.month_run_count >= {settings.FREE_CURATOR_RUNS_PER_MONTH} "
            "  AND a.month_run_count > worked.runs_recorded"
        )
    )


def downgrade() -> None:
    # Nothing to undo, and undoing it would be the wrong direction anyway: the
    # values this wrote are counts of sessions that exist, and restoring the
    # pre-pass numbers would put the overstated count back into the row — the
    # exact reading this card exists to retire.
    pass
