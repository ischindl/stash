"""Close the scheduled-run sessions whose run is provably over (STAS-303).

`sessions.finished_at` takes its value from a close-named event and nothing else
(`session_service.CLOSE_EVENT_TYPE`; the one SQL write site is `upsert_session`).
That rule is right for a transcript some client is still writing, and it was
never reachable for a server-created run: a curator or scheduled-agent run has no
plugin, its harness lives on a sprite, and the dispatcher writes no history events
at all (`tasks/agent_schedules.py` meters `curator_runs`/`agents` only).
`run_scheduled` now emits the close for the runs it finishes; this carries the
same decision onto the rows those runs left behind — the founder stack holds 899
of them, over half its session table, each reading "still running" forever.

Evidence only, and only evidence that says *no further event can arrive* — the two
forms stored rows already offer:

* **E1 — the run recorded its terminal message.** Its event stream carries an
  `assistant_message`. Each run writes its own per-run-stamped session
  (`sprite_agent_service.scheduled_session_prefix` + a seconds stamp) and pushes
  exactly one final text into it — the harness's answer, the stopped note, or the
  failure marker — which is the terminal fact the reader surfaces already derive
  (`routers/agents.py:108-126`, `routers/curator_log.py:56-69`).
* **E3 — the server let a later run of the same lane start.** Another session of
  that lane (the id with its optional `-digest` phase and its trailing numeric run
  stamp removed) whose first event is later than this session's last one. The lane
  is single-flight: `agent_run_lock` admits one run per agent, and its TTL is the
  server's own declared lease for a dead run — "a worker killed before its
  `finally` releases the lock on this TTL rather than wedging the agent's schedule
  forever" (`tasks/agent_schedules.py:48-52`). A successor therefore could only
  start once this run's lease was released by the run ending, or expired; and by
  that same declaration an expired lease means the run is over.

What is deliberately NOT evidence: silence. The harness may legally run 95 minutes
(`HARNESS_TIME_LIMIT`) and the reaper only strikes a lane past 6,600 seconds
(`REAP_STRIKE_AGE_SECONDS`), so a quiet session is inside the contract, not a
finished one. `last_event_at` becomes a close instant only for a session this
migration has already proved finished by E1 or E3 — never on its own.

The closed value is the session's own `last_event_at`: the instant its last
recorded event was stored, which is also what the run list displays as the run's
end (`MAX(he.created_at)`), so the column and the display cannot disagree.

Scope is the two server-run prefixes (`agent-curate-`, `agent-sched-`, as spelled
by `sprite_agent_service.scheduled_session_prefix`) — frozen here as literals
because a migration is a dated artifact; the test seeds through the service
function, so a spelling change reddens the test rather than quietly stopping this
from matching. Other families are not this migration's to decide: a `fusion-task-*`
session closes when its pusher says so, and an interactive session is resumable.
`finished_at IS NULL` keeps every close ingestion already stamped, so the pass is
forward-only and running it twice changes nothing.

Revision ID: 0226
Revises: 0225
"""

from alembic import op
from sqlalchemy import text

revision = "0226"
down_revision = "0225"
branch_labels = None
depends_on = None

# The lane a run-session id belongs to: no phase suffix, no run stamp. Two runs of
# one agent share a lane; the digest phase shares the writer run's lane.
_LANE = "regexp_replace(regexp_replace({column}, '-digest$', ''), '-[0-9]+$', '')"


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "UPDATE sessions s SET finished_at = s.last_event_at "
            "WHERE s.finished_at IS NULL "
            "  AND s.last_event_at IS NOT NULL "
            "  AND (s.session_id LIKE 'agent-curate-%' OR s.session_id LIKE 'agent-sched-%') "
            "  AND ("
            # E1 — the run recorded its terminal message.
            "    EXISTS (SELECT 1 FROM history_events e "
            "            WHERE e.owner_user_id = s.owner_user_id "
            "              AND e.session_id = s.session_id "
            "              AND e.event_type = 'assistant_message') "
            "    OR "
            # E3 — the server let a later run of the same lane start.
            "    EXISTS (SELECT 1 FROM sessions later WHERE "
            "            later.owner_user_id = s.owner_user_id "
            "            AND later.session_id <> s.session_id "
            f"            AND {_LANE.format(column='later.session_id')} = "
            f"                {_LANE.format(column='s.session_id')} "
            "            AND (SELECT MIN(h.created_at) FROM history_events h "
            "                 WHERE h.owner_user_id = later.owner_user_id "
            "                   AND h.session_id = later.session_id) > s.last_event_at)"
            "  )"
        )
    )


def downgrade() -> None:
    # Nothing to undo. Every value this wrote is the last instant the run itself
    # recorded, so blanking it on the way down would also erase the closes
    # ingestion has stamped since, and leave the sessions list reading a finished
    # run as still running — less true than the state it replaced.
    pass
