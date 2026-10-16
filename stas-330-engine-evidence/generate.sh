#!/bin/sh
# Regenerates the git-derived evidence files in this directory from the engine repo's objects.
#
# The engine code STAS-330 changes lives in the sibling Fusion repository, not in this checkout,
# so a reviewer of this card reads the change through these files. Everything here is derived from
# git objects with no manual transcription, which is what makes verify.sh meaningful.
#
# Usage (from anywhere):
#   ./generate.sh                                   # writes next to this script
#   OUT_DIR=/tmp/x ./generate.sh                    # writes elsewhere (what verify.sh uses)
#   ENGINE_GITDIR=/path/to/Fusion/.git ./generate.sh
set -eu

ENGINE="${ENGINE_GITDIR:-/home/schindler/git/Fusion/.git}"
BASE="${STAS330_BASE:-bc2f242f6ba05c1d76b2e6548755be7c8b9e2278}"
TIP="${STAS330_TIP:-0c2fe67fe401b135926ec8c4952dfcc6ddb44b3d}"
FOLD="${STAS330_FOLD:-0c2fe67fe401b135926ec8c4952dfcc6ddb44b3d}"
BRANCH="${STAS330_BRANCH:-fusion/stas-330-wake-comment-delta-consumption}"

here=$(cd "$(dirname "$0")" && pwd)
out="${OUT_DIR:-$here}"
mkdir -p "$out"

git --git-dir="$ENGINE" rev-parse --verify "$BASE^{commit}" >/dev/null
git --git-dir="$ENGINE" rev-parse --verify "$TIP^{commit}" >/dev/null

{
  echo "engine gitdir        $ENGINE"
  echo "branch               $BRANCH"
  echo "base (branch parent) $BASE"
  echo "tip (reviewed head)  $TIP"
  echo "fold commit          $FOLD"
  echo "commits on branch    $(git --git-dir="$ENGINE" rev-list --count "$BASE..$TIP")"
  echo "diff --shortstat     $(git --git-dir="$ENGINE" diff --shortstat "$BASE" "$TIP" | tr -d '\n')"
  echo "fold --shortstat     $(git --git-dir="$ENGINE" diff --shortstat "$FOLD^" "$FOLD" | tr -d '\n')"
  echo "changed files"
  git --git-dir="$ENGINE" diff --name-only "$BASE" "$TIP" | sed 's/^/  /'
} > "$out/provenance.txt"

git --git-dir="$ENGINE" log --stat --date=iso-strict \
  --format='commit %H%nAuthor: %an <%ae>%nDate:   %ad%n%n    %s%n%n%b' \
  "$BASE..$TIP" > "$out/commits.txt"

git --git-dir="$ENGINE" diff --stat "$BASE" "$TIP" > "$out/diffstat.txt"

# The whole branch as one patch: the advertise-once implementation, its tests, docs and changeset.
git --git-dir="$ENGINE" diff -U5 "$BASE" "$TIP" > "$out/stas-330-series.diff"

# The single commit that answers the code review (F1 wording must-fix + advisories A1/A2).
git --git-dir="$ENGINE" show -U15 --format=fuller "$FOLD" > "$out/remediation.diff"

echo "generated into $out: provenance.txt commits.txt diffstat.txt stas-330-series.diff remediation.diff"
