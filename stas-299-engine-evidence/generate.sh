#!/bin/sh
# STAS-299 — regenerate the reviewable evidence for the engine-side change.
#
# The engine product code for this card lives in the sibling repository `Fusion`
# (/home/schindler/git/Fusion), which the code-review gate cannot read: its boundary is this
# stash worktree. This script copies the committed engine change INTO the worktree so every
# hunk of STAS-299 is inspectable and greppable inside that boundary.
#
# It is read-only against the engine repository: `git show` / `git log` only. It never writes
# outside this directory and never mutates an engine ref.
#
# Usage:  sh stas-299-engine-evidence/generate.sh
set -eu

ENGINE_GIT_DIR=/home/schindler/git/Fusion/.git
BRANCH=fusion/stas-299-selfheal-lifecycle
TIP=dc40314375bd29b3b6262b467ebfe8ea8ed99e7e
BASE=47fe311d6d
# Deterministic provenance stamp: the reviewed commit's own date, not the generation time. A
# generation timestamp would make every re-run rewrite four headers and dirty the working tree —
# and a dirty review checkout is exactly what refuseUncommittedApproval refuses.
SRC_DATE=$(git --git-dir="$ENGINE_GIT_DIR" log -1 --format=%cI "$TIP")
OUT=$(dirname "$0")

# Every non-merge commit authored for STAS-299, chronological (oldest first).
SERIES="1e00f94c9b 3b55220aee 533ebc973d 83afa89542 87196c65b9 d8480855a6 ffff0ff38a f322d89502 806529251e 60efd08b50 56c7767471 50b1771f52 dc40314375"
# The two commits answering the code-review REVISE that requested this evidence.
REMEDiation="50b1771f52 dc40314375"

# Files STAS-299 added (status A) — copied verbatim at the tip so the new shared class and
# its suites can be read whole rather than reassembled from hunks.
ADDED="packages/core/src/tasks/stranded-gate-retry.ts
packages/core/src/__tests__/stranded-gate-retry.test.ts
packages/engine/src/__tests__/self-healing-orphaned-pending-lifecycle-ownership.test.ts
packages/engine/src/__tests__/stranded-gate-park-ownership.test.ts
packages/cli/src/__tests__/extension-store-boot-transience.test.ts
packages/cli/src/__tests__/extension-stranded-gate-retry.test.ts
packages/cli/src/__tests__/extension-retry-resolved-target-report.test.ts
packages/dashboard/src/routes/__tests__/register-task-workflow-routes.stranded-gate-retry.test.ts
.changeset/self-heal-gate-park-and-retry.md"

mkdir -p "$OUT/snapshot"

# ── 1. commit series with per-commit stats ────────────────────────────────────────────────
{
  echo "# STAS-299 — engine commit series (chronological, non-merge)"
  echo "# engine repo: /home/schindler/git/Fusion   branch: $BRANCH"
  echo "# fork base:   $BASE        reviewed tip: $TIP"
  echo "# reviewed commit date: $SRC_DATE  (byte-stable output; regenerate with stas-299-engine-evidence/generate.sh)"
  echo "#"
  echo "# reproduce with:  git --git-dir=$ENGINE_GIT_DIR log -1 --stat <sha>"
  for c in $SERIES; do
    echo "================================================================================"
    git --git-dir="$ENGINE_GIT_DIR" log -1 --stat --format='commit %H%nAuthor: %an <%ae>%nDate:   %ad%n%n%B' "$c"
    echo
  done
} > "$OUT/commits.txt"

# ── 2. the whole committed engine diff (13 commits, 3 lines of context) ───────────────────
{
  echo "# STAS-299 — the committed engine diff, every non-merge commit on $BRANCH"
  echo "# engine repo: /home/schindler/git/Fusion   fork base: $BASE   tip: $TIP"
  echo "# reviewed commit date: $SRC_DATE  (byte-stable output; regenerate with stas-299-engine-evidence/generate.sh)"
  echo "#"
  echo "# This is the complete STAS-299 change (engine requirements 1-4 + Invariant C):"
  echo "#   1e00f94c9b  engine   self-heal stamps the stranded hold-lane card failed  (req 1,2)"
  echo "#   533ebc973d  core     why a card is NOT the stranded-gate class"
  echo "#   83afa89542  engine   a failure park owns the card until a retry discharges it"
  echo "#   3b55220aee  core     the stranded-gate retry class named once               (req 2,4)"
  echo "#   d8480855a6  core     the class classified against the card's own workflow"
  echo "#   ffff0ff38a  core     the class stays a pure function of card + workflow"
  echo "#   87196c65b9  cli      a stalled TaskStore boot answers with a retry          (req 3)"
  echo "#   f322d89502  retry    one verdict-less gate answer on all four surfaces      (req 4, Inv. C)"
  echo "#   806529251e  test     gate discharge on every retry surface                  (req 4)"
  echo "#   60efd08b50  test     the STAS-281 card from sweep through park to hatch     (reqs 1-3)"
  echo "#   56c7767471  docs     changeset, architecture, task-management, skill refs"
  echo "#   50b1771f52  FIX      code-review P0 (tool budget != boot stall) + P1 (resolved lane)"
  echo "#   dc40314375  test     pin the one place allowed to read a timeout sentence"
  echo "#"
  echo "# Read the two FIX commits first: remediation.diff holds them with 25 lines of context."
  echo
  for c in $SERIES; do
    echo "################################################################################"
    echo "## COMMIT $c"
    echo "################################################################################"
    git --git-dir="$ENGINE_GIT_DIR" show --format=fuller --unified=3 --no-color "$c"
    echo
  done
} > "$OUT/stas-299-series.diff"

# ── 3. the two remediation commits, wide context ─────────────────────────────────────────
{
  echo "# STAS-299 — remediation diff (the commits answering the code-review REVISE)"
  echo "#   50b1771f52  P0: a tool over its own budget is answered as THAT tool's failure, never as a"
  echo "#               stalled store boot (classification is the TYPE, not the message)"
  echo "#               P1: every retry report names the lane it resolved (details.newColumn included)"
  echo "#   dc40314375  test: pin the boot boundary as the only place allowed to read a timeout sentence"
  echo "#"
  echo "# reviewed commit date: $SRC_DATE  (byte-stable output; regenerate with stas-299-engine-evidence/generate.sh)"
  echo
  for c in $REMEDiation; do
    echo "################################################################################"
    echo "## COMMIT $c"
    echo "################################################################################"
    git --git-dir="$ENGINE_GIT_DIR" show --format=fuller --unified=25 --no-color "$c"
    echo
  done
} > "$OUT/remediation.diff"

# ── 4. line-numbered excerpts of the touched regions AT THE TIP ──────────────────────────
# The reviewer cites absolute line numbers; a diff cannot answer those. Each block below is the
# file as committed at $TIP, prefixed with its real 1-based line numbers.
{
  echo "# STAS-299 — line-numbered excerpts at the reviewed tip $TIP"
  echo "# Every block is the committed content of the named engine file, line numbers = real file lines."
  echo "# reviewed commit date: $SRC_DATE  (byte-stable output; regenerate with stas-299-engine-evidence/generate.sh)"
  echo
  for spec in \
    "packages/cli/src/extension.ts:386:480:boot budget const, TaskStoreBootTransientError, isTransientStoreBootFailure (P0 site), buildStoreBootStallResult" \
    "packages/cli/src/extension.ts:530:600:raceWithTimeoutAndAbort — the shared '<label> timed out after <N>ms' sentence builder" \
    "packages/cli/src/extension.ts:620:700:wrapExtensionToolExecute catch — the P0 answer site (tool budget vs typed boot stall)" \
    "packages/cli/src/extension.ts:750:845:boot factory deadline + getStore: the one place allowed to classify a timeout sentence" \
    "packages/cli/src/extension.ts:1332:1400:policy gate — typed transient vs typed infra error (requirement 3)" \
    "packages/cli/src/extension.ts:1540:1580:a real policy-resolution denial still denies (deniedFor agent-permission-policy-unavailable)" \
    "packages/cli/src/extension.ts:2830:2900:fn_task_retry tool description - the stranded-gate half is stated; its 'moves to todo' wording is the disclosed not-implemented item" \
    "packages/cli/src/extension.ts:2975:3110:fn_task_retry re-queues — P1: resolved target in log, reply text and details.newColumn" \
    "packages/cli/src/commands/task.ts:1884:1935:CLI retry classifier — the same shared class (parity)" \
    "packages/cli/src/commands/task.ts:2010:2200:CLI generic re-queue — P1: reports name the resolved hold lane" \
    "packages/engine/src/self-healing.ts:11280:11360:the sweep that finds the orphaned gate row, and the hold-lane gate it must respect" \
    "packages/engine/src/self-healing.ts:11440:11560:the lifecycle write that fails the stranded hold-lane card (requirements 1-2)" \
    "packages/dashboard/src/routes/register-task-workflow-routes.ts:3795:3900:retry route — the class guard that runs before the destructive branch" \
    "packages/dashboard/src/routes/register-task-workflow-routes.ts:3990:4075:the destructive continuation + the class's own non-destructive discharge" \
    ; do
    f=$(echo "$spec" | cut -d: -f1); a=$(echo "$spec" | cut -d: -f2); b=$(echo "$spec" | cut -d: -f3); w=$(echo "$spec" | cut -d: -f4-)
    echo "================================================================================"
    echo "### $f  lines $a-$b"
    echo "### why: $w"
    echo "================================================================================"
    git --git-dir="$ENGINE_GIT_DIR" show "$TIP:$f" | sed -n "${a},${b}p" | awk -v s="$a" '{printf "%6d | %s\n", s++, $0}'
    echo
  done
} > "$OUT/tip-excerpts.txt"

# ── 5. verbatim copies of the files STAS-299 added ───────────────────────────────────────
for f in $ADDED; do
  mkdir -p "$OUT/snapshot/$(dirname "$f")"
  git --git-dir="$ENGINE_GIT_DIR" show "$TIP:$f" > "$OUT/snapshot/$f"
done

# ── 6. P1 audit: every details.newColumn reply site at the tip ─────────────────────────
# P1 asked that a retry report name the lane it resolved, on every surface. This is the whole
# population of that field at the tip, so "fixed" is judged against all of it, not only the three
# sites the finding happened to name.
{
  echo "# STAS-299 — audit of every details.newColumn reply site in the engine"
  echo "# reviewed commit date: $SRC_DATE  (byte-stable output; regenerate with stas-299-engine-evidence/generate.sh)"
  echo "# tip: $TIP"
  echo "# Expected end state: no hardcoded lane literal anywhere — each site carries a resolved target"
  echo "# (retryTarget / executionRetryTarget) or the card's own column (task.column)."
  echo "#"
  for f in packages/cli/src/extension.ts packages/engine/src/agent-tools.ts \
           packages/dashboard/src/routes/register-task-workflow-routes.ts packages/cli/src/commands/task.ts; do
    echo "--------------------------------------------------------------------------------"
    echo "### $f"
    echo "--------------------------------------------------------------------------------"
    git --git-dir="$ENGINE_GIT_DIR" show "$TIP:$f" | grep -n 'newColumn' || true
    echo
  done
  echo "================================================================================"
  literals=$(git --git-dir="$ENGINE_GIT_DIR" show "$TIP:packages/cli/src/extension.ts" "$TIP:packages/engine/src/agent-tools.ts" | grep -oE "newColumn: [\"']" | wc -l | tr -d ' ')
  echo "hardcoded lane literals in a newColumn payload, extension + chat surfaces, at the tip: $literals (expected 0)"
  before=$(git --git-dir="$ENGINE_GIT_DIR" show "0dd24e8d46:packages/cli/src/extension.ts" "0dd24e8d46:packages/engine/src/agent-tools.ts" | grep -oE "newColumn: [\"']" | wc -l | tr -d ' ')
  echo "the identical count at 0dd24e8d46 (the reviewed tip these two commits answer): $before"
  echo "# The second number is what makes the first one mean something: the counter is not vacuous,"
  echo "# it measured the literals that were there before the fix and reports them gone."
} > "$OUT/newcolumn-audit.txt"

# ── 7. manifest ───────────────────────────────────────────────────────────────────────
# Last, so it hashes the files as this run left them. README.md is hand-authored guidance rather
# than generated output, so it stays outside the manifest — editing the guide must not invalidate
# hashes.
(
  cd "$OUT"
  find . -type f ! -name MANIFEST.sha256 ! -name README.md -print | sort | sed 's|^\./||' | while read -r f; do
    sha256sum "$f"
  done
) > "$OUT/MANIFEST.sha256"

echo "evidence regenerated in $OUT"
