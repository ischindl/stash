#!/bin/sh
# Verifies the STAS-335 evidence bundle:
#   1. every git-derived file re-derives byte-for-byte from the engine repo right now;
#   2. the reviewed tip is still contained in the engine's local main;
#   3. the non-generated files (this script, README.md, verification.txt) still match MANIFEST.sha256.
#
# Run from anywhere: ./verify.sh   (engine checkout override: ENGINE_GITDIR=/path/to/Fusion/.git ./verify.sh)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
ENGINE="${ENGINE_GITDIR:-/home/schindler/git/Fusion/.git}"
BASE="${STAS335_BASE:-080cfdc646fc1567febf8ac5a51c6d98e150a7a0}"
TIP="${STAS335_TIP:-ca55f0b30b5348ed1368082a3ea47ea6fc263354}"
BRANCH="${STAS335_BRANCH:-fusion/stas-335-taskstore-boot}"

tmp=${STAS335_VERIFY_TMP:-$here/.verify-scratch}
rm -rf "$tmp"; mkdir -p "$tmp"
trap 'rm -rf "$tmp"' EXIT INT TERM

OUT_DIR="$tmp" ENGINE_GITDIR="$ENGINE" STAS335_BASE="$BASE" STAS335_TIP="$TIP" STAS335_BRANCH="$BRANCH" \
  sh "$here/generate.sh" > /dev/null

fail=0
for f in provenance.txt commits.txt diffstat.txt stas-335-series.diff invariants.txt tip-excerpts.txt; do
  if diff -u "$here/$f" "$tmp/$f" > "$tmp/.$f.diff"; then
    echo "OK       $f"
  else
    echo "MISMATCH $f — re-run generate.sh against the engine repo, then re-check README/verification"
    head -40 "$tmp/.$f.diff"
    fail=1
  fi
done

if diff -ru "$here/snapshot" "$tmp/snapshot" > "$tmp/.snapshot.diff"; then
  echo "OK       snapshot/ ($(find "$here/snapshot" -type f | wc -l | tr -d ' ') files match the tip blobs)"
else
  echo "MISMATCH snapshot/ — a shipped file copy differs from git show $TIP:<path>"
  head -40 "$tmp/.snapshot.diff"
  fail=1
fi

# The published state this card claims: the branch head is the tip, and the tip is an ancestor of main.
if [ "$(git --git-dir="$ENGINE" rev-parse "refs/heads/$BRANCH")" = "$(git --git-dir="$ENGINE" rev-parse "$TIP")" ]; then
  echo "OK       $BRANCH is still at $TIP"
else
  echo "NOTE     $BRANCH has moved past $TIP (now $(git --git-dir="$ENGINE" rev-parse "refs/heads/$BRANCH"))"
fi
if git --git-dir="$ENGINE" merge-base --is-ancestor "$TIP" refs/heads/main; then
  echo "OK       $TIP is contained in refs/heads/main"
else
  echo "MISMATCH $TIP is NOT an ancestor of refs/heads/main"
  fail=1
fi

if ! ( cd "$here" && sha256sum -c MANIFEST.sha256 ); then
  echo "MISMATCH MANIFEST.sha256 — a committed file is not what was published"
  fail=1
fi

rm -rf "$tmp"
[ "$fail" -eq 0 ] && echo "bundle verified" || echo "BUNDLE NOT VERIFIED"
exit "$fail"
