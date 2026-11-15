---
"stash": patch
---

A curator pass is now priced in model turns, not in seconds of ceiling, and a wall too small to buy one pass fails loudly instead of draining nothing.

**The problem this closes.** `_INPUT_CHARS_PER_CEILING_SECOND` — 333 characters per ceiling-second —
sized each pass by multiplying the *ceiling* by a rate whose denominator was a wall death. On the
founder's box that handed ~19× more characters to a pass than its run wall could ever carry: the
ceiling is 600 s, the run wall resolves to 570 s, and 333 × 600 = 199,800 characters is roughly 25,000
wall-seconds of work against a 570 s wall — some forty-four walls of reading per pass. The measured result
was exactly what a number that wrong predicts: 42+ consecutive curator runs, each dying at its wall,
each committing nothing, the watermark frozen since 2026-08-22. This card replaced the constant with a
turn price. In one sentence, for the twins this card was cut beside: the pass is
turns-the-wall-affords × characters-per-measured-turn, `input_budget` takes the loop's wall, refuses a
wall that cannot buy the cheapest slice it would return, and no second budget formula is authored.

**What changed.** `input_budget(wall_seconds)` now takes the wall the loop actually works to —
`agent_service.run_wall_seconds(agent)`, the same number `sprite_agent_service` cuts the run at — and
returns `int(wall // T) * C` floored at `MINIMUM_BATCH_CHARS`, where `T = 95.0` seconds per model turn
and `C = 291` characters per turn are shipped constants measured on the founder's own vLLM box at the
operator's OFF reasoning level (samples: 2,255 characters walked in 735.9 s across 21 tool-uses; the
fewest-characters arm binds because it measured the slowest walk of a checkpoint). `turn_ceiling_seconds`
is gone from the budget path entirely; the worker's cap now reaches the slice, because the wall it
bounds does. A wall that cannot buy the cheapest slice `input_budget` would return — any wall at or
below `max(T, MINIMUM_BATCH_CHARS × T / C)`, 334.3 s at the shipped pair — raises naming the bound, the
turn price, the character unit and the loader's 1,024-character floor, which a curator operator reads
as a failed run that says what to dial. The loop also changed shape at its seam: `slowest_pass` seeds
at the calibrated turn price instead of `0.0`, so the pre-pass check bounds the *first* pass with the
same subtraction it already gave the second, and the guard's refuse-set is a superset of the loop's
decline-set by construction (`MINIMUM_BATCH_CHARS ≥ C`) — no wall exists that licenses a pass and then
declines it. It is a guard/refuse relation, not an equality, and the plan's original "same set" claim
is corrected in the tests: `wall <= one turn` declines, but `wall <= smallest pass` refuses, and
between 95 s and 334.3 s those sets differ deliberately, in the guard's favor.

**What users see.** A lane that used to burn every dispatch and commit nothing now either drains or
fails with a named price. The per-pass meter line keeps its six fields in order and gains four: the
`budget=` the pass was cut from, `turn_seconds=` and `calibrated_seconds_per_character=` (the shipped
price), and `seconds_per_character=` — this pass's wall divided by this pass's characters, the price
its content *actually* cost, printed beside the calibration that priced it. A curator whose ceiling is
saved below the cheapest passable wall now reports a failed run naming the calibration instead of an
unbroken streak of silent zero-drain.

**What did not change:** the loop structure, the per-pass checkpoint and its fence, `curated_through`
and `curation_cursor` semantics, ceiling resolution and validation (`turn_ceiling_seconds` /
`run_wall_seconds` were read, not edited), `celery_app.py`, `config.py`, the catch-up chain, the
`Scoped curation exceeded its run time limit` sentence, and the second failure message's `cannot
advance the curation cursor` text operators may have grepped for — its advice clause now says "raise
the curator's run ceiling" because that is the knob that moves the wall. `MINIMUM_BATCH_CHARS` was not
moved or duplicated; it was exported from `scoped_curation_inputs.py`, where it has lived all along,
so the loader's floor can be named by the budget that respects it.

**What STAS-438's changeset said about the rate, and what is now true.** That file's "Honest about the
number" paragraph is kept, corrected in place rather than deleted: its final two sentences — that the
live probe "could not be taken" because the host holds no model credential the scoped lane can dial,
and that "what bounds the damage of a wrong rate is the per-pass checkpoint, not that constant" — were
wrong in the first and falsified by measurement in the second. The probe *can* be taken: a curator
pinned to the owner's own endpoint runs the personal lane on this host and stamps its samples
`lane=personal`; it just had never been taken. And the checkpoint bounded nothing when the rate was
off by 19×, because every pass in flight died uncommitted; what bounds a wrong rate is a price in the
unit the wall is kept in. The card's own claims about loop structure, fence and chain still stand and
are untouched.

**How the number is kept honest.** One test asserts the shipped grant against
`MEASURED_SECONDS_PER_CHARACTER = 1277 / 10211` — the founder lane's own completed pass, 10,211
characters in 1,277 wall seconds, written into the test from the measurement and derived from nothing
it judges; at the shipped 333 that assertion was red by forty-one walls (`189810 × 0.12506 <= 570`),
which is AC2's non-vacuity. A second test dials a row's ceiling to 10,000 s against the worker's
1,500 s cap and spies `load_batch`: the `max_chars` reaching the loader is the wall-sized number
(pre-fix: 3,330,000 against 489,510 — red by 6.8). A ladder from half a turn to twice the founder
ceiling pins that every refused wall is at or below the bound and every granted slice is affordable by
the wall that granted it, and a loader spy proves every granted wall actually starts a pass. If a
future model moves the true rate above ~4,558 characters per 570 s wall, the measurement test reddens
while the constants sit unread — which is the point of a test that cannot be satisfied by editing the
constant.
