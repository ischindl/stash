---
"stash": patch
---

A scheduled dispatch that arrives while the agent's run is still going now
steps aside and records nothing. The `skipped_already_running` verdict it used
to write on that lane is gone.

**What users get:** the run-outcome column an operator reads in the console, the
API and the scheduler's history says only what the run itself did. Before, a
copy of a dispatch landing mid-run left that lane reading
"skipped: already running" for as long as the original kept going — a verdict
that had not happened, on a run that had not skipped. It also silenced the alarm
that pages when a run dies without reporting, because an unresolved `started` is
what that alarm watches for and the copy had resolved it early. On the founder's
account on 2026-10-01 that window was 30 minutes (skip written 12:42, the real
run killed by its own 90-minute limit at 13:12) and the curator never paged.

**What was removed:** the `mark_run_skipped(agent_id, "already_running")` write
from the beat task's already-running guard in
`backend/tasks/agent_schedules.py`. `mark_run_skipped` now has exactly its three
`_run_due` call sites — `credits`, `no_credential`, `no_changes` — plus its
definition. Nothing replaces it: the dispatch logs one info line naming the run
it is deferring to and returns. The metering does not change — the tick stays
consumed, no refund, no double-charge — and the single-flight guarantee it was
protecting is untouched and stays pinned by the existing double-dispatch test.

**What still means what it always meant:** the `skipped_already_running` value
itself. The database CHECK constraint keeps admitting it, the frontend outcome
union keeps it, and rows written before this change keep their value and their
old meaning — nothing is rewritten, and the stranded-run sweep still leaves a
lane carrying it alone. The value stops being produced; it does not stop being
readable.

**Why the duplicate existed at all:** the same commit raises the Redis
`visibility_timeout` from its 3600 s default to 7200 s. Tasks are acknowledged
late, so the queue re-offers a running task's message once that window lapses,
and a harness run may legally take 5700 s — the requeue was structural, not a
glitch. The accepted trade-off: a task whose worker genuinely died is retried
after 2 h instead of 1 h, because these runs are paid and non-idempotent.

**Note:** this repo has no tooling that consumes `.changeset/`;
`CHANGELOG.md`'s Unreleased bullet is the authoritative release note. This
file exists because the task framework mandates a removal record for a
net-negative change — the founder may delete it.
