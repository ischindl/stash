# STAS-299 — the engine change, made reviewable inside this worktree

## Why this directory exists

Code-review retry 3 returned REVISE with the blocking finding that the review checkout — this
stash worktree — contains **no STAS-299 change**: the engine product code lives in the sibling
repository `Fusion`, and the engine working copy that run 7 had staged under `.engine/` was
deleted before the review fired. The card's diff against `main` is empty by design, and a task
document is not source, so the gate had nothing to read and reported the diff as "trivially
empty and unreviewable".

This directory is the reviewer's own remedy (1): *a plain source copy plus the committed engine
diff, landed as tracked files in the task branch*. Everything here is produced by
[`generate.sh`](generate.sh), which runs **read-only** `git show` / `git log` against the engine
repository and writes only into this directory. No file of the Stash repository was modified to
produce it, and nothing here is product code — it is the review evidence for the engine half of
this card.

Nothing in this directory asks to be believed on its author's word. [`verify.sh`](verify.sh)
re-derives the evidence from the engine repository's objects and compares it with the bytes
committed here — hashes, snapshot blobs, all 1297 numbered excerpt rows, the probe tokens, and the
clean-checkout probe the gate itself uses. Run it before trusting a sentence below.

## Exact engine provenance

| | |
| --- | --- |
| Engine repository | `/home/schindler/git/Fusion` (remote `ischindl/fusion`, base `Fergana-Labs/fusion`) |
| Branch | `fusion/stas-299-selfheal-lifecycle` |
| Fork base (where the branch left trunk) | `47fe311d6d` |
| **Reviewed tip** | `dc40314375bd29b3b6262b467ebfe8ea8ed99e7e` |
| Merge-base of branch and engine local `main` | `0dd24e8d46003774ca5bb5b8b4dcf72029eec70a` |
| Engine local `main` at evidence time | `f2db502381` — 26 commits ahead of that merge-base |
| Commits ahead of `main` | 2 (`50b1771f52`, `dc40314375`) |

`git rev-list --left-right --count main...fusion/stas-299-selfheal-lifecycle` → `26 2`.

The 13 non-merge commits authored for STAS-299, chronological:

| SHA | What it commits |
| --- | --- |
| `1e00f94c9b` | engine: self-heal stamps the stranded hold-lane card failed (reqs 1–2) |
| `533ebc973d` | core: answer *why* a card is not the stranded-gate retry class |
| `83afa89542` | engine: a failure park owns the card until a retry discharges it |
| `3b55220aee` | core: name the stranded-gate retry class once (reqs 2, 4) |
| `d8480855a6` | core: classify the class against the card's own workflow |
| `ffff0ff38a` | core: keep the class a pure function of card + workflow |
| `87196c65b9` | cli: a stalled TaskStore boot answers with a retry, not a denial (req 3) |
| `f322d89502` | retry: one verdict-less gate answer on board, CLI, extension, chat (req 4, Inv. C) |
| `806529251e` | test: gate discharge on every retry surface (req 4) |
| `60efd08b50` | test: the STAS-281 card from sweep through park to hatch (reqs 1–3) |
| `56c7767471` | docs: changeset, architecture, task-management, skill references |
| `50b1771f52` | **FIX** — code-review P0 (tool budget ≠ boot stall) + P1 (report names resolved lane) |
| `dc40314375` | **test** — pin the boot boundary as the only place allowed to read a timeout sentence |

## How to read it, in this order

| File | What it gives you |
| --- | --- |
| [`remediation.diff`](remediation.diff) | The two commits answering the open findings, with 25 lines of context — read this first. |
| [`tip-excerpts.txt`](tip-excerpts.txt) | Every cited region **as committed at the tip, prefixed with its real 1-based line numbers**, so a finding that cites `extension.ts:658` can be checked at `658`. A diff cannot answer an absolute line number; this can. |
| [`snapshot/`](snapshot/) | Verbatim copies, at the tip, of the 9 files STAS-299 *added* (the shared class, its suites, the changeset) — readable whole instead of reassembled from hunks. |
| [`stas-299-series.diff`](stas-299-series.diff) | The complete 13-commit committed engine diff (3 lines of context) — what requirements 1–4 and Invariant C rest on. |
| [`newcolumn-audit.txt`](newcolumn-audit.txt) | Every `details.newColumn` reply site in the engine at the tip, with real line numbers, across all four surfaces — so P1 is judged against the whole population rather than only the sites the finding named. Ends with the machine check: **0** hardcoded lane literals at the tip against **3** at `0dd24e8d46`, the reviewed tip these commits answer. |
| [`commits.txt`](commits.txt) | Per-commit metadata and `--stat` for all 13. |
| [`verify.sh`](verify.sh) | Re-checks every claim in this directory against the engine repository's objects — manifest hashes, snapshot blob identity, excerpt row fidelity, probe resolution, the clean-checkout probe, and the "no product file touched" bound. Exits non-zero on any failure. |
| [`MANIFEST.sha256`](MANIFEST.sha256) | Content hash of every file here except this README and itself (`README.md` is hand-authored guidance, so editing the guide must not invalidate hashes). The two scripts are hashed on purpose: a reviewer can detect a generator that was quietly edited to flatter the evidence. Check: `sha256sum -c MANIFEST.sha256` from this directory. |

## The open findings, and where the committed tip answers them

### P0 — "a tool over its own budget is still answered as a stalled TaskStore boot"

Fixed in `50b1771f52`; classification is now **the type, never the sentence**.

- `packages/cli/src/extension.ts:414-440` — `isTransientStoreBootFailure` returns
  `error instanceof TaskStoreBootTransientError || error.name === "TaskStoreBootTransientError"`,
  with the reason recorded in-code: `raceWithTimeoutAndAbort` builds `${label} timed out after
  ${ms}ms` and `wrapExtensionToolExecute` passes `label = toolName`, so
  `fn_task_merge timed out after 60000ms` was byte-identical to the boot deadline sentence.
  Visible at `tip-excerpts.txt` block *lines 386-480*.
- `packages/cli/src/extension.ts:658` — the wrapper's answer site calls that predicate, so a
  tool-budget expiry falls through to the plain `{ error: message }` failure shape. The remaining
  `BOOT_DEADLINE_CLASS` use at `:662` chooses only the console stream (`warn` vs `error`); it
  cannot reach the agent-visible answer.
- `packages/cli/src/extension.ts:1370` — the policy gate's answer site is type-gated the same way,
  so the `retryable`/`transient` answer is unreachable to a tool's own timeout.
- The narrowing loses nothing: `getStore`'s catch (`extension.ts:829-836`, plus the deadline-armed
  cooldown entry that re-throws the typed error at `:799-806`) wraps **both** transient branches — the
  wall-clock expiry and the backoff entry its orphan leaves behind — in
  `TaskStoreBootTransientError`, and those are the only two paths either answer site can meet. A real
  boot failure stays un-typed and keeps the fail-closed denial.
- **The test the reviewer asked for**: `extension-tool-timeout.test.ts` → *"answers a tool that
  overran its OWN budget as that tool's failure, never as a stalled store boot"* drives
  `wrapExtensionToolExecute("fn_task_merge", …, 25)` against a never-settling tool and pins the
  **absent** fields — `details.transient`, `details.retryable`, `details.bootTimeoutMs` all
  `toBeUndefined()` — plus `expect(text).not.toMatch(/taskstore|boot|retry this same call|nothing
  was denied|do not ask the operator/i)`. The pre-existing test could not see the defect because
  `toMatchObject` ignores extra fields; the message it now shares is exactly the collision.
- `dc40314375` additionally pins the boundary the reviewer asked classification to live at:
  *"types the pool's own lock-expiry sentence at the boot boundary, the one place allowed to read
  it"*, paired against the neighbouring case where a lock failure names no expired budget and
  stays a real failure.

### P1 — "a retry still reports `details.newColumn: 'todo'`"

Fixed in `50b1771f52`; every report carries the **resolved** target.

All four `fn_task_retry` branches in `packages/cli/src/extension.ts` (visible in the
`tip-excerpts.txt` block *lines 2975-3110*):

| Branch | Line | `details.newColumn` |
| --- | --- | --- |
| stranded-gate discharge — moves nothing | `:2990-3003` | (no move; card stays in its lane) |
| unusable-worktree recovery | `:3018` resolves, `:3025` answers | `retryTarget` |
| in-review execution re-queue | `:3039` resolves, `:3050` answers | `executionRetryTarget` |
| in-review merge retry — moves nothing | `:3064` | `task.column` (the lane the card is in; was `in-review`) |
| generic re-queue | `:3088` resolves, `:3096` answers | `retryTarget` |

Each resolved value is computed *before* the `logEntry` call, so the audit line, the reply text and
the machine-readable payload carry one value. `FNXC:RetryReportCarriesTarget 2026-10-13-06:30` at
`:3063` records the third site.

- CLI parity: `commands/task.ts:2049` resolves `retryHoldColumn` once and the log lines and
  terminal output at `:2101-2189` all name it. Chat parity: `agent-tools.ts:3622`, `:3676` answer
  `task.column` and `:3725` answers `retryTarget`. `newcolumn-audit.txt` lists that entire
  population and asserts no hardcoded lane literal survives on any surface.
- **The renamed-board test the reviewer asked for**:
  `snapshot/packages/cli/src/__tests__/extension-retry-resolved-target-report.test.ts`. Its fixture
  IR is `custom:renamed-lanes` — `backlog` (`intake` + `hold`, release `capacity`), `building`
  (`wip`), `checking` (`merge-blocker` + `human-review` + `merge`) — so the resolved `hold` is
  `backlog`, the resolved `review` is `checking`, and the board declares **neither `todo` nor
  `in-review`**. Its three cases assert `moveTask` is called with `backlog`, then that the reply
  text, `details.newColumn` **and** the `logEntry` argument each contain `backlog` and
  `not.toContain("todo")`; the merge case asserts `moveTask` is *not* called, `newColumn` is
  `checking`, and `JSON.stringify(result)` does not contain `in-review`. The reviewer's own
  reasoning is confirmed in-code: `extension.test.ts` cannot catch this class because every retry
  fixture there sits on `builtin:coding`, whose hold lane *is* `todo`, so pre-fix and post-fix
  bytes agree.

### P0 — "the review source was deleted, the engine diff is unreviewable"

Closed by this directory. The six probes the reviewer ran over the worktree now resolve:

| Probe (retry 3: zero matches) | Now |
| --- | --- |
| `STAS-299` / `stas-299` | `stas-299-engine-evidence/` (every file) |
| `FN-8492` | `stas-299-series.diff` (the sweep's own card-facing message and the engine changeset both name it: *“… marked failed by self-healing (FN-8492)”*), `commits.txt` |
| `TaskStoreBootTransientError` | `tip-excerpts.txt`, `remediation.diff`, `snapshot/packages/cli/src/__tests__/extension-store-boot-transience.test.ts` |
| `isTransientStoreBootFailure` | `tip-excerpts.txt` (`extension.ts:414`), `remediation.diff`, `stas-299-series.diff` |
| `strandedGateRetry` | `snapshot/packages/core/src/tasks/stranded-gate-retry.ts` (`classifyStrandedGateRetry`), `stas-299-series.diff` (9 sites), `tip-excerpts.txt` (`register-task-workflow-routes.ts:3837`) |

Two properties of this directory are deliberate, both because retry 2/3 taught them:

- **It is committed, not untracked.** Retry 2 was refused by `refuseUncommittedApproval()` running
  `git status --porcelain=v1 -z --untracked-files=all` in this checkout; evidence left sitting
  there untracked would refuse the approval again. After this commit that probe reports zero
  records.
- **It is not a hidden directory.** The earlier attempt lived at `.engine/stas-299-engine/`; a
  dot-prefixed path is skipped by ripgrep's defaults, so the reviewer's own probes would have kept
  answering "zero matches" even with content present. `stas-299-engine-evidence/` is visible to a
  plain recursive grep *and* a default `rg`.

## Verification state, stated honestly

Nothing here was re-run by this session: the engine checkout that can execute the suites was
deleted deliberately (leaving a bootstrapped engine working copy in the review checkout is what
tripped `UncommittedSourceChanges` on retry 2, and the card's own standing rule is *create it, use
it, delete it inside the same session*). The measurements below are **quoted from the card's two
verification records**, `task document: remediation` and `task document: review-source-proof`,
both taken at the reviewed tip `dc40314375` in that checkout:

- **Red-before / green-after.** The P0 boundary test failed (1) against the stashed pre-fix
  `extension.ts` and passed after; the three P1 tests failed (3) pre-fix and passed after. The
  typed-stall positives pass **both** pre and post — which is the point: narrowing must not lose a
  real stall.
- **The six CLI suites at the tip**, run with this card's own `stas299-test-pg` container
  (`FUSION_PG_TEST_URL_BASE=postgresql://stash:stash@127.0.0.1:5299`): `task-retry`,
  `extension-stranded-gate-retry`, `extension-permission-gates`, `extension-tool-timeout`,
  `extension-store-boot-transience`, `extension-retry-resolved-target-report` → **exit 0**.
- **Siblings touched by `extension.ts`**: `extension.test.ts`, `extension-agent-class-boot-budget`,
  `task-store-boot-concurrency`, `worktree-doc-accuracy` (13) — all green.
- **Whole CLI package, three runs**: `2181 passed / 15 failed` twice (the first run reported
  `85 failed / 3 files` and is recorded there as an unreproduced env flake). The 15 are the
  disclosed baseline — 14 `commands/__tests__/task.test.ts` project-routing mocks and 1
  `workflow-docs-current` doc pin — proven not-caused by checking out `HEAD~1`'s `commands/task.ts`
  and diffing the sorted FAIL-name lists to **empty**.
- **Workspace gates at the final commit, each with its own exit code**: `pnpm typecheck` 0,
  `pnpm test:gate:static` 0, `pnpm check:changesets` 0, `tsc --noEmit` for `packages/cli` 0,
  `eslint` 0 errors on the touched **source** files (the repo's eslint config ignores
  `__tests__/**`, so no lint claim is made for tests). `check:move-target-literals` population
  still empty.

**What this session did verify first-hand**, through read-only `git` against the engine
repository: that the tip's committed `isTransientStoreBootFailure` body is the type-only
predicate, that the four `fn_task_retry` payloads carry `retryTarget` /
`executionRetryTarget` / `task.column`, that `commands/task.ts:2049` resolves `retryHoldColumn`
once, and every line number quoted in `tip-excerpts.txt` against the tip's own bytes.

Out-of-band copies of the two remediation commits (identical content, produced before the scratch
checkout was deleted) are attached to this card with the sha256s recorded in
`task document: review-source-proof`: `stas-299-remediation-patch.txt`
(`a3d47dd2…`), `stas-299-card-vs-trunk-diff.txt` (`3f172609…`),
`stas-299-remediation-commits.txt` (`3359afd0…`), `stas-299-remediation-bundle-base64.txt`
(bundle `5f48193c…`, prerequisite `0dd24e8d46`).

## Disclosed and deliberately not implemented (not new findings)

Recorded by `task document: remediation`, deliberately left out of this finding's scope — flagged
here so a reader meets them as known rather than as fresh defects:

- `fn_task_retry`'s own **description string** (`extension.ts:2836-2837`, visible in
  `tip-excerpts.txt`) still reads “move to todo” / “moves to todo or stays in in-review”. On a
  renamed board that prompt string is the same lie told differently; fixing it is a wording change
  outside this finding and outside the card's file scope.
- `buildStoreBootStallResult`'s “do not ask the operator” is unconditional; the reviewer marked
  “retry once, then report” as advisory taste and it was filed as a recommendation instead of
  adding a counter mechanism.
- The reviewer's own `no-verdict-review-stall-park` (3) and `task-retry-stage-route` (5) finding
  sets, plus the 14 + 1 pre-existing CLI failures: the verdict says each deserves its own card.

## What still has to happen (recorded landing state, not a finding)

Measured here, first-hand and read-only, at the moment this directory was committed:

| Measurement | Result |
| --- | --- |
| `rev-list --left-right --count main...fusion/stas-299-selfheal-lifecycle` | `26 2` — the branch is 2 commits ahead of engine `main` (`f2db502381`) and 26 behind |
| What `main` lacks | exactly `50b1771f52` and `dc40314375` — the P0/P1 fix and its boundary test |
| `merge-tree --write-tree --name-only main <branch>` | **exit 0, zero conflicted paths**, merged tree `b175c0616deadf336ef1e1f8af088ad883b6c8b2` — the landing merge needs no conflict resolution |
| `diff --stat main <merged-tree>` | exactly `7 files, +355 / −34`: `extension.ts`, `commands/task.ts`, the three CLI suites, `docs/architecture.md`, the changeset — nothing else |
| Blob identity of every touched path | merged tree == branch tip on all 7 (e.g. `extension.ts` `9a313b6ee7` in both) — the merge introduces no line of content that was not in the tree the suites were run against |
| Engine primary checkout | `/home/schindler/git/Fusion` is on `main` at `f2db502381` with **0** `status --porcelain -uall` records |

So the landing is a clean `--no-ff` merge of already-reviewed, already-tested content, and it has to
be executed **in the operator's own engine checkout**, which this session cannot reach: `main` is
checked out there, and moving that ref from outside would leave the primary worktree's index and
files behind `refs/heads/main` — the operator would open a clean tree to a bogus wall of modified
files. `git merge --ff-only` is separately impossible while the branch trails trunk by 26, and the
`STAS-262` rule forbids resolving that drift by merging trunk into a branch that is about to be
merged back. Opening a PR is operator-gated anyway (`AGENTS.md`, operator directive 2026-08-30).

The one command, in the engine checkout:

```sh
git -C ~/git/Fusion merge --no-ff fusion/stas-299-selfheal-lifecycle
```

The engine half of this card is otherwise complete and — as of this directory — reviewable inside
the gate's own boundary.

## Regenerate / integrity

```sh
sh stas-299-engine-evidence/verify.sh          # re-derive every claim here from the engine's objects
sh stas-299-engine-evidence/generate.sh        # read-only git show/log against the engine repo
cd stas-299-engine-evidence && sha256sum -c MANIFEST.sha256
```

`verify.sh` is the one to run when deciding a verdict: it does not read this README's prose, only
the committed bytes and the engine repository, and it fails loudly (non-zero exit) if a hash, a
snapshot blob, an excerpt line number, a probe token, the clean-checkout state, or the
"no product file touched" bound has drifted.

Re-running is safe at any time, including while a review gate is pending: the generator only reads
the engine repository through `--git-dir` (it checks nothing out) and its output is byte-stable —
headers carry the reviewed commit's date rather than a generation timestamp — so a re-run leaves
`git status --porcelain -uall` empty instead of dirtying the review checkout.

Independent confirmation from the engine repository (read-only):

```sh
git --git-dir=/home/schindler/git/Fusion/.git show --stat 50b1771f52
git --git-dir=/home/schindler/git/Fusion/.git show --stat dc40314375
git --git-dir=/home/schindler/git/Fusion/.git rev-list --left-right --count main...fusion/stas-299-selfheal-lifecycle
```
