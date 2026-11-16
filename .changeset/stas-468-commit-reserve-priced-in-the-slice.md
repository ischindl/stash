---
"stash": patch
---

A curator's pass is now priced including the commit it has to make, so no wall can buy a slice that
leaves the checkpoint no time to run.

**The problem this closes.** STAS-458 priced a pass and stopped at the pass. At the deployment default
the run wall resolves to 570 s, and 570 s bought 1,746 characters — 6 turns × 291 — whose calibrated
price is `1746 × 95 / 291` = **570.0 s exactly**, not the 569 s the calibration note in the source
claimed. A slice that costs the whole wall leaves 0.0 s for the `_checkpoint_pass` that runs inside it,
which is the same zero drain STAS-458 exists to end, only smaller: a lane priced correctly still
committed nothing unless the commit happened to cost nothing. The general law said this was not a
rounding accident — the headroom a granted wall kept was `wall mod 95`, whose minimum is 0.0, so every
wall whose remainder fell under the commit's 30 s reserve was already broken, not just multiples of 95.

**What changed.** One quantity, counted once, inside the function that prices the slice. For every
wall `input_budget` answers: `grant = max(int((wall - R) // T) × C, MINIMUM_BATCH_CHARS)` and therefore
`grant × T / C <= wall - R`, where `R = agent_service.RUN_COMMIT_RESERVE_SECONDS`. The refusal reads
the same reserve: the new `_minimum_wall_seconds()` returns the cheapest pass's price *plus* `R`, and
any wall at or below it raises. The threshold and the grant cannot drift apart because they read that
one helper, and it reads `RUN_COMMIT_RESERVE_SECONDS` at call time exactly as the pass price is read
from the calibration pair — no restated number anywhere. The subtraction went inside `input_budget`
rather than at its two callers because it is a fact about the slice, and putting it at the call sites
is the two-sources-of-truth defect this card was filed to remove; both callers already hand in
`agent_service.run_wall_seconds(agent)`, so neither moved, and both lanes moved together.

The headroom law after the fix is `((wall - R) mod T) + R`, whose minimum over every grantable wall is
exactly `R` — reached at wall 600 s, where a 1,746-character slice costs exactly 570.0 s of it. That is
why the ladder's headroom assert is non-strict: written as a strict inequality it reddens for the right
answer.

| wall | before: grant | cost | headroom | after: grant | cost | headroom |
|---|---|---|---|---|---|---|
| 335 | 1,024 | 334.3 | **0.7** | REFUSED | — | — |
| 340 | 1,024 | 334.3 | **5.7** | REFUSED | — | — |
| 355 | 1,024 | 334.3 | **20.7** | REFUSED | — | — |
| 364 | 1,024 | 334.3 | **29.7** | REFUSED | — | — |
| 475 | 1,455 | 475.0 | **0.0** | 1,164 | 380.0 | 95.0 |
| 570 | 1,746 | 570.0 | **0.0** | 1,455 | 475.0 | 95.0 |
| 571 | 1,746 | 570.0 | 1.0 | 1,455 | 475.0 | 96.0 |
| 600 | 1,746 | 570.0 | 30.0 | 1,746 | 570.0 | 30.0 |
| 950 | 2,910 | 950.0 | **0.0** | 2,619 | 855.0 | 95.0 |
| 1,140 | 3,492 | 1,140.0 | **0.0** | 3,201 | 1,045.0 | 95.0 |

*Cost is the grant priced at `T / C`. Bold headrooms are the ones at or under the 30 s the commit
needed. Wall 600 is the only row the fix leaves alone — both laws land on `R` there, which is what makes
it the tightest wall a run can be granted. The post-fix rows of this table are printed by
`test_every_granted_wall_affords_the_slice_it_grants` with `-s`, so the proof lives in the run output
rather than in a throwaway script that imports `backend.database` to reach a pure function.*

**What users see.** Two numbers move, and the honest pairing is this one: at the deployment default a
pass is handed **1,455 characters instead of 1,746** (−16.7 %), and what a run commits goes from zero
to at least one finished pass. Throughput per pass is the cost; the drain is the gain, and the drain is
the number the backlog is measured in. A curator whose **ceiling sits between 365 and 394 s** now fails
its run loudly where it previously ran with 0.7045 to 29.7045 s of headroom — a 365–394 s ceiling is a
335–364 s wall against a floor pass priced at 334.3 s, and the wall could pay for neither that pass nor
its commit. The failure names both prices and the wall that buys them.

**Which number is which.** Three figures sit near each other and only one is a wall: 334.3 s is what
the loader's 1,024 characters cost; 364.3 s is the *wall* that pays for them plus the commit; a row's
*ceiling* must reach about 394 s to resolve to that wall. `docs/running-stash.md`, `CHANGELOG.md` and
`docs/testing.md` said "a ceiling too small to buy even the cheapest pass (~334 s)", which named a pass
price where an operator needed a ceiling — corrected in place. The calibration note's "569 s" is now
570.0 s, with the two measures named apart: the priced measure is the pair `95 / 291 = 0.32646` s per
character and it is what prices a grant, while the sample's rounded 0.326 s/char is an average over a
pass that died mid-flight.

**What did not change:** `RUN_COMMIT_RESERVE_SECONDS`, `run_wall_seconds`, `turn_ceiling_seconds` and
everything else in `agent_service.py` — this card prices the existing reserve, it does not retune it;
`_MODEL_TURN_SECONDS`, `_INPUT_CHARS_PER_MODEL_TURN` and `MINIMUM_BATCH_CHARS`, since a recalibration
needs a model box behind it; `_smallest_pass_seconds()`, which keeps its name and its meaning because
the refusal quotes it as a price, and folding the reserve into it would print 364.3 wall-seconds as the
cost of 1,024 characters, which is a lie. `MEASURED_SECONDS_PER_CHARACTER = 1277 / 10211` is unchanged
in both senses — the literal stays an empirical tripwire, 2.61 times more generous than the priced
measure, and the test now carries a separate headroom assert on `T / C` so nobody mistakes the literal
for the gate. No fallback, clamp, default wall or minimum-wall patch-time validation was added: a wall
that cannot pay for a pass and its commit raises, and the run-time refusal is the only place that
threshold is enforced. `agent_service` is imported locally inside the two helpers, matching how `run`,
`_checkpoint_pass` and `curation_service` already read it, so this card takes no import-cycle risk.

**How the number is kept honest.** The ladder's cost assert went from `<=` to `<` and gained a second
assert that the leftover is at least the reserve, reading `RUN_COMMIT_RESERVE_SECONDS` from the shipped
module where no test may patch it. Both halves are falsifiable and both were measured so: deleting the
reserve from the grant reddens the ladder at wall 475, and deleting it from the guard reddens it at
`gate - 0.1`. Walls 335 and 364 are in the ladder as the boundaries of the newly-refused band — 335 was
the tightest wall the shipped guard admitted, and 364 the highest whole-second wall now refused — so a
gate dialled one second off in either direction cannot pass. The exact gate float is deliberately not a
sample: `gate` is `F + R` and `(F + R) - R` need not return `F` under IEEE rounding, so the exact
boundary is a knife-edge of representation, not a semantic case. One loader test stopped zeroing the
reserve and dials a 395 s ceiling instead, so the tightest wall the suite exercises end to end (365 s, a
1,024-character grant) is production-reachable; the helper's other ten call sites keep zeroing it, and
the unpatched ladder is what judges the reserve's size.

**On the `.changeset/` directory itself:** this repo has no tooling that consumes it. Grepping for
`.changeset` outside the directory finds only `docs/proposals/` prose about needing a removal note,
lockfile entries for an unrelated npm package, and the `stas-299-engine-evidence/` snapshots, which
describe the *engine's* own release-gate checker rather than anything in this repo. These files are
read by people and by the next card, not by a bot.
