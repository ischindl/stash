# STAS-330 engine evidence — advertise-once wake comment delta

## Why this directory exists

STAS-330 fixes a defect in the **Fusion engine** (the sibling `Fusion` repository), not in this
Stash checkout. The card is tracked on the Stash board, so the reviewed source cannot be read from
this branch alone, and the scratch worktree plus git bundle the implementation session used to work
in were wiped from disk before review (the STAS-273 class of disappearance — the branch and its
commits in the engine repo survived, the scratch copies did not). The code review therefore had no
committed diff to open, and a review gate refused the approval:

> Review approval refused: uncommitted source changes in
> `.fusion/worktrees/stas-330: ".engine/engine/", ".engine/stas-330-wake-comment-delta-consumption.bundle"`

This directory is the fix for that, on the pattern `stas-299-engine-evidence/` established: every
git-derived artifact is reproducible from engine git objects by `./generate.sh`, and `./verify.sh`
re-derives them and fails on any mismatch. Nothing here is transcribed by hand from the code.

## Provenance (single source of truth for every SHA quoted below is `provenance.txt`)

| | |
|---|---|
| Engine repo | `/home/schindler/git/Fusion` (branch `main` at `14ba495cb4`) |
| Branch | `fusion/stas-330-wake-comment-delta-consumption` |
| Base (branch parent) | `bc2f242f6ba05c1d76b2e6548755be7c8b9e2278` |
| Reviewed tip | `0c2fe67fe401b135926ec8c4952dfcc6ddb44b3d` |
| Diff | 13 files, +1060 / −87 |
| Commits | `74151ac0b0` Steps 2+3 (consume the delta) · `ee0bbe30f9` Step 4 (second steering renderer + duplicated heading) · `fd70820220` docs · `b8130dda56` Step 5 (header counts what it showed) · `0c2fe67fe4` review fold (F1 + A1/A2) |

Carrier for the work is the **branch ref** in the engine repo plus this directory. The bundle
mentioned in the `handoff` task document is gone and is not regenerated inside this worktree: an
untracked bundle in the task tree is exactly what tripped the gate above.

## What the change does

1. **Consumption, not recomputation.** `packages/engine/src/agents/wake-comment-delta.ts` is new.
   A wake subtracts the ids this agent's recent runs already advertised on this card from the ids it
   is about to advertise (`consumeWakeCommentDelta`), reading the record off those runs' own
   `contextSnapshot.triggeringCommentIds` — the record exists before the session does anything, so a
   crashed or terminated run cannot un-consume what it was already told. Bounded by
   `WAKE_COMMENT_CONSUMPTION_SCAN_RUNS = 20` runs and `MAX_CONSUMED_WAKE_COMMENT_IDS = 200` ids.
2. **Advertised-once, recorded on what was actually rendered.** `renderWakeCommentDelta` returns the
   ids it printed a body for; the executor's trim ledger is fed those (`hiddenTriggeringCommentIds`),
   and an id that is not resolvable on the card is still consumed — advertised once, never twice.
3. **Self-echo rule.** `selfAuthorKeys` / `isSelfAuthoredComment` / `selectExternalSteeringComments`
   withhold a comment whose author key is this agent (id, or lowercase name on the steering lane) and
   name it on one `[wake-comment-delta] ... selfSuppressed=` line. Covers the heartbeat delta, the
   executor `## Steering Comments` lane and both workflow reply detectors, which previously each
   carried their own copy of the watermark rule with no author check.
4. **One heading, one count.** `TRIGGERING_COMMENT_HEADING` is the single source for
   `New comments since last run:`, and the prompt now says
   `triggering comments: N of M readable on this card` where N is what the agent can actually read.

## Review fold (this is the answer to the code review)

`remediation.diff` is the single commit `0c2fe67fe4`. Verdicts and where each is answered:

| id | what the reviewer asked for | where it is answered |
|---|---|---|
| **F1** (must-fix, wording) | The seam does not "make a comment visible to the very first wake that shows it": the write is at run open (`contextSnapshot.triggeringCommentIds` at the startRun), which sits before the three graceful exits, so those exits consume without presenting. Say plainly that consumption is recorded when the wake opens the run, and record the graceful-exit loss as an accepted boundary, not as harmless. | `wake-comment-delta.ts` module docstring (`The seam is run-open, not render-time …`) and the call-site docstring in `agent-heartbeat.ts`; `docs/agents.md` advertise-once paragraph. Both state the seam and the accepted cost. |
| **A1** (advisory) | Two docstrings claimed `SteeringComment.author` is `user \| agent \| system \| squad-role` and that a literal agent id in a steering author is a "malformed row". Both claims are false: steering rows written through `addSteeringComment` carry a raw free-string `req.userId`, and `WorkflowEngineDeps.getUserPreferences` receives it verbatim. | `await-input-node.ts` and `workflow-input-markers.ts` docstrings corrected to the real `SteeringComment.author` domain and to "identity is stamped by whatever the writer used". |
| **A2** (advisory) | One assertion in a test fixture pinning the seam for a later writer-side identity stamp. | `workflow-input-author-filter.test.ts`: the fixture now states that the `agent:` prefix is the *reader's* contract, not a writer guarantee, and that a future writer-side stamp must keep this seam green. |
| **Measurement 4** | Measure the runs whose `triggeringCommentIds` end up in a graceful exit. Zero is a valid answer if measured. | `measurement.txt`: **0** runs of 52 202 carry non-empty `triggeringCommentIds` *and* exit gracefully (1 carrier run, 156 graceful exits, 0 overlap) — reported as a measured zero with denominators, not as proof the boundary is unreachable. |

## Reading order for a reviewer

1. `stas-330-series.diff` — the whole branch (implementation, tests, docs, changeset).
2. `remediation.diff` — the review fold on its own, at `-U15` context.
3. `diffstat.txt` / `commits.txt` — file inventory and per-commit message bodies (each carries the
   measured evidence that justified it).
4. `measurement.txt` — the measurements behind the claims, including the ones that came out
   **REFUTED** at this base and the **EVIDENCE-BLOCKED** per-id counts.
5. `verification.txt` — which gates were run at the tip, and the one pre-existing red that was
   reported rather than chased.

## Warnings a reviewer needs

- `stas-330-series.diff` and `remediation.diff` contain verbatim proprietary engine source and test
  code, kept for review only, inside a private repository.
- This directory changes **no** Stash runtime file and is imported by no build or test job.
- Nothing was pushed, no PR opened, no engine restarted, no deployed checkout edited: all of those
  are operator-gated (operator directive 2026-08-30).
- The scratch engine worktree used for the gates is removed again after this commit; `git status` in
  this worktree is clean apart from tracked files.

## Regenerating and verifying

```
./generate.sh          # rewrites the git-derived files from the engine repo (ENGINE_GITDIR to override)
./verify.sh            # re-derives into a temp dir, diffs, checks MANIFEST.sha256, non-zero on mismatch
```

`MANIFEST.sha256` covers every file in this directory except itself. `verify.sh` re-derives the
git-derived artifacts; `measurement.txt` and `verification.txt` are observations of the board DB and
of test runs, so they are checksummed and quoted, not re-derived.
