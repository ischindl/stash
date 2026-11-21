# STAS-490 — a curator pass carries what its wall pays for, priced per route class

A developer curator's pass is sized by `input_budget(run wall, price of a character)`. Three
code facts made the founder's lane drain 43,160 characters in a whole day (40 committed passes
× 1,079 characters, 7.8 seconds of feed position) while 4,669,736 of its input documents sat
behind its watermark. All three are changed here; none of them is the run wall.

- **Fill.** `backend/services/scoped_curation_inputs.py` applied the batch floor
  (`MINIMUM_BATCH_CHARS`, 1,024) to every document it considered, so after the first document
  of a 1,455-character grant the 376 characters left could not start a second and the pass paid
  its fixed 4.7–13.9 s of model turns for one file. The floors are two constants now: the batch
  floor keeps its meaning (it is what a budget prices and what `has_pending` probes) and
  `MINIMUM_DOCUMENT_CHARS` (512, bounded by the widest empty history envelope the schema can
  render) is the smallest slice a document may start. A budget below that raises naming both
  numbers rather than returning an empty batch a caller would read as "nothing pending".
- **Price.** What a character costs was one shipped pair — 95 s of model turn per 291 characters,
  STAS-458's measurement of one box — that no later measurement read back, while the log line
  quoting it reported 0.0367–0.4198 s per character on that same machine. `backend/services/
  curator_calibration.py` now owns it per route class (`anthropic`/`local` × `internal`/`external`),
  seeded where measured (0.32646 hosted, 0.12 on a founder box whose passes ran 0.0367–0.12) and
  thereafter learned from the last 12 committed samples, answering their maximum. An unpriced key
  raises. Migration 0276 adds `curator_pass_samples`. The founder's 570 s wall now grants
  4,500 characters instead of 1,455.
- **Cadence.** `CURATOR_CATCHUP_PASSES` (default 8) replaces a module literal a deployment could
  not raise, so a chain bound is a declared value. It is a cadence bound and never was the
  entitlement guard: `_chain_permitted` still refuses a free lane's next paid pass, and the chain
  dispatches `metered=False`, so the allowance figure will never show this spend.

Two guards keep the learned price from eating itself, because a sample is
`pass_seconds / characters` and every pass pays fixed model-turn cost: a bigger grant measures a
cheaper character, which grants more. `MAX_PASS_INPUT_CHARS` (8,192) caps one pass whatever the
rate says, and the 12-sample window is what lets a slow sample age out instead of latching — a
maximum without a window is a ratchet that winds one way. A pass that consumed its grant and
committed nothing now records its bound (`wall / grant`, never optimistic) on **every** failure
out of the pass body, not only where the ceiling raises; before this, a curator that died before
its first checkpoint taught the price nothing and the next tick recomputed the same grant and
died identically.

The pass meter prints the two numbers separately: `seconds_per_character=` (measured),
`priced_seconds_per_character=` and `price_source=` (`seed`/`measured`).

**What this does not do.** It converts a hopeless rate into roughly keeping up with a day's feed.
At 430k–860k characters per lane per day — the arithmetic this change licenses, not yet measured —
4,669,736 documents is a 1,500–11,000 day job depending on average document size. Clearing three
months of history needs the drain fan-out applied to the scoped lane or more inference, which is a
decision to make after these numbers are read on a running stack. Nothing here touches
`AGENT_TURN_TIMEOUT_SECONDS`, the credit allowance, what a run is metered at, the personal-lane
wall failure (STAS-491), or the drain's read order (STAS-492). The 24-hour reading on the founder
stack is an operator leg, because the deploy that makes it possible moves `refs/heads/deploy/main`.
