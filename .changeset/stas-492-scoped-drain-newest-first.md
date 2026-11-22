# The developer curator's backlog is now read newest window first, so a fresh upload is not queued behind four years

`run_scoped_curation` had one reading order: from the position the lane is live on, upward,
one budgeted batch at a time (`scoped_curation_inputs.load_batch(…, since=…)`, ordered by
`created_at ASC`). On a lane whose backlog is deeper than its monthly turn budget can absorb
that order is a queue that never reaches the present, and the founder stack's developer lane
measured exactly that on 2026-11-22: 4 813 383 eligible rows standing behind a watermark of
2026-08-10T13:21:54.812999+00:00, oldest eligible row 2026-07-15T10:38:03.493+00:00, nine of
ten recent days spending their whole turn budget (month_runs 9/10). A document uploaded today
was read after all of that, which is to say: not this quarter.

The scoped lane now descends instead of climbing, one bounded window per pass, and publishes
each window the moment it finishes it:

* the run cuts a **head cell** — `(stored_top, now]`, the fresh ground above the seam it
  captured — and works it first, so the first committed pass of a run with a four-year backlog
  carries the document uploaded this morning;
* below the seam it walks **descent cells** of `CURATOR_DRAIN_PART_HOURS` (24 by default),
  `(descent_to − H, descent_to]`, from the newest end downward. `descent_to` starts at the seam,
  and the seam is `drain_seam_target` anchored ON the floor, so `seam = floor + k·H` and every
  cell closes on that congruence: `part_id_of` truncates to the second, and a drifted close would
  name the same id as its neighbour two days later. Walking down by `H` reaches the floor by
  arithmetic rather than by a clamp;
* a finished window is PRUNED, not rested in `done`: what a descent's next run asks is where
  to read next, not which pages were written.

`drain_through` on this lane is derived as `max(published, the surviving slots' through)`, and
`published` is seeded at the live `curated_through` — never at the seam. That is the difference
between a watermark that names read ground and one that names an intention: a plan's seam is a
guess at the newest end, so hand-offs that began at `seam` would move `curated_through` to
ground no committed pass ever read, and `finish_drain`'s monotone clamp only refuses a proposal
behind the live watermark, so nothing downstream would have caught it. Both `advance_drain`
fences are needed and neither replaces the other: the per-slot fence asks whether one pass
stopped reading ahead of where it committed, and the new column-level fence asks whether the
aggregate lowered, which no slot fence can see because pruning the published slot removes the
maximum the column was standing on.

Nothing about fairness was invented here. A run works at most one cell above the seam and then
descends, so a head window wider than its budget defers its remainder to the next dispatch
instead of starving the past; and a run never reads a position twice — the read set of one
descent is exactly the eligible rows above its floor, partitioned into windows, in the opposite
order from the one the incremental read would have used.

One law is worth stating because it is the one a reader is most likely to get backwards: reading
input publishes it and does not make it ineligible. `has_input` is the loader's own UNION with no
memory of what a committed pass already curated, so both the retirement gate and the beat's cost
gate ask only the ground the lane still owes — an open window, a cell below `descent_to` that no
cutter has opened, or input above the plan's top. Asked instead across the plan's whole span
`(floor, top]`, the probe returns true for the rest of the descent's life the moment one window
is published: the pair never retires, `curated_through` stays at the pre-descent instant while the
lane keeps working, and every due tick spends a metered run reading nothing until the monthly
allowance is gone.

Two readings worth having before anyone measures a lane off this. The bound it buys is one
dispatch, not one night: fresh input is read by the run whose head cell contains it, and the
committed pass that follows that head is the run's next successful pass, so the first run of a
descent can legitimately publish nothing. And the descent's last cell runs short to the floor
instead of re-anchoring the grid — its id names its own close, and there is nothing below the
floor for that id to collide with, which is the historical clamped-window failure this lane does
not inherit.
