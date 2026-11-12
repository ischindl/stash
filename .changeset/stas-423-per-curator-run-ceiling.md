---
"stash": patch
---

The ceiling on a curator run is the curator's own property, not a process-wide
constant.

**What users get:** every curator row in Settings now carries a "Run ceiling" in
seconds. A lane pointed at a slow local model box can be given the room it needs
without handing that room to every other curator, and "Deployment default" hands
that curator back to `AGENT_TURN_TIMEOUT_SECONDS`. The row shows both numbers —
what is stored and what the next run will use — so a curator left without its own
ceiling reads as unset rather than as configured, and a refused save says so on
the row instead of looking like a number that was kept. The saved value needs no
restart: the next dispatch reads it.

**Why it was one number before:** `AGENT_TURN_TIMEOUT_SECONDS` (default 600) was
the wall for every curator run *and* the TTL of the `scoped-curator-{id}` lock
that keeps a second run of the same curator from starting while the first is
still going. The wall was the wrong shape for a founder lane on a 45 s-per-response
box, and the only lever was the environment plus a restart.

**What changed:** one nullable `agents.turn_timeout_seconds`, and
`agent_service.turn_ceiling_seconds(agent)` as the only code in the tree that
names a run's ceiling — the column, or the setting while the column is null. The
scoped lane resolves it once per run and gives that one value to `asyncio.wait_for`
and to the lock TTL, because the two drifting apart is exactly the failure the
single-flight fence (migration 0258) exists to prevent: a lock keyed shorter than
the wall expires mid-run and the next dispatch walks in behind a half-finished run.
PATCH `/api/v1/me/curators/{id}` writes it. The global default stays 600.

**What cannot be saved:** a ceiling at or above what the worker enforces on the
task (`worker_turn_ceiling_seconds()` — the earlier of `task_soft_time_limit`
1500 s and `task_time_limit` 1800 s, read from the celery conf rather than
restated). Such a run is killed by the worker, so it never reaches the chore that
marks it failed and refunds it: the curator's monthly credit stays spent and the
lane keeps reading "started" until the stranded-run sweep notices. The refusal
names the number typed and the live bound, and writes nothing at all — a PATCH
that bundles another field is refused whole, not half applied.

**What deliberately still reads the global constant:** `request_stop`, `_pump_turn`
and `run_chat` — the chat turn lock and its stop flag. Those fence an interactive
session, not a curator lane.
`test_a_scoped_run_never_writes_a_stop_flag_only_a_chat_lane_reads` measures that a
scoped run never touches those keys, whether it finishes or is cut off by its own
ceiling, which is the evidence that leaving them alone is not an oversight.
