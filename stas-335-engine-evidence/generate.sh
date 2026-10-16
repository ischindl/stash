#!/bin/sh
# Regenerates every git-derived evidence file in this directory from the engine repo's objects.
#
# STAS-335 fixes a defect in the Fusion ENGINE (the sibling `Fusion` repository), so the reviewed
# source is not in this checkout and a reviewer of this card cannot open it from the branch alone.
# Everything under this directory that describes code is derived from engine git objects by this
# script — nothing is transcribed by hand — which is what makes ./verify.sh a real check.
#
# Usage (from anywhere):
#   ./generate.sh                                     # writes next to this script
#   OUT_DIR=/tmp/x ./generate.sh                      # writes elsewhere (what verify.sh uses)
#   ENGINE_GITDIR=/path/to/Fusion/.git ./generate.sh   # another engine checkout
#   STAS335_BASE=<sha> STAS335_TIP=<sha> ./generate.sh # review a different head
set -eu

ENGINE="${ENGINE_GITDIR:-/home/schindler/git/Fusion/.git}"
BASE="${STAS335_BASE:-080cfdc646fc1567febf8ac5a51c6d98e150a7a0}"
TIP="${STAS335_TIP:-ca55f0b30b5348ed1368082a3ea47ea6fc263354}"
BRANCH="${STAS335_BRANCH:-fusion/stas-335-taskstore-boot}"

here=$(cd "$(dirname "$0")" && pwd)
out="${OUT_DIR:-$here}"
mkdir -p "$out"

git --git-dir="$ENGINE" rev-parse --verify "$BASE^{commit}" >/dev/null
git --git-dir="$ENGINE" rev-parse --verify "$TIP^{commit}" >/dev/null

EXT=packages/cli/src/extension.ts
FACT=packages/core/src/postgres/startup-factory.ts
ERRS=packages/engine/src/tool-store-errors.ts
TOOLS=packages/engine/src/agent-tools.ts
NEWCLI=packages/cli/src/__tests__/extension-boot-stage-attribution.test.ts
NEWERR=packages/engine/src/__tests__/tool-store-errors.test.ts
READPATH=packages/engine/src/__tests__/read-path-store-failure.test.ts
CHANGES=.changeset/stas-335-store-boot-and-query-attribution.md

# Files small enough to ship whole at tip; the rest are shipped as wide-context hunks below.
SNAPSHOTS="$CHANGES
$ERRS
$NEWERR
$READPATH
$NEWCLI"

# ---------------------------------------------------------------- provenance

{
  echo "engine gitdir        $ENGINE"
  echo "branch               $BRANCH"
  echo "base (branch parent) $BASE  $(git --git-dir="$ENGINE" log -1 --format=%s "$BASE")"
  echo "tip (reviewed head)  $TIP  $(git --git-dir="$ENGINE" log -1 --format=%s "$TIP")"
  echo "commits on branch    $(git --git-dir="$ENGINE" rev-list --count "$BASE..$TIP")"
  echo "diff --shortstat     $(git --git-dir="$ENGINE" diff --shortstat "$BASE" "$TIP" | tr -d '\n')"
  if git --git-dir="$ENGINE" merge-base --is-ancestor "$TIP" refs/heads/main; then
    echo "contained in refs/heads/main: yes (git merge-base --is-ancestor $TIP refs/heads/main -> 0)"
  else
    echo "contained in refs/heads/main: NO (git merge-base --is-ancestor $TIP refs/heads/main -> non-zero)"
  fi
  echo "changed files"
  git --git-dir="$ENGINE" diff --name-status "$BASE" "$TIP" | sed 's/^/  /'
} > "$out/provenance.txt"

# ---------------------------------------------------------------- history / patch

git --git-dir="$ENGINE" log --stat --date=iso-strict \
  --format='commit %H%nAuthor: %an <%ae>%nDate:   %ad%n%n    %s%n%n%b' \
  "$BASE..$TIP" > "$out/commits.txt"

git --git-dir="$ENGINE" diff --stat "$BASE" "$TIP" > "$out/diffstat.txt"

# The whole branch as one patch.
git --git-dir="$ENGINE" diff -U5 "$BASE" "$TIP" > "$out/stas-335-series.diff"

# ---------------------------------------------------------------- invariants

# Each section is a measurement, not a claim: the command is printed with its output, so a
# reviewer can re-run any line by hand. The verdicts are computed here, never written by hand.
{
  echo "STAS-335 invariant checks — derived from git objects at base and tip"
  echo "base $BASE"
  echo "tip  $TIP"
  echo
  echo "== A. The budgets were not raised: the fix reports the stall, it does not outwait it =="
  echo "-- base: git show $BASE:$EXT | grep -E '^const (BOOT_FAILURE_COOLDOWN_MS|EXTENSION_STORE_BOOT_TIMEOUT_MS|EXTENSION_TOOL_TIMEOUT_MS) ='"
  git --git-dir="$ENGINE" show "$BASE:$EXT" \
    | grep -E '^const (BOOT_FAILURE_COOLDOWN_MS|EXTENSION_STORE_BOOT_TIMEOUT_MS|EXTENSION_TOOL_TIMEOUT_MS) ='
  echo "-- tip: same command on $TIP"
  git --git-dir="$ENGINE" show "$TIP:$EXT" \
    | grep -E '^const (BOOT_FAILURE_COOLDOWN_MS|EXTENSION_STORE_BOOT_TIMEOUT_MS|EXTENSION_TOOL_TIMEOUT_MS) ='
  base_consts=$(git --git-dir="$ENGINE" show "$BASE:$EXT" | grep -E '^const (BOOT_FAILURE_COOLDOWN_MS|EXTENSION_STORE_BOOT_TIMEOUT_MS|EXTENSION_TOOL_TIMEOUT_MS) =')
  tip_consts=$(git --git-dir="$ENGINE" show "$TIP:$EXT" | grep -E '^const (BOOT_FAILURE_COOLDOWN_MS|EXTENSION_STORE_BOOT_TIMEOUT_MS|EXTENSION_TOOL_TIMEOUT_MS) =')
  if [ "$base_consts" = "$tip_consts" ]; then
    echo "VERDICT A: every boot/tool/cooldown constant is byte-identical at base and tip."
  else
    echo "VERDICT A: DIFFERS — a constant changed between base and tip."
  fi
  echo
  echo "-- No new retry loop: added lines in source files that introduce looping or swallowing."
  echo "   (the guidance text 'Retry this same call' is pre-existing and lives on context lines, not added ones)"
  if git --git-dir="$ENGINE" diff "$BASE" "$TIP" -- "$EXT" "$FACT" "$ERRS" "$TOOLS" \
       | grep -E '^\+' | grep -vE '^\+\+\+' \
       | grep -E 'for \(|while \(|retry\(|\.catch\(\(\) => \{\}\)|catch \{ *\}' ; then
    echo "VERDICT A2: loops/swallowers found on added lines — inspect above."
  else
    echo "VERDICT A2: no loop, no empty catch, no retry call was added to any changed source file."
  fi
  echo
  echo "== B. The stop-authority rules are untouched: only the process that started the embedded postmaster may stop it =="
  echo "-- every hunk header of the two files that own the embedded-postmaster lifecycle."
  echo "   git shows the enclosing declaration after '@@'; a hunk naming a stop-authority function would be the bug."
  echo "--- $EXT"
  git --git-dir="$ENGINE" diff -U3 "$BASE" "$TIP" -- "$EXT" | grep -E '^@@'
  echo "--- $FACT"
  git --git-dir="$ENGINE" diff -U3 "$BASE" "$TIP" -- "$FACT" | grep -E '^@@'
  echo "-- stop-authority declarations that the series does NOT land a hunk in:"
  hits=$(git --git-dir="$ENGINE" diff -U3 "$BASE" "$TIP" -- "$EXT" "$FACT" | grep -E '^@@' \
    | grep -E 'stopEmbeddedRuntime|releaseEmbeddedRuntimeLease|closeCachedStores|setHostTaskStore|clearHostTaskStores|session_shutdown|detachKeepingEmbedded|detachWithoutStop' || true)
  if [ -z "$hits" ]; then
    echo "  (none — VERDICT B: no hunk in the series is inside stopEmbeddedRuntime, releaseEmbeddedRuntimeLease,"
    echo "   closeCachedStores, setHostTaskStore, clearHostTaskStores, the session_shutdown handler, or a detach path.)"
  else
    echo "$hits"
    echo "VERDICT B: a hunk lands in a stop-authority function — review it."
  fi
  echo
  echo "-- every changed line that mentions a budget or stop-authority token, listed per file. There are only"
  echo "   these; README.md classifies each one (two are prose, one pair is a throw re-wrapped across lines)."
  git --git-dir="$ENGINE" diff --name-only "$BASE" "$TIP" | while IFS= read -r f; do
    found=$(git --git-dir="$ENGINE" diff "$BASE" "$TIP" -- "$f" \
      | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
      | grep -E 'EXTENSION_STORE_BOOT_TIMEOUT_MS|EXTENSION_TOOL_TIMEOUT_MS|stopEmbeddedRuntime|releaseEmbeddedRuntimeLease|session_shutdown|detachKeepingEmbedded|detachWithoutStop' || true)
    if [ -n "$found" ]; then printf '  %s\n%s\n' "$f" "$found"; fi
  done
  echo
  echo "-- the only new try/catch pair in source, which is the read site that must answer WITH attribution"
  echo "   rather than rethrow the driver's echo. Listed so a reviewer can see no other swallow was added."
  git --git-dir="$ENGINE" diff "$BASE" "$TIP" -- "$EXT" "$FACT" "$ERRS" "$TOOLS" \
    | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
    | grep -E 'try \{|catch' || echo "  (none)"
  echo
  echo "== C. The surfaced diagnostic is credential-safe =="
  echo "-- added lines in source files carrying a credential, connection string, or bound-parameter read."
  echo "   ('params' as a WORD appears only in comments explaining that it is never read; '.params' is the read)"
  if git --git-dir="$ENGINE" diff "$BASE" "$TIP" -- "$EXT" "$FACT" "$ERRS" "$TOOLS" \
       | grep -E '^\+' | grep -vE '^\+\+\+' \
       | grep -E 'postgres://|password|connectionString|DATABASE_URL|\.params|params\[|error\.detail|cause\.detail'; then
    echo "VERDICT C: a credential-bearing read appears on an added line — inspect above."
  else
    echo "VERDICT C: no added source line reads a password, a connection string, bound parameters, or a driver detail."
  fi
  echo "-- the pins that hold it, verbatim from tip:"
  git --git-dir="$ENGINE" show "$TIP:$NEWERR" | grep -nE 's3cret|params|not\.toMatch' | sed 's/^/  /'
  git --git-dir="$ENGINE" show "$TIP:$NEWCLI" | grep -nE 's3cret|postgres://|not\.toMatch' | sed 's/^/  /'
  echo
  echo "== D. The new pins cannot pass pre-fix code (red-first) =="
  echo "-- occurrences of each seam symbol introduced by this series, base vs tip"
  echo "   (git show <ref>:<file> is the file's whole content; base has no such symbol, so the import fails there)"
  for sym in onBootStage BootStage formatBootStages describeStoreQueryFailure StoreQueryAttribution; do
    b=$(git --git-dir="$ENGINE" show "$BASE:$EXT" "$BASE:$FACT" "$BASE:$ERRS" | grep -c "$sym" || true)
    t=$(git --git-dir="$ENGINE" show "$TIP:$EXT" "$TIP:$FACT" "$TIP:$ERRS" | grep -c "$sym" || true)
    printf '  %-26s base=%s tip=%s\n' "$sym" "$b" "$t"
  done
  echo "-- the store-error composer's arity, which is what makes a read distinguishable from a write"
  echo -n "  base: "; git --git-dir="$ENGINE" show "$BASE:$ERRS" | grep -E '^export function storeErrorResult'
  echo -n "  tip:  "; git --git-dir="$ENGINE" show "$TIP:$ERRS" | grep -E '^export function storeErrorResult'
  echo "-- new test files, absent from base entirely:"
  for f in "$NEWCLI" "$NEWERR"; do
    if git --git-dir="$ENGINE" cat-file -e "$BASE:$f" 2>/dev/null; then
      printf '  %s present at base\n' "$f"
    else
      printf '  %s absent at base (the pins are new files)\n' "$f"
    fi
  done
  echo -n "  createListAgentsTool pins in $READPATH: base="
  git --git-dir="$ENGINE" show "$BASE:$READPATH" | grep -c 'createListAgentsTool' || true
  echo -n " ; tip="
  git --git-dir="$ENGINE" show "$TIP:$READPATH" | grep -c 'createListAgentsTool' || true
  echo
} > "$out/invariants.txt" 2>&1

# ---------------------------------------------------------------- readable code

# Full tip copies for the small files, so the reviewer reads exactly what the tip contains.
printf '%s\n' "$SNAPSHOTS" | while IFS= read -r f; do
  [ -n "$f" ] || continue
  mkdir -p "$out/snapshot/$(dirname "$f")"
  git --git-dir="$ENGINE" show "$TIP:$f" > "$out/snapshot/$f"
done

# The two oversized files (extension.ts ~360 KB, agent-tools.ts ~350 KB) and the factory ship as
# wide-context hunks instead of whole files, plus the stop-authority regions they must NOT have
# changed, printed from the tip blob around a fixed anchor so the rule is read in context.
{
  echo "STAS-335 tip excerpts"
  echo "Part 1 — the changed code in wide context (git diff -U60) for the files too large to ship whole."
  echo "         Files: $EXT, $TOOLS"
  echo "========================================================================================="
  git --git-dir="$ENGINE" diff -U60 "$BASE" "$TIP" -- "$EXT" "$TOOLS"
  echo
  echo "Part 2 — the stop-authority rules at the tip, printed from the tip blob (unchanged by this series)."
  echo "         Each window is located by the literal quoted in its header."
  echo "========================================================================================="
} > "$out/tip-excerpts.txt"

window() {
  # window <path> <literal> <lines before> <lines after>
  f=$1; lit=$2; pre=$3; post=$4
  ln=$(git --git-dir="$ENGINE" show "$TIP:$f" | grep -nF -- "$lit" | head -1 | cut -d: -f1)
  if [ -z "$ln" ]; then
    printf '\n!! anchor not found: %s in %s\n' "$lit" "$f" >> "$out/tip-excerpts.txt"
    return 1
  fi
  start=$((ln - pre)); [ "$start" -lt 1 ] && start=1
  {
    printf '\n--- %s  (anchor "%s" at line %s)\n' "$f" "$lit" "$ln"
    git --git-dir="$ENGINE" show "$TIP:$f" | sed -n "${start},$((ln + post))p"
  } >> "$out/tip-excerpts.txt"
}

window "$FACT" "defers that owner stop until joined leases release" 14 28
window "$FACT" "Close the TaskStore/pools and release the runtime lease but LEAVE an" 22 14
window "$FACT" "// Close the TaskStore first (releases the AsyncDataLayer / pool), then" 2 40
window "$EXT" "readonly external?: boolean;" 14 6
window "$EXT" "The entry is external: closeCachedStores / clearHostTaskStores will not shut it down" 4 30
window "$EXT" "pi.on(\"session_shutdown\"" 4 22

echo "generated into $out:"
echo "  provenance.txt commits.txt diffstat.txt stas-335-series.diff invariants.txt tip-excerpts.txt"
printf '  snapshot/%s\n' $(printf '%s\n' "$SNAPSHOTS" | tr '\n' ' ')
