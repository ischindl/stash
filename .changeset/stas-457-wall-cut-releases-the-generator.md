---
"stash": patch
---

Release the in-flight pull before closing a stopped turn, so a curator wall arrives as a wall.

**The problem this closes.** The founder stack's personal-lane curator died twice on 2026-11-15 with
`RuntimeError: agent turn failed: aclose(): asynchronous generator is already running` — a choice
error, not the run ceiling it had actually hit. The wall fires as `asyncio.wait_for` cancelling the
turn, and the cancellation lands while the turn's own reader is inside `anext`. `_stoppable`'s
`finally` then called `iterator.aclose()` on a generator whose frame is still executing, which
CPython refuses; that `RuntimeError` replaced the `CancelledError` mid-unwind, `run_chat`'s
`except Exception` laundered it into `RuntimeError("agent turn failed: …")`, and a plain
`RuntimeError` is not a `TimeoutError` — so `except TimeoutError` never ran, `RunCeilingExceeded`
was never raised, and the dispatcher's `isinstance(e, RunCeilingExceeded)` branch, the only thing
that hands an unread backlog to the next pass, never fired either. The reap that makes this work was
already in the same function: the `TurnStopped` branch cancels and awaits `next_event` before
anything closes, which is why a user's stop never produced this error. The `finally` had no such
reap, and `next_event` was bound inside the `try`, so it could not have one.

**What changed.** `_release_pending_pull` is that reap, extracted, and `_stoppable` now calls it both
from the stop branch and from its `finally`, with `next_event` bound before the `try` so the one exit
everyone leaves — the stop flag, the wall above `run_chat`, or a consumer that stops iterating — goes
through it. A pull that already finished has no frame left to release, and awaiting its result there
would either return a value nobody asked for or re-raise an error the caller is unwinding, so the
helper returns without touching a done future.

**The exit that left its row unowned.** `mark_run` states that every path after it resolves the
outcome. One normal return broke that and no rescuer could see it, because a clean return is what
`_run_guarded` reads as success: `_run_scheduled_locked`'s `run_mode != "scheduled"` check sits past
the beat's charge, and used to `return` there — leaving the row `started`, the month credit spent,
`last_run_error` NULL, nothing logged, and nothing to sweep on a deployment that runs no `beat`. That
arm now resolves its own tick through `resolve_started_runs`, the same compare-and-set the escape
guard uses, with the cause `agent run resolved without running: run_mode=…`, and logs when it flips.
It is deliberately not the body's `except Exception`, which alerts: an agent switched to manual
mid-dispatch is not an outage. What this card deliberately does NOT resolve is the contracted state —
a dispatch that finds the lane's run lock held defers to the run in flight, which owns the shared
outcome; `test_busy_scoped_curator_refunds_only_skipped_run` pins that such a row keeps `started` and
exactly one refund, and a blanket resolver would stamp `failed` on a healthy lane and take its credit
a second time.

**The message now names what a cut run left unread.** A cut run's `last_run_error` is the only witness
it leaves, so it states the wall it hit, that the feed is unread, and the position the run was reading
from. On a draining run that position is the drain's committed pair, not the nightly watermark: the
watermark is what a drain never reads from, its backlog sitting behind it. Because `draining` is the
dispenser's fact, a `full_history` run works a drain its row does not carry yet, and that case names
itself as freshly seeded and reading from the feed's start rather than answering from a row that holds
no position.

**What did not change, and could not be allowed to.** No wall was raised, lengthened, or disabled —
AC3 of this card forbids it, and the failure mode this closes was measured *at* the wall, not from a
missing one. `asyncio.wait_for`, `run_wall_seconds`, `RUN_COMMIT_RESERVE_SECONDS`, the worker's own
ceilings, the watermark fence, the `TurnInProgress` skip semantics, the in-process escape guard, and
the beat's stranded-run sweep all stand as they were. The scoped lane's own message
(`Scoped curation exceeded its run time limit`) is untouched, so no string-matching reader of it
breaks. `aclose()` is still called — the fix releases the frame first, it does not stop closing.

**Measured on a copy of production, on this branch's image.** A curator dispatch on the `stashdemo`
copy (personal lane, local endpoint, `digest_provider=local`) shows the whole exit for the first time:
`turn started lane=personal full_history=False metered=False` at 07:03:57, and at 07:18:32
`RunCeilingExceeded: Curator curation exceeded its run time limit of 570s and left the feed unread,
reading from drain position at=2026-11-11 12:00:00+00:00 cursor={'scopes': []}` — with
`last_run_outcome='failed'` and that text in `last_run_error`, the catch-up chain reporting
`guard=no_progress`, the `agent-run:42fad1ea…` Redis key released by the run itself, and **zero**
occurrences of `aclose(): asynchronous generator is already running` in this run's worker log, against
six distinct failures carrying that text in the copy's own worker log on 2026-11-15 (18 log lines — the
message repeats three times per failure: 11:27:36, 11:41:27, 11:46:52, 13:21:32, 13:35:07, 13:58:20). The phase split is measured too: mark_run to the cut is 875 s, of
which the digest phase took 305 s un-walled and the turn 570 s — which falsifies the intake's
inference that the wall never fired, because the wall wraps only the second phase. The same run made
25 harness tool calls and wrote no page, on a feed whose drain stood three canonical events
(2,255 characters) from the end of that curator's whole window.

**Inferred, and labelled as such.** The intake's two run durations (1,266 s and 728 s) are measured
from the copy's container log, but the digest/wall split *for those two runs* is inferred from the
position of `wall =` after the digest call — no per-phase timestamps survive for them; only this
card's own run has the split measured. That the copy's worker containers carry no
`LOCAL_STASH_API_URL`, measured as a refused connection to the default `localhost:3456` from inside a
worker and a 200 from `http://backend:3456`, is the standing explanation for a personal-lane curator
with zero page writes on that copy; this card did not change that env and did not fix it.

**AC4's second half is not met on this copy, and here are the numbers.** AC4 asks the copy for two
things: a wall cut that names the unread source, and at least one curator pass that commits its
position *with page writes*. The first half is met four times over — the same exit with its three
shapes was reached at 05:57:34 (a freshly seeded drain: `reading from drain position at=None cursor={}
(freshly seeded: reads from the start)`), 06:13:45 (`at=2026-11-10 20:10:17.037000+00:00`), 07:18:32
and 07:36:29 (both `at=2026-11-11 12:00:00+00:00`), each of them `failed` in the row, each with the
Redis lock released and none of them producing an `aclose` error. The second half never happened:
across **six** curator turns on this copy today, this curator's page writes stayed at **0** by both
probes (`agent_name` and `session_id LIKE 'agent-curate-42fad1ea%'`), and the copy's total page_edits
stayed at its pre-proof value of 6,593 rows. The drain pair did move twice under this build
(NULL → `2026-11-10 20:10:17.037` → `2026-11-11 12:00:00`), which proves the fenced checkpoint commit
itself works on this copy — but a pair that moves while zero page rows appear is exactly the
half the intake complained about, not the half it asked for. The reason is measured too: the wall-cut
turn spent its 570 s on 25 harness tool calls (≈23 s each, `timeout 45` on every `stash search`) reading
the wiki corpus — `index.md`, `log.md`, per-page files — and died before its first write. The
only lever this card may pull is the row's own ceiling, and it points the wrong way: the ceiling must
stay in `(364.3, 600]` for any pass to be priced at all, so every narrower wall cuts earlier and a
wider one is AC3's forbidden fix. What the committing-with-writes path *is* proven by, in the meantime:
34 page_edits rows on this same copy carry `session_id LIKE 'agent-curate-%'` (all of them the scoped
lane), and the personal lane's commit-and-writes pair is pinned by `test_curator.py` and
`test_scoped_curation.py`. The gap this leaves is a real finding and is named in the card's report:
the personal lane has no per-pass checkpoint the way the scoped loop got one in STAS-438, so a slow
box can spend wall after wall on a lane that has nothing to show for it.

**One shape AC2 cannot reach, named rather than claimed.** A cause that names itself is what both new
messages do, but an `Exception` whose `str()` is empty still resolves its row with a nameless cause,
because the cause is built from `{exc}` and not from the class name. This card left that alone rather
than widen its reach into every writer of `last_run_error`; the test
`test_catchable_turn_failure_keeps_its_own_cause` pins what is reachable today so a later card cannot
claim it was already fixed.
