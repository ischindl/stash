#!/bin/sh
# Re-derives every git-derived claim in this directory from the engine repo and exits non-zero on any
# mismatch. Records (measurement.txt, verification.txt) are checked against MANIFEST.sha256 but cannot
# be re-derived here — they are observations of the board DB and of test runs, not of git.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

OUT_DIR="$TMP" ./generate.sh > "$TMP/generate.out" 2>&1 || { echo "generate.sh failed:"; cat "$TMP/generate.out"; exit 1; }

fail=0
for f in provenance.txt commits.txt diffstat.txt stas-330-series.diff remediation.diff; do
  if ! diff -q "$f" "$TMP/$f" > "$TMP/$f.diff" 2>&1; then
    echo "MISMATCH $f"
    head -20 "$TMP/$f.diff"
    fail=1
  fi
done

# MANIFEST.sha256 must still describe the files it claims to.
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c MANIFEST.sha256 > "$TMP/manifest.out" 2>&1 || { echo "MANIFEST.sha256 mismatch"; tail -5 "$TMP/manifest.out"; fail=1; }
else
  echo "sha256sum unavailable; manifest check skipped"
fi

# The tip quoted in provenance.txt should still be the branch head. A moved branch is reported and
# never silently tolerated, because the evidence files are pinned to the reviewed head.
recorded=$(sed -n 's/^tip (reviewed head)  //p' provenance.txt)
branch=$(sed -n 's/^branch               //p' provenance.txt)
live=$(git --git-dir="${ENGINE_GITDIR:-/home/schindler/git/Fusion/.git}" rev-parse --verify "$branch" 2>/dev/null || echo unresolvable)
if [ "$recorded" = "$live" ]; then
  echo "branch head matches the reviewed tip ($recorded)"
else
  echo "NOTE branch $branch is now $live; this evidence is pinned to the reviewed tip $recorded"
fi

if [ "$fail" -eq 0 ]; then
  echo "verify: git-derived artifacts reproduce byte-for-byte; manifest intact"
fi
exit "$fail"
