---
"stash": patch
---

Checkpoint every completed curation pass, and size the slice a pass reads from the run's own ceiling.

**The problem this closes.** The founder stack's external curator had not moved its watermark since
2026-08-22 across 42 runs, each one dying at its 600 s wall (measured 2026-09-23). Spreading the
backlog walk across runs did not fix that, because a run is one wall: it read one batch, and the whole
batch was what it could lose. A batch whose own reading outgrew the ceiling died at the ceiling with
nothing committed, and the next run read the same first slice again, forever.

**What changed.** `scoped_curation_service.run` is now a loop of passes. Each pass loads one
budget-sized batch, reads it across every scope that batch applies to, and moves the durable pair —
`curation_cursor` beside `curated_through`, in the same fenced UPDATE that has always guarded it —
before the next batch is loaded. The most a ceiling cut can now discard is the pass in flight. A pass
that advanced only the cursor half of the pair (the instant stays one microsecond behind the document
still being read) is a checkpoint like any other; a pair that moved nowhere is not written. The loop
ends when a batch stops owing anything, and two shapes of "this budget cannot buy progress" raise
instead of spinning: a batch that owes input but cannot fill one row, and a pass whose pair moved
nowhere while its batch still owed input.

**What was removed:** `_MAX_INPUT_CHARS` and the default `max_chars` on
`scoped_curation_inputs.load_batch`. There is no constant input budget any more — `input_budget()`
derives one whole-run number from the run's own ceiling through `_INPUT_CHARS_PER_CEILING_SECOND`, and
`load_batch` and `load_scope` take their arguments (`max_chars`, `batch=`) as required parameters
rather than loading a slice of their own behind the run's back. Three scopes on one lane read slices
of the one batch; they do not spend three budgets.

**Honest about the number.** `_INPUT_CHARS_PER_CEILING_SECOND` is 333 characters per ceiling-second.
Its denominator is measured — the 600.05 s the founder curator's own mark_run took to reach its wall
on 2026-11-10 — but its numerator inherits the input volume that lane was already being handed, so it
is a calibration carried over from the bound it replaces, not an independent throughput probe. The
measurement that would have priced the numerator live could not be taken: this host holds no model
credential the scoped lane can dial. What bounds the damage of a wrong rate is the per-pass
checkpoint, not that constant.

**What users see when the ceiling does cut a run off:** the run still reports `failed` and the task
still raises, but it now fails as `RunCeilingExceeded`, and that one failure hands the backlog the cut
run did not reach to the next pass of the catch-up chain — with both brakes it always had (the lane has
to have advanced, and the chain has to have passes left), so a cut that checkpointed no pass starts no
chain and no run becomes a chain of paid retries. A chain that runs out of passes with input still
pending logs that instead of returning quietly. The per-run ceiling is also what sizes one pass's
slice now, which is what `docs/running-stash.md` says.

**What did not change:** the ceiling values, `_MAX_TURNS`, `_MAX_CONCURRENT_SCOPES`, the failure
message `Scoped curation exceeded its run time limit`, and the fence itself — `CuratorWatermarkConflict`
still fires when an ingest rewind or an overlapping run moves the pair this pass started from. One
promise the loop nearly broke and keeps: the developer platform's `full_history` backfill still reads
with no watermark. Its dispatcher zeroes the position inside its own copy of the agent while holding
on to the stored pair as the write anchor, and once the loop had read from the anchor instead of from
the row, a backfill would have found an empty window and reported success having curated nothing.

**Two files outside the card's own limit list, said out loud.** The intake told this card to
reach only `scoped_curation_service.py`, `scoped_curation_inputs.py`, `curation_service.py` and
`agent_schedules.py`. Two more carry part of the work and neither was touched quietly:
`sprite_agent_service.py` (12 lines) is where the wall converts its timeout into a failure, and
the catch-up chain needs to tell a run the ceiling ended from a run that broke, so that one site
raises `RunCeilingExceeded(RuntimeError)` with the message text byte-identical — string matching on
the operator-facing sentence and a wall-clock guess inside the scheduler were the rejected
alternatives; and `docs/running-stash.md` (5 lines) states what the ceiling bounds, which is the
thing this card changed. Everything else stayed inside the four.

**One logging change worth naming:** a scope used to push its own `tool_result` event
(`tool_name="curate_wiki"`) as soon as it finished, before the batch was committed. With one push per
pass now publishing alongside that pass's checkpoint, that event went away rather than duplicating
every summary: no reader selected on `tool_name="curate_wiki"` — the curator's own log reads
`assistant_message` events — and a report of work that was not yet durable was the part worth losing.

**One log line per committed pass:** `scoped_curation_service` now writes one INFO drain-rate line
for every pass that committed — pass number, agent, the instant it committed, the characters that
pass's batch carried, its scope count, and that pass's wall — and no line for a pass that committed
nothing. That line is what makes a run the ceiling cut off readable as the passes it actually
finished, and what lets an operator read how fast a lane empties without dialing the model.
