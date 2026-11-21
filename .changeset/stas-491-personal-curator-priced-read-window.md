# STAS-491 — a personal curator pass reads what its wall pays for, and commits it

The scoped lane got its fill and its price in STAS-490. This is the other half of the same
wall, on the lane that actually runs against the founder's data every night: the personal
wiki curator, whose five lanes had reported `failed` since 2026-09-01 without moving a single
reading position. None of what follows changes the wall, the ceiling, or the credit.

- **The read was unpriced.** `backend/tasks/agent_schedules.py` handed the personal turn no
  `until` at all unless a history drain owned the read, so `changes_since` arrived with
  `max_event_chars=None` and the model was shown the whole unread delta — 4,396,654 events on
  one founder lane. The digest spent 570s reading, `wait_for` fired, `_ceiling_exceeded` raised
  and `mark_curated` was never reached. The wiki writer inside that same run HAD already
  edited pages from the partial read, so the product visibly moved while the watermark did
  not, and the next night read the same characters again. The plumbing this needed was built
  for STAS-456 and never wired on this path: `run_scheduled(..., until=)` already reaches
  `build_digest_turn`, `curator_changes_cmd` and `curator_window`.
- **One priced part per run.** `curation_service.personal_budget_of` prices the read with the
  same `input_budget` and the same per-route rate as `drain_budget_of`, on a wall reduced by
  the write phase's share: a personal run is TWO model turns inside one wall (digest reads,
  curator writes), which is what `run_digest_turn_under_wall`'s own docstring says the personal
  lane cannot escape. The reserve is 270s — the worst of four `saneca` write-phase sessions
  measured on the founder stack 2026-11-18..21 (176s, 196s, 199s, 268s) — and a wall too small
  to pay it gives the writer half, because a lane that raises on its own cheap wall cures
  nothing.
- **The cut and the commit are one definition.** `curation_service.read_window` returns the
  instant `_budget_kept` keeps up to, and `complete_through` commits against the same budget;
  `until_bound` closes a read with `<=`, so the whole timestamp group at the seam belongs to
  this pass and the next pass' strict lower bound cannot hand it twice. A feed that fits
  answers the tick's `now`: the unchanged run, asserted rather than assumed.
- **The floor it prices against is not the loader's.** `input_budget` grew `floor_chars`
  because `MINIMUM_BATCH_CHARS` (1,024) is a fact about how the scoped loader batches
  documents; a personal lane reads event groups, whose measured size on the founder feed is
  17 characters at the median and 384 at the 90th percentile. Against the loader's floor the
  founder's 570s wall would have *refused* to grant at all (1,024 chars cost 334s at the priced
  0.326s, and only 300s survive the write reserve); against a group floor it grants ~800
  characters and finishes inside its wall. Both callers of the loader floor are unchanged.
- **Both paths get it, because only one of them runs nightly.** `_run_curator_locked` is the
  dispatcher; the beat's own tick reaches `_run_scheduled_locked`, which had the same
  unpriced commit and is the path the founder's 09:58 and 13:42 lanes take.
- **The lane became measurable.** `_log_personal_pass` prints `characters=` (the batch's own
  size, never the budget) and `budget=` once per commit, on the path that runs. Before this the
  personal lane wrote no INFO record at all — which is why the STAS-490 sample table stayed at
  0 rows through a full failed night and the only surviving evidence was a `failed` row.
- **The two sentences that promised the impossible.** `RunCeilingExceeded` claimed "everything
  it had completed is already durable", and the ceiling branch in the dispatcher repeated it.
  That is the scoped lane, which checkpoints per pass; a cut personal run has one turn and no
  mid-run checkpoint, so it leaves nothing. The chain's progress gate is what stops a personal
  wall from becoming a chain of paid retries, and the docstring now says which lane keeps what.

**What this does not do.** It does not clear the backlog: ~800 characters per pass at the
founder's priced rate is roughly 77k characters a day across 96 catch-up passes, against
hundreds of thousands arriving daily — recency for fresh input is STAS-492's job, and the
operator's chosen bound there is recorded on that card. It does not touch
`AGENT_TURN_TIMEOUT_SECONDS`, the drain's read order, `curated_through` written by hand, or the
credit allowance. The proof that matters is one founder lane's `curated_through` moving after a
deploy that moves `refs/heads/deploy/main`, which is an operator leg.
