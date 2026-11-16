---
"stash": patch
---

Bound the whole personal curator turn with one wall, and make an abandoned turn commit nothing.

**The problem this closes.** STAS-457 made the personal-lane wall arrive as a wall instead of the
`aclose()` error, but two things still stood between that lane and a run that ends inside its own budget
and leaves durable progress. First, the wall STAS-447 gave this lane bounded only the curator's write
turn. `_run_scheduled_turn` ran the digest phase — the local-model curator reading the wiki corpus into
extracts — *outside* the wall, then wrapped only the second (`run_chat`) phase. A slow digest was therefore
free to spend the whole budget before the write ever began, and on the founder's own box a digest alone
outran the worker's ceiling, so `SoftTimeLimitExceeded` landed outside the coroutine, where no `except`
can resolve the lane, refund its credit, or continue its chain. The wall was real for the phase it wrapped
and absent for the phase that ate the clock. Second, an abandoned turn was success-shaped: `run_chat` maps
`TurnStopped` to `STOPPED_NOTE` and returns *normally*, so a stopped personal turn fell through the
dispatcher to `complete_through` → `_commit_personal_drain` → `mark_run_succeeded` — advancing the drain
over a feed nobody finished reading and reporting `ran`. Nothing downstream separated "returned text" from
"abandoned."

**What changed.** `_run_scheduled_turn`'s personal branch is now one coroutine, `_curator_turn()`, that
covers digest *and* write, bounded by a single `asyncio.wait_for` over the run's own wall plus a
`_WALL_RESCUE_GRACE_SECONDS` rescue. The cut is driven *cooperatively*: at the wall a deadline task calls
`request_stop(in_flight_session)`, the same flag a user's Stop presses, and `_stoppable` ends the turn on
its own on the next poll — so the normal exit runs `run_chat`'s whole teardown with the generator's frame
already released (STAS-457's reap). The outer `wait_for` remains only as a rescue for a turn that ignores
the flag, and both exits raise the same `RunCeilingExceeded`. The digest now checks the stop flag after
reading and returns `STOPPED_NOTE` before starting the write phase, so a wall that spent itself on the
digest does not begin a phase it cannot finish. At the shared choke point (`_run_turn` → `run_scheduled`,
reached by both the personal and scoped dispatchers), a turn that comes back `STOPPED_NOTE` now *raises*
`_ceiling_exceeded(...)` instead of returning, so an abandoned turn reaches none of the commit path.

**What an operator sees.** A cut or stopped personal run reports `last_run_outcome='failed'` with a cause
that names the unread feed and the position it stopped at (a draining run names its drain's committed pair;
a freshly-seeded one says it reads from the feed's start), and its `drain_through`/`drain_cursor`/
`curated_through` are byte-identical to where the dispatch found them. It does not report `ran`. The
personal lane also writes its own `curator pass=1 agent=… committed_through=… lane=personal` line for each
committed drain pass — previously `curator pass=` was scoped-only, so a personal run that *was* committing
passes looked, in the worker log, identical to one committing none. The line deliberately omits `budget=`
and `seconds_per_character=` because the personal lane is not sized or counted in characters the way the
scoped batch is, and its prefix never contains `curator <id> turn `, so it cannot collide with the turn
bracket's record filter.

**What did not change, and could not be allowed to.** No wall was raised, lengthened, or disabled — the
fix bounds the *same* wall to more of the turn, it does not move it. `run_wall_seconds`,
`RUN_COMMIT_RESERVE_SECONDS`, the worker ceilings, the watermark fence, and the scoped lane's own message
(`Scoped curation exceeded its run time limit`) all stand. STAS-457's `_release_pending_pull` reap is the
mechanism this card's cooperative exit leans on, not something it replaces. Pass sizing is STAS-458's turn-
priced `input_budget` (95 s per 291-character turn), consumed by both lanes — this card did not touch it.

**Tests.** `test_curator.py` gains `test_a_personal_wall_cut_arrives_as_a_cut_not_a_cleanup_error` (drives
a turn that never finishes inside a tiny wall; asserts a clean `RunCeilingExceeded` naming the unread feed,
no `aclose` anywhere, the drain/watermark pair unmoved, and every redis key released; a second scenario
disables the cooperative stop so only the outer `wait_for` cancels, exercising the `finally` reap),
`test_a_stopped_personal_curator_turn_commits_nothing` (a turn that returns `STOPPED_NOTE` over an in-flight
drain commits nothing and reports `failed`), and `test_a_committed_personal_drain_pass_prints_the_meter` (the
positive control: one committed drain pass prints exactly one personal `curator pass=` line and moves the
pair). Removing the `finally` reap turns the second scenario back into `RuntimeError: aclose(): asynchronous
generator is already running`; removing the wall makes the cut test report `DID NOT RAISE`. Both reds are
recorded in the card's `ac-d-red-runs` document.

**Measured on the base, and what is deferred to the operator.** On this branch's base the intake's aclose
error no longer reproduces — STAS-457 closed it — and the clock-eater is the un-walled digest phase (400–835 s
in the traces this card read; a curator lived its full 1500 s worker ceiling dying from the outside on a
digest alone). The three code-level ACs are pinned by the tests above. The live end-to-end AC(a) run — a
personal curator on the founder's local box committing a pass inside its wall and printing the meter line —
runs on the `stashdemo` production *copy*, whose backend the branch has not yet been deployed to; that copy
moves only when a human runs the trunk publish runbook (`docs/trunk-publish-and-deploy.md`), so the copy's
live run is the operator's post-landing leg, with the exact commands in the card's report. The commit-and-
advance mechanism itself is proven deterministically by the meter test, and STAS-457 measured the drain pair
moving under the fenced commit on this same copy (NULL → `2026-11-10 20:10:17` → `2026-11-11 12:00:00`).
