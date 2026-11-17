# STAS-452 final account: what the fix reclaims, what it does not, and what is an operator leg

Measured on this host on 2026-11-17 while the lane was live. Nothing here deletes, prunes, resets, cleans,
lands, builds, or restarts anything; the scripts (`lane-account.py`, `refusal-probe.py`) are read-only and the
raw reads are in `lane-account.txt` and `live-loop-probe.txt`.

## Measurement basis

| thing | value |
| --- | --- |
| engine trunk read for the lane cut | `81c576a865791bffa61bf8dcfd2c3a7433b921ec` (intake named `bd332d309e`; that SHA is stale) |
| probe the release gate runs | `git status --porcelain=v1 --ignored=matching --untracked-files=normal` (`worktree-backend.ts:1367`) |
| base allowlist transcribed from | base `81c576a865`, `worktree-backend.ts:1285-1307` |
| lane population | 28 directories under `.fusion/worktrees/`, 22,746 MB in one `du -sm -c` pass |
| `/home` at step 0 | 72 G free, 96 % used (intake read 20 G / 99 %) |

Both gates were asked of the same probe output per lane, so the classes differ only in the rule, never in the
reading. The first version of this script omitted `--ignored=matching`, which silently erased the ignored-name
class and made class 1 measure zero; the flag mirror above is what produced the numbers below.

## The three classes, reconciled to one `du -sm -c`

```
du -sm -c over 28 lane directories: TOTAL 22746 MB        sum of per-directory lines: 22760 MB
                                                              delta 14 MB = hardlinked content in >1 lane
class                                 lanes      MB
1-releasable-by-this-fix                 3      101
2-merged-with-occupancy                 14   11359
3-tip-not-in-trunk                       7   11255
residual-not-a-task-lane                 3        4
residual-released-by-both-gates          1       41
RECONCILED                              28   22760
```

### Class 1 - what this fix reclaims: 3 lanes, 101 MB

```
     34 MB  stas-484  todo  contained=True  base refuser: !! stashai.egg-info/
     34 MB  stas-485  todo  contained=True  base refuser: !! stashai.egg-info/
     33 MB  stas-235  todo  contained=True  base refuser: !! .ruff_cache/
```

This is the smaller answer, printed because AC3 asks for it: the intake claimed **262 MB over four lanes**
(stas-235 72 M, stas-430 41 M, stas-443 75 M, stas-449 74 M). Measured now it is **101 MB over three**:
`stas-443` and `stas-449` no longer exist on disk - their paths appear among the 5,689 refusal lines whose
checkout is already gone - and `stas-430` survives at 41 MB holding *only* allowlisted ignored output, which the
base gate already released, so it was never wedged and now sits in the residual row rather than in class 1. Only
`stas-235` of the four named lanes is still wedged, and at 33 MB rather than 72. The card reports the
measurement, not the claim.

The name that actually refuses is `stashai.egg-info/` - the artifact the documented
`uv venv -p 3.12 && uv pip install -e .` produces - and `.ruff_cache/`, which `ruff` writes. `.venv` is **not**
among them: it sits on the base allowlist at `worktree-backend.ts:1288`, so the intake's diagnosis named the
wrong artifact even though it named the right mechanism. A lane wedges on the *one* ignored name the list
lacked, and the install procedure guarantees that name exists.

### Class 2 - merged lanes held by occupancy: 14 lanes, 11,359 MB - NOT freed

Largest: stas-198 3,829 MB (4 occupiers), stas-246 1,660 MB (1: an untracked `engine/`), stas-262 1,432 MB,
stas-184, stas-185, stas-232, stas-239, stas-265... The occupiers are real git-visible content - `.stas198-embproof.sh`,
`.stas186-plan.md`, `frontend/.scratch/`, `.lc.txt`, `.step4-logs/`, and in one case a tracked modification to
`.github/workflows/test.yml`. Both gates refuse these, and refusing is correct: untracked is not ignored. Freeing
this 11 GB is a human decision about each lane's scratch, one lane at a time - operator leg 3 below. This card
does not promise it and does not do it.

### Class 3 - tip not contained in trunk: 7 lanes, 11,255 MB - deletion forbidden here

A squash landing leaves the lane's tip uncontained even after the content has landed, so ancestry alone cannot
license deletion; the documented predicate is a patch-id comparison, and the lane-sweep rule requires the card's
own `done` + `mergeConfirmed` conjunction first. Nothing in this card's scope touches them.

### Residual, named rather than folded in

3 registered directories that are not task lanes (4 MB) and 1 task lane that both gates already released (41 MB).

### Outside the lane directory: 6 registered worktrees, 16,369 MB

`stash-demo` (8,232 MB), `stash-main` (7,276 MB), `stash-sso` (750 MB), `stash-ceo461` (47 MB),
`stash-pr-compose` (34 MB), `stash-preview-125` (30 MB) are registered worktrees of this repository that no
automatic sweep addresses - they are not task lanes and no card points at them. They are reported apart, not
counted into class 1, because the fix changes only what the door *would* decide if one of these were ever
handed to it. Worth noting to the operator: each of those trees carries exactly one base-refusing ignored entry
(`backend/.env`, `.coverage`, `.ruff_cache/`), so today they cannot be cleared through the defensive door at all.

## The loop's own arithmetic, from the engine's telemetry

6,004 `tip-already-merged cleanup failed` lines across 88 cards (intake: 75 cards, "over 6600 lines"), first
2026-08-30, newest 2026-11-17T21:26:11Z - minutes before this session started. 5,994 of those lines name a
checkout, which makes it possible to say who owns each historical refusal instead of assuming:

| owns the refusal, measured at the checkout today | lines |
| --- | --- |
| checkout already gone (released by some other path; nothing left to refuse) | 5,689 (94.9 %) |
| ignored-name gate only - **this fix owns these** | 163 (stas-484: 90, stas-485: 73) |
| occupancy - both gates refuse, and correctly | 142 |

Two honest footnotes. First, 94.9 % of the historical noise resolved by the directory disappearing, so the loop's
disk toll is not what its line count suggests; its toll is 6,004 warnings, 88 cards carrying a warning that
repeats every sweep forever, and the 305 lines of genuinely stuck cases underneath. Second, the single biggest
producer of new lines in the last hours was **this card's own lane**: stas-452, 119 lines, refused because it
holds untracked evidence files while its base commit is already in trunk. That refusal is the mechanism working
as designed - do not delete a tree someone's work is sitting in - and it is why class 2 is an operator leg
rather than something this fix should have silently reclaimed.

## The capacity verdict: two instruments, and what they disagree about

AC4 asks whether `maxWorktrees` counts a dead tree. Both instruments were read at the same moment.

**Instrument A - the admission log.** 1,972 `queued - concurrency limit reached: gate=maxWorktrees` readouts in
card telemetry, newest 2026-11-15T23:43:13Z. `/settings/maxWorktrees` = 4. The readouts name their holders as
card ids, and the sets are small and specific: `maxWorktrees used=4/4 (holders: STAS-443, STAS-467, STAS-468,
STAS-470 ...)`, while `maxConcurrent used=3/7` on the same line.

**Instrument B - the filesystem.** 28 lane directories, 22.7 GB, of which only a handful are card-holders at
any moment.

The instruments disagree by an order of magnitude about *how many trees exist* and agree completely about
*admission*: the gate is computed over cards, not over directories, and a card is released from holding by
`provenCheckoutFreeTaskIds` (`concurrency/concurrency.ts:752`) once it retains no worktree path, or git proves
every retained path clean and zero commits ahead of base (`worktree/checkout-emptiness.ts:457` bare
`git status --porcelain`, `:462`/`:476` `rev-list --count`). Ignored content is invisible to that bare
porcelain read, so a lane full of `.venv` never made its card a holder.

**Verdict: the wedge costs disk and 6,004 log lines. It does not cost admission slots.** The two instruments are
in agreement on that, and the reason is a deliberate separation this card preserved: tip ancestry and emptiness
for *capacity* are read in `checkout-emptiness.ts`, and occupancy for *release* is decided only in
`worktree-backend.ts`. The fix narrows the second door; it does not touch the first, which is why the phantom-holder
bug RUFU-198 fixed stays fixed and why this change cannot quietly widen admission.

## Inherited claims this run corrected

| claim (intake) | measured at the live base |
| --- | --- |
| `/home` 20 G free, 99 % used | 72 G free, 96 % used |
| trunk `bd332d309e` | `81c576a865` |
| `.venv/` is the unnamed artifact | `.venv` is allowlisted (`:1288`); `stashai.egg-info/` and `.ruff_cache/` are the names that refuse |
| 262 MB immediately releasable (4 lanes) | 101 MB (3 lanes). Of the four named lanes, two are gone, one was never wedged (`stas-430`: allowlisted-only ignored content), one remains at 33 MB not 72 MB |
| 75 cards, over 6600 warning lines | 88 cards, 6,004 lines |
| `removeWorktree` non-test call sites "~100" | 25 |
| `worktree:removal-discarded-ignored-content` absent from trunk | confirmed absent (rc=1) - and now delivered |

## Four operator legs, named and left unexecuted

1. **Landing.** The delivery ref is `refs/heads/fusion/stas-452` in `/home/schindler/git/Fusion`, tip
   `fb9c89f35e`, base `81c576a865791bffa61bf8dcfd2c3a7433b921ec`, which is exactly what `refs/heads/main`
   pointed at when this lane read it. Three commits, in the order the work happened, so the guard is the
   direct parent of the change it guards: `b90f5bbce1` the 8-case guard test (3 failed / 5 passed in its own
   tree), `6b23a7c665` the predicate fix with its adapted sibling suites and docs, `fb9c89f35e` the changeset.
   Verify descent, then advance trunk by compare-and-swap with the value read from the ref itself:
   `git -C ~/git/Fusion merge-base --is-ancestor refs/heads/main refs/heads/fusion/stas-452` then
   `git -C ~/git/Fusion rev-parse refs/heads/main` immediately before and after the CAS write. Nothing in this
   lane moved `refs/heads/main`. The superseded pre-split chain is still reachable as
   `refs/heads/stas-452-pre-split-backup` (`1d6b5555e7`) and its final tree is byte-identical to `fb9c89f35e`;
   it can be deleted once the operator has no use for it.
2. **Build plus daemon restart.** The fix only takes effect in a running engine. Building the workspace package
   and restarting the daemon are the operator's legs, and they are the ones this session cannot perform at all:
   I execute inside the daemon, so a restart ends me mid-flight. Any artifact for that has to be run by a human.
   After it, the three class-1 lanes (`stas-484`, `stas-485`, `stas-235`) are released by the sweep itself, with a
   `worktree:removal-discarded-ignored-content` row naming lane and path per release.
3. **Class-2 scratch, one lane at a time.** 11,359 MB across 14 merged lanes, held by untracked scratch files
   and one tracked modification. Each needs a human to decide whether the file is evidence or work - including
   `stas-184`'s modified workflow file, which is a change nobody committed, not junk. Not this agent's call, and
   the `git worktree remove` command is deliberately not scripted here.
4. **The predecessor's pull request.** `ischindl/Fusion#2` stays untouched: no close, no comment, no force-push.
   Per the repository rule, PR work happens only on an explicit operator order for the named task, and
   `refs/heads/stas-397-delivery` was neither cherry-picked nor merged here - this lane carries its own fix.

## Verification actually run, at tip `fb9c89f35e`

Every row below was executed against `fb9c89f35e` itself, except the two multi-file engine-default sets, which
were executed on `1d6b5555e7` - a tree proven byte-identical to the shipped one by an empty
`git diff fusion/stas-452 stas-452-pre-split-backup`, so their verdicts transfer without inference.

| check | result |
| --- | --- |
| guard test (real git), 8 cases | 8 passed at the tip; 3 failed / 5 passed at the guard commit, run in that commit's own tree |
| engine-default worktree set (backend, acquisition-pinned, doc-accuracy, refusal-non-terminal, post-landing cleanup, merger-ai) | 257 passed |
| engine-default self-healing / branch-conflict set (14 files) | 294 passed, 1 failed |
| engine-reliability worktree set (guard, defensive-removal-preservation, acquisition-ignored-only, exec-lane) | 66 passed |
| engine-reliability real-git branch/self-healing set (4 files) | 10 passed |
| core targeted (`no-commits-finalize-guard`, `zero-commit-landing-proof`) | 60 passed |
| `tsc --noEmit` core and engine | clean |
| static gates (changeset format, non-latin, comment-assertions, retention, mock completeness, inert seams, quarantine ledger, future dates, and the rest of the pretest chain) | clean |
| `check-file-line-count` | red, and red at base: every named file was already over its ceiling. Disclosed cost of this card: `self-healing.ts` +18 lines, `worktree-acquisition.ts` +12, `worktree-backend.ts` -49; net across src is -17 |
| `check-lane-wiring` | red at base for 5 files, none of them in this diff; baseline untouched |
| full engine suite at the **base** commit, run in this session | 495 failed / 18,215 passed / 382 skipped in 1,484 files - the trunk the lane was cut from is not green |

The one failure inside the set this card touched, `self-healing-ghost-branch-recovery.test.ts > keeps genuine
live-foreign conflicts parked`, is reproduced in that base baseline log (`.fusion-tmp/before-suite.log`), so it is
not this change. A matching full-suite run at the tip was not attempted: it needs another two hours of a
contended box for a suite that is 495 tests red without this card in it, and every file this card can reach is
covered by the targeted runs above.

## An incident this session caused, stated plainly

To compare a failing suite against the base, this session ran `git stash push` in the nested engine checkout when
it had nothing uncommitted to save, then `git stash pop`. The push saved nothing; the pop therefore consumed a
**foreign** entry from the repository-shared stash - the RUFU-493 recovered board-workflow WIP - and applied it
into this lane's checkout, producing merge conflicts in ten dashboard files. Recovery: the applied content was
reverted (`git reset` plus `git checkout -- packages/dashboard`, two log artifacts deleted) so this checkout is
back at its own tip with an empty `git status`, and **the foreign stash entry was not dropped** - the conflicted
pop kept it, so its owning lane can still `git stash pop` it. This is the hazard those stash entries themselves
record. The lesson, worth carrying: a lane that needs a base comparison must cut a temporary worktree at the
base commit, never touch a shared stash.

## A second self-inflicted wound, found only at handover

This lane's engine checkout was created detached, and it stayed detached for the whole card, so the work had
**no branch ref at all** until the moment a landing ref was about to be quoted: the commits existed only as
`HEAD` of one working tree, reachable from no ref, which is a state where any later cleanup could have taken
them with it. The earlier text of this file named a ref and a tip sha that had never existed as refs - the
shas were real, the ref was invented by inference from what the lane *should* have been. Corrected here:
`refs/heads/fusion/stas-452` now exists and names the tip quoted above, the nested worktree has it checked
out, and the history was rebuilt so the guard commit is the parent of the fix. The lesson is the same shape as
the stash one: name the ref before quoting it, and read it from `git branch --show-current` rather than
assuming the checkout is where you think it is.

## Defects found on the base that this card did not create

- `check-lane-wiring.mjs` fails on trunk for 5 files (`core/task-store/follow-up-ops.ts`,
  `human-merge-approval-ops.ts`, `tasks/task-follow-up.ts`, `engine/agent-heartbeat.ts`, `dashboard/app/App.tsx`);
  none of them is in this card's diff, and this card did not touch the baseline.
- `worktree-acquisition-ignored-only.real-git.test.ts` was red on the base for all 3 of its cases: its fixture
  left the pinned checkout detached, which reads as branchless and routes acquisition into the branch-rebind path
  (`store.getTask` + `proveTaskWorktreeRebind`) that the fixture store never implemented. Reaching the door this
  card changed required re-shaping the fixture to a foreign registration that stays on its branch - done, and the
  3 cases now pass, which they did not before.
