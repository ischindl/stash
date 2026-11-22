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
* a pass the wall cut committed nothing and files the GRANT over the seconds its own
  reading spent, which is a price of the read: it can raise a later grant to the 8 192
  ceiling, and it cannot shorten the writer that filled the wall.

The two dispatch paths are separate functions (`_run_curator_locked`, manual and
catch-up; `_run_scheduled_locked`, the beat's nightly tick) and both are wired. The
first version of this change touched only the manual one and its own test caught it:
the tick is the path that actually cures founder lanes, and it is the one the sample
was written on.

Priced at what a founder pass cost (778 s over 1 735 characters = 0,45 s/char against a
0,12 seed), this shrinks what the lane is handed to roughly a quarter. That is the point
of the row: the price becomes a measurement of this lane's box rather than a number that
came from a different route and a smaller feed.

**Who cut the run is what gets filed.** One shape of unfinished is not a measurement of
anything, and this change refuses it at the gate rather than with a threshold:
`RunCeilingExceeded` now carries `by_user`, the fact `_wall_deadline` exists to hand back
and which used to be folded into a sentence at one call site and discarded as `_cut` at
another. A turn a human stopped spent its seconds on a feed it abandoned — five such
seconds over a 2 700-character grant would have taught 0,0011 s per character, and because
`price()` takes the MAXIMUM of its window that one row would have handed the next pass the
8 192-character ceiling. Stopped turns now file nothing on either personal body, while
still reporting `failed` and alerting exactly as before.

**Two readings of the row worth having before anyone prices off it.** A committed pass's
seconds are the whole turn — digest read AND wiki writer, one wall over both — while
`personal_budget_of` subtracts the 270 s `PERSONAL_WRITE_RESERVE_SECONDS` from the wall
before dividing by that same rate, so the writer is charged twice and that row is dearer
per character than a digest-only measurement would be. And the key is the one the grant
itself was priced at, `route_class:curator_wiki`, which the scoped lane shares: a personal
row prices the scoped lane's passes too, and a max moves both lanes together.
