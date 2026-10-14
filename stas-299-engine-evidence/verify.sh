#!/bin/sh
# STAS-299 — verify that this directory still tells the truth about the engine repository.
#
# The defect this card is about is a gate approving work it cannot read. A README that merely
# ASSERTS "the fix is committed" repeats that failure in prose, so every claim here is checkable:
# this script re-derives the evidence from the engine repository's objects and compares it with the
# bytes committed in this stash worktree. Read-only against both repositories; it writes nothing.
#
# Usage (from anywhere in this checkout):  sh stas-299-engine-evidence/verify.sh
# Prints PASS/FAIL per check and exits with the number of failed checks.
set -eu

EV=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
REL=${EV#"$ROOT/"}

ENGINE_GIT_DIR=${ENGINE_GIT_DIR:-/home/schindler/git/Fusion/.git}
BRANCH=${ENGINE_BRANCH:-fusion/stas-299-selfheal-lifecycle}
TIP=${ENGINE_TIP:-$(git --git-dir="$ENGINE_GIT_DIR" rev-parse "$BRANCH")}
fails=0

say() {
  # $1 = PASS|FAIL, $2 = what was measured, $3 = the measurement
  printf '%-4s %-56s %s\n' "$1" "$2" "$3"
  [ "$1" = PASS ] || fails=$((fails + 1))
}

if [ ! -d "$ENGINE_GIT_DIR" ]; then
  echo "engine git dir is not readable: $ENGINE_GIT_DIR (override with ENGINE_GIT_DIR)" >&2
  exit 2
fi
printf 'verifying %s against engine tip %.10s (%s)\n\n' "$REL" "$TIP" "$BRANCH"

# 1 — every committed artifact hashes to what the manifest recorded.
if ( cd "$EV" && sha256sum -c MANIFEST.sha256 >/dev/null 2>&1 ); then
  say PASS "manifest: generated artifacts match recorded hashes" "$(wc -l < "$EV/MANIFEST.sha256" | tr -d ' ') files"
else
  say FAIL "manifest: a committed artifact differs from MANIFEST.sha256" "run: sh $REL/generate.sh"
fi

# 2 — the snapshot copies are the engine's own bytes, not an edited paraphrase.
bad=0; total=0
while IFS= read -r f; do
  total=$((total + 1))
  rel=${f#"$EV"/snapshot/}
  upstream=$(git --git-dir="$ENGINE_GIT_DIR" show "$TIP:$rel" | sha256sum | cut -d' ' -f1)
  here=$(sha256sum "$f" | cut -d' ' -f1)
  [ "$upstream" = "$here" ] || bad=$((bad + 1))
done <<SCOPE
$(find "$EV/snapshot" -type f)
SCOPE
if [ "$bad" -eq 0 ]; then
  say PASS "snapshot: added files identical to the engine tip's blobs" "$total files"
else
  say FAIL "snapshot: files differ from the engine tip" "$bad of $total"
fi

# 3 — the line-numbered excerpts are faithful: every numbered row is re-pulled from the engine
#     object and compared. A cited line number that no longer resolves is the failure mode this
#     card exists to eliminate, so it is checked row by row rather than sampled.
if python3 - "$EV/tip-excerpts.txt" "$ENGINE_GIT_DIR" "$TIP" <<'PY'
import re, subprocess, sys

excerpt_path, git_dir, tip = sys.argv[1], sys.argv[2], sys.argv[3]
header = re.compile(r"^### (\S+)  lines ")
row = re.compile(r"^\s*(\d+) \| (.*)$")

source, path, checked, mismatched = None, None, 0, []
for line in open(excerpt_path, encoding="utf-8").read().split("\n"):
    head = header.match(line)
    if head:
        path = head.group(1)
        data = subprocess.run(["git", f"--git-dir={git_dir}", "show", f"{tip}:{path}"],
                              capture_output=True, check=True).stdout.decode()
        source = data.split("\n")
        continue
    found = row.match(line)
    if found and path:
        n, text = int(found.group(1)), found.group(2)
        checked += 1
        if source[n - 1] != text:
            mismatched.append(f"{path}:{n}")

for miss in mismatched[:10]:
    print("   mismatched row:", miss)
print(("PASS" if not mismatched else "FAIL"),
      "tip-excerpts: numbered rows match the engine blob exactly".ljust(56),
      f"{checked} rows, {len(mismatched)} mismatched")
sys.exit(0 if not mismatched else 1)
PY
then :; else fails=$((fails + 1)); fi

# 4 — the probes the review gate reported as zero matches now resolve inside this checkout, under
#     both a plain grep and a default ripgrep (ripgrep skips hidden directories, which is why this
#     evidence sits at a visible path instead of `.engine/`).
for token in STAS-299 FN-8492 TaskStoreBootTransientError isTransientStoreBootFailure strandedGateRetry RetryReportCarriesTarget; do
  hits=$(grep -rlI --exclude-dir=.git -- "$token" . 2>/dev/null | wc -l | tr -d ' ')
  if [ "$hits" -gt 0 ]; then say PASS "probe resolves inside the review boundary: $token" "$hits files"
  else say FAIL "probe finds nothing inside the review boundary: $token" "0 files"; fi
done
if command -v rg >/dev/null 2>&1; then
  hits=$(rg -l --no-messages 'STAS-299' . 2>/dev/null | wc -l | tr -d ' ')
  if [ "$hits" -gt 0 ]; then say PASS "default ripgrep sees the evidence (path is not hidden)" "$hits files"
  else say FAIL "ripgrep cannot see the evidence" "0 files"; fi
fi

# 5 — the clean-checkout probe that refused the earlier approval: refuseUncommittedApproval runs
#     exactly this command against the review checkout.
dirty=$(git status --porcelain=v1 -z --untracked-files=all | tr -cd '\000' | wc -c | tr -d ' ')
if [ "$dirty" -eq 0 ]; then
  say PASS "review checkout is clean for refuseUncommittedApproval" "0 records"
else
  say FAIL "review checkout carries uncommitted/untracked records" "$dirty records"
fi

# 6 — this evidence is evidence, not smuggled product code: every path in the commit lives here.
outside=$(git show --name-only --format= HEAD | grep -v "^$" | grep -v "^$REL/" | wc -l | tr -d ' ')
if [ "$outside" -eq 0 ]; then
  say PASS "evidence commit touches no file outside this directory" "0 paths"
else
  say FAIL "evidence commit reaches outside $REL" "$outside paths"
fi

echo
if [ "$fails" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "$fails CHECK(S) FAILED"; fi
exit "$fails"
