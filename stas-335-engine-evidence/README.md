# STAS-335 engine evidence — TaskStore boot-stage and query-failure attribution

## Why this directory exists

STAS-335 fixes a defect in the **Fusion engine** (the sibling `Fusion` repository), not in this
Stash checkout, so the reviewed source cannot be read from this branch alone. The card's own code
review ran in exactly that position and said so: it measured the live board, read the evidence
documents, and still could not open the diff — `/home/schindler/git/Fusion` is outside that
reviewer's worktree boundary, `.stas335-logs/` had been wiped when this work tree was re-checked
out, and the git bundle it pointed at was not in the gallery either. Its verdict was
*approve-with-rollout-condition* with the diff marked **"compare-only, not read directly"**.

This directory closes that gap on the pattern `stas-330-engine-evidence/` established: every
git-derived artifact is reproducible from engine git objects by `./generate.sh`, and `./verify.sh`
re-derives them, diffs them, checks the manifest, and fails non-zero on any mismatch. Nothing that
describes code here is transcribed by hand.

## Provenance (the single source of truth for every SHA quoted below is `provenance.txt`)

| | |
|---|---|
| Engine repo | `/home/schindler/git/Fusion` (local `main` at the reviewed tip) |
| Branch | `fusion/stas-335-taskstore-boot` |
| Base (branch parent) | `080cfdc646fc1567febf8ac5a51c6d98e150a7a0` (RUFU-436) |
| Reviewed tip | `ca55f0b30b5348ed1368082a3ea47ea6fc263354` |
| Diff | 12 files, +624 / −24 |
| Commits | `3b8aa69a69` boot-stage attribution through `onBootStage` + `[statement= table= sqlState=]` on store query failures · `511d936442` the agent-roster read fails as an attributed tool error · `ca55f0b30b` docs (architecture, task-management) + changeset |
| Publication | `git update-ref refs/heads/main ca55f0b30b… 080cfdc646…` — a compare-and-swap fast-forward, not a merge commit, **no push, no PR** (operator-gated) |
| Containment | `git merge-base --is-ancestor ca55f0b30b… refs/heads/main` → **0 (contained)**, re-checked by `verify.sh` on every run |

The carrier of the work is the **branch ref and `main` in the engine repo**, plus this directory.
The portable copy named in the `deploy-handoff` document survives as this card's attachment
`stas-335-engine-bundle-3b8aa69a..ca55f0b3.txt` (the board rejects a `.bundle` extension, so it is
base64-wrapped); its sha256, decoded size, and `git bundle verify` output are recorded in
`verification.txt`. The raw bundle under `.stas335-logs/` is gone — that directory was wiped by the
re-checkout and its now-empty remnant is deleted rather than left as a pointer to nothing. No
untracked scratch or bundle lives in this work tree: that is the STAS-292/STAS-330 failure this
directory replaces.

## What the change does

The card arrived describing one symptom. Measurement (task document `diagnosis`) showed **two bugs
reporting as one**, and the fix addresses both without touching a single timeout:

1. **A stalled boot now names the stage it reached.** `createTaskStoreForBackend` reports every boot
   stage through an optional `onBootStage` observer (the `onMigrationProgress` precedent); the
   extension collects the stages of the attempt a caller was waiting on, appends
   `Boot attribution: <phase>=<ms> … (elapsed Nms of the Nms budget).` to both deadline shapes,
   carries `details.bootStages` / `details.elapsedMs`, and records them on the cooldown entry so a
   caller inside the backoff learns where the *abandoned* attempt stalled. This is the part that had
   to move channel: the five stage timings already existed, but they left the process as
   `console.log`, and an agent session's extension is forked `silent: true` with only IPC forwarded —
   so a stall that had already happened was unattributable by anyone.
2. **A failed query is typed instead of echoed.** `describeStoreQueryFailure` reads Drizzle's wrapper
   structurally (`query` + `cause.code`, never `params`) and `storeErrorResult` appends
   `[statement= table= sqlState=]` with `details.operation` saying whether a read lost nothing or a
   write persisted nothing. `fn_list_agents` had **no catch at all**: the raw wrapper — whole `SELECT`
   re-printed, bound project id, SQLSTATE buried in `cause` — was thrown past the tool boundary.
3. **Empty stays empty.** A roster that is simply empty is still an ordinary answer, not an outage;
   the refusal and the green case are pinned next to each other.

What it deliberately does **not** do, each checked in `invariants.txt` rather than asserted:
no budget was raised (§A — `EXTENSION_STORE_BOOT_TIMEOUT_MS`, `EXTENSION_TOOL_TIMEOUT_MS` and
`BOOT_FAILURE_COOLDOWN_MS` are byte-identical at base and tip), no retry loop or cached fallback
handle was added (§A), no error is swallowed (§A2/B list every `try`/`catch` and every hunk the
series owns), the stop-authority rule that only the process which started the embedded postmaster
may stop it is untouched (§B — no hunk lands in `stopEmbeddedRuntime`, `releaseEmbeddedRuntimeLease`,
`closeCachedStores`, `setHostTaskStore`, `clearHostTaskStores`, the `session_shutdown` handler or a
detach path, and the two lines in the series that merely *mention* those tokens are prose in
`docs/architecture.md` and in a test comment, plus one `throw` re-wrapped across lines around an
unchanged constant), and the surfaced diagnostic reads no credential, connection string, bound
parameter or driver `detail` (§C).

## Contents

| file | what it is | re-derived by `verify.sh`? |
|---|---|---|
| `stas-335-series.diff` | the whole branch as one patch (`git diff -U5 base tip`) | yes |
| `diffstat.txt` · `commits.txt` | file inventory · per-commit messages with the measurements that justified them | yes |
| `provenance.txt` | branch, base, tip, commit count, shortstat, changed files, containment verdict | yes |
| `invariants.txt` | the four checks summarised above, each printed with the command that produced it and a computed verdict | yes |
| `tip-excerpts.txt` | the changed code in `-U60` context for `extension.ts` / `agent-tools.ts` (too large to ship whole), then the stop-authority rules printed from the **tip blobs** around a named anchor, so they can be read in context and compared | yes |
| `snapshot/…` | full tip copies of the five files small enough to ship whole: the changeset, `tool-store-errors.ts`, and the three test files | yes (`diff -ru` against `git show tip:<path>`) |
| `verification.txt` | which gates ran at the tip, what this remediation re-derived first-hand, the publication proof, the bundle re-verification, and the pre-existing red that was reported rather than chased | checksummed, quoted — a record of runs, not a derivation |
| `MANIFEST.sha256` | every file here except itself | — |

## Reading order for a reviewer

1. `stas-335-series.diff` — the branch.
2. `invariants.txt` — the four claims above, each with the command that produced it and a computed verdict.
3. `tip-excerpts.txt` part 2 — the lifecycle rules the change had to leave alone, at the tip.
4. `snapshot/packages/**/__tests__/…` — the pins, including the credential-canary fixture
   (`'params: ["proj-1","leak-canary"]'`, asserted absent) and the teardown tripwires that a session
   which only *joined* a store must never close it.
5. `verification.txt` — what was executed where, and by whom.

## Warnings a reviewer needs

- `stas-335-series.diff`, `tip-excerpts.txt` and `snapshot/` contain verbatim proprietary engine
  source and test code, kept for review only, inside a private repository.
- This directory changes **no** Stash runtime file and is imported by no build or test job.
- The gate numbers in `verification.txt` PART 1 were executed by the implementing session in the
  engine work tree; the remediation session could not re-run them and says so in those words,
  because this work tree's boundary refuses any command whose working directory is the engine repo.
  PART 2 is what it did measure.
- **The roll-out condition from the review is still open and is not closed by this bundle**: the
  engine daemon serving every session's `fn` calls was started ~7.5 h before the publish, so live
  sessions cannot print the new attribution text until a rebuild plus a managed restart — both
  operator-ordered (STAS-103 is the precedent for why "in `dist`" and "running" are different facts).

## Regenerating and verifying

```
./generate.sh          # rewrites the git-derived files from the engine repo (ENGINE_GITDIR to override)
./verify.sh            # re-derives, diffs, checks containment and MANIFEST.sha256; non-zero on mismatch
```

Both take `STAS335_BASE` / `STAS335_TIP` / `STAS335_BRANCH` to review a different head. `verify.sh`
re-derives into `.verify-scratch/`, which it removes again (this work tree may not write outside
it, so the scratch cannot live under `/tmp`); the directory is gitignored and `git status` is clean
after a run.
