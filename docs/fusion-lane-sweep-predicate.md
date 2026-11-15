# Deciding whether a `fusion/*` lane may be deleted

The predicate a lane or worktree sweep must evaluate before it deletes anything under
`refs/heads/fusion/` or `.fusion/worktrees/`. STAS-466 measured it across all 46 lane refs on
2026-11-15 and shipped it here because the two obvious shortcuts each destroy something: one makes
the sweep a no-op, the other deletes content or the audit trail a landing depends on.

A lane is deletable only when **all** of these hold, evaluated in this order and never short-circuited
by a hunch about the lane's age:

1. `column == 'done'` — read from the store, not inferred from the last log line.
2. `mergeDetails.mergeConfirmed == true` — also a store read.
3. When `mergeDetails.commitSha` is present, `git merge-base --is-ancestor <commitSha> refs/heads/main`
   exits `0`. When it is absent **only** because finalization recorded the no-op proof (reason
   `no-net-changes`), that substitution applies and the sweep must print that it used it.
4. `git diff --quiet refs/heads/main...<lane>` exits `0` — the lane contributes no content trunk lacks.
5. `git cherry -v refs/heads/main <lane>` — report the `+` count. Patch-id is **evidence, never a
   conjunct**: this project lands by squash (`mergeStrategy=direct`,
   `directMergeCommitStrategy=always-squash`), so a legitimate landing changes patch-ids by design.

Conjunct 4 is what makes the sweep safe, and conjunct 1 is what keeps the audit trail alive.

## Why `commitSha` is the trunk-side commit, and where it is missing

The merger writes the record at `packages/engine/src/merge/merger-ai.ts:3921` as
`commitSha: landedSha`, keeping the lane tip separately in `landedBranchTipSha`, so containment of
`commitSha` in trunk is the right test in this repository — it is not testing whether the lane was
merged, it is testing whether the commit finalization created is reachable from today's trunk. A
no-op finalization at `:4020` writes **no** `commitSha` at all while still stamping
`mergeConfirmed: true`, because the clean-room loop proved the branch has no net diff. That is exactly
the set of lanes where conjunct 3 has no operand, which is why conjunct 4 exists rather than a
substitute invented at sweep time.

## The two rejected variants, each with the row that demonstrates it

- **Over-strict** — "never delete a lane whose `rev-list` right-hand count is non-zero". Measured at
  trunk `982214ee4`, 19 of 46 lanes match that description, including squash-landed and deletable
  ones, so the sweep frees nothing and the disk pressure that motivated it persists.
- **Over-loose** — "deletable whenever `done` and `mergeConfirmed`, or whenever patch-ids match".
  `stas-184` satisfies all three record conjuncts and still held 11 commits trunk did not have; same
  shape for `stas-217`, `stas-232`, `stas-248`. `stas-189` held `mergeConfirmed: true` from a no-op
  proof while carrying 5 commits and a non-empty net diff. Deleting any of those on the strength of
  `done` plus `mergeConfirmed` destroys content. The mirror failure: `stas-117`'s landed commit
  `f346cd00448` was reachable only from `archive/fold-b-cleanup-2026-08-30` and
  `quarantine/stas-137-wrong-trunk-merge`, so `mergeConfirmed: true` does not even imply trunk ever
  contained the commit the record names.

At the census trunk, 5 of 46 lanes were eligible under the declared substitution (`stas-185`, `stas-186`,
`stas-239`, `stas-246`, `stas-252`), 1 conditional, 40 not deletable.

## Two states a sweep must not read as a deletable lane

- **A lane ref can vanish without a sweep.** The engine's own finalization removes the ref of a lane it
  has landed — `fusion/stas-459` was gone as soon as its landing commit `1352e4084` became the trunk
  tip. A census list of lane refs re-read an hour later differs; that is not an anomaly to chase.
- **A card can be complete on disk and invisible to every tool.** `fn_task_show`, `fn_task_list` and
  `fn_task_retry` all resolve through `ACTIVE_TASK_FILTER = deletedAt IS NULL`
  (`packages/core/src/task-store/async/async-persistence.ts:50`), and the reconciler that would
  re-import `.fusion/tasks/<ID>/task.json` refuses at `taskIdExistsAnywhere` because that check counts
  tombstones. Such a card's lane is **not** deletable by a sweep: the ref is then the only holder of
  its commits and no agent can retry or close the card that owns them (measured on `stas-460`, 6
  lane-only commits). The restore lever is the dashboard-API unarchive route, which an operator runs.

## Before any deletion, and before any count is written down

Preserve first, then decide: `git bundle create <file> refs/heads/fusion/<lane>` plus an annotated tag
(`git tag archive/fusion-stas-460 refs/heads/fusion/stas-460`) makes either verdict reversible. A thin
bundle is only as durable as the repository that holds its prerequisite — verify with
`git bundle verify` and read the reply. Every count quoted from a lane is void without the trunk SHA
it was measured against: trunk moved four times inside a single hour on 2026-11-15 and a lane's
`left-right` count changed from `2 6` to `4 6` with no lane-side action at all.
