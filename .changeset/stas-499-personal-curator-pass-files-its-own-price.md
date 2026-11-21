# Personal curator passes now write the calibration row the scoped lane alone was writing

`curator_calibration.observe()` had exactly two call sites, both in
`scoped_curation_service.run`, so the personal lane filed no sample and
`curator_pass_samples` stayed empty on the founder stack (verified: 0 rows on
2026-11-21). `price()` therefore answered `("seed")` for every personal run ever
dispatched, and `personal_budget_of` — which sizes the reading a personal digest is
handed — divided a constant nobody had measured by a wall that had been measured.

Both shapes of a personal pass now teach, in `backend/tasks/agent_schedules.py`:

* a pass that committed files the characters it actually read, the same figure
  `_log_personal_pass` prints, so the meter line and the row cannot disagree;
* a pass the wall cut committed nothing and files the GRANT over the wall it spent,
  which is a lower bound on the cost of a character and can only shrink a later grant.

The two dispatch paths are separate functions (`_run_curator_locked`, manual and
catch-up; `_run_scheduled_locked`, the beat's nightly tick) and both are wired. The
first version of this change touched only the manual one and its own test caught it:
the tick is the path that actually cures founder lanes, and it is the one the sample
was written on.

Priced at what a founder pass cost (778 s over 1 735 characters = 0,45 s/char against a
0,12 seed), this shrinks what the lane is handed to roughly a quarter. That is the point
of the row: the price becomes a measurement of this lane's box rather than a number that
came from a different route and a smaller feed.
