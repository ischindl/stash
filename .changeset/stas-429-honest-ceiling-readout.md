---
"stash": patch
---

A curator row shows a run ceiling only on the lane that actually resolves one.

**What users get:** the "Run ceiling" control and its number stay exactly where they
do work — on a curator whose run resolves a workspace. On the curators that resolve
none, the row now says so instead of quoting a number: "This lane has no per-curator
ceiling; its run is bounded by the worker's own task limit." Nothing else about those
rows changes. The number disappeared from them because it was never theirs: those
runs ignore a per-curator ceiling entirely, so the row was telling an operator to tune
something their runs would never read.

**Why the number was wrong before:** `agent_service.turn_ceiling_seconds` was called
for every curator payload, whatever lane that curator runs on. Only the scoped lane
reads a ceiling — the run resolves it once and hands it to its timeout and its lock. A
folder curator, and every curator of an account with no External wiki, dispatches
without that branch, so its only wall is the worker's task limit. The Settings row
still printed the deployment default (600 by default) beside a control that would have
stored a value nothing consulted.

**What changed:** one rule, applied where an operator meets the number. The lane answer
comes from `scoped_curation_service.workspace_for_agent` — the same call the dispatcher
makes — so the readout and the run cannot disagree: `effective_turn_timeout_seconds` is
a number on a lane that resolves a workspace and `null` on one that resolves none, the
Settings row renders its control only for the number, and PATCH
`/api/v1/me/curators/{id}` refuses a body that names `turn_timeout_seconds` on a lane
that would ignore it, leaving every other field on that row writable as before. The
ceiling itself is untouched: `turn_ceiling_seconds`, the worker's own bound, the stored
column and both run branches mean what they meant. A comment in `llm.py` that repeated
the same claim — naming a scoped-run "outer wall" function that no longer exists — now
describes the single ceiling that number really has.
