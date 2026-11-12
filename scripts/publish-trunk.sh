#!/usr/bin/env bash
set -euo pipefail

# -------------------------------------------------------
# publish-trunk.sh — mirror our trunk refs to the fork, then prove the mirror took.
#
# Run it after every merge to `main`. Publishing is a standing action, not a release
# ritual: the work multiplies on `refs/heads/main`, and the mirror is what keeps the
# fork from becoming a line of its own.
#
# The fork is a mirror and a pull-request source, never a branch of its own, so this
# script only ever fast-forwards. Upstream (`origin`) is never pushed to: `ischindl`
# holds pull-only rights there, a pull request is the only route, and opening one is
# the operator's call (see the operator-gated PR clause in CLAUDE.md).
#
# Every push names the remote AND the refspec. Never a bare `git push`, never
# `--all`, never `--tags`: `.git/config` maps `branch.main.remote` to `origin`, so a
# bare push on `main` aims at upstream, and tags are the one ref that must never
# leave this machine — `.github/workflows/publish.yml` publishes to PyPI on a `v*`
# tag and `docker-publish.yml` builds the release images from it.
#
# A divergence stops the script rather than being resolved by it: if the fork holds
# work we do not have, mirroring it away is an operator's decision, so this file
# contains no force of any kind.
# -------------------------------------------------------

REMOTE=fork

mirror_ref() {
  local ref="$1"
  local ours theirs

  ours=$(git rev-parse "$ref")
  theirs=$(git ls-remote "$REMOTE" "$ref" | awk -v want="$ref" '$2 == want { print $1 }')

  if [[ -z "$theirs" ]]; then
    echo "$ref: $REMOTE has no such branch; creating it at $ours"
  elif [[ "$theirs" == "$ours" ]]; then
    echo "$ref: already mirrored at $ours"
    return 0
  elif git merge-base --is-ancestor "$theirs" "$ours"; then
    echo "$ref: $REMOTE is behind at $theirs, ours is $ours; fast-forwarding"
  else
    echo "$ref: $REMOTE holds commits this machine does not have."
    echo "A non-fast-forward would drop exactly these:"
    git log --oneline "$theirs" --not "$ref"
    echo "OPERATOR ACTION REQUIRED: $REMOTE's $ref is a line of its own, not a mirror."
    echo "Nothing was pushed. Decide what happens to that work, then run this again."
    return 1
  fi

  git push "$REMOTE" "$ours:$ref"

  # The re-read is the product of this script. A push nobody read back is a failed run.
  theirs=$(git ls-remote "$REMOTE" "$ref" | awk -v want="$ref" '$2 == want { print $1 }')
  if [[ "$theirs" != "$ours" ]]; then
    echo "$ref: PUSH UNVERIFIED: $REMOTE reads ${theirs:-absent}, expected $ours"
    return 1
  fi
  echo "$ref: mirrored, $REMOTE reads $theirs"
}

status=0
for ref in refs/heads/main refs/heads/deploy/main; do
  mirror_ref "$ref" || status=1
done

echo "measured: main local=$(git rev-parse refs/heads/main)" \
  "fork=$(git ls-remote "$REMOTE" refs/heads/main | awk '$2 == "refs/heads/main" { print $1 }')" \
  "deploy/main local=$(git rev-parse refs/heads/deploy/main)" \
  "fork=$(git ls-remote "$REMOTE" refs/heads/deploy/main | awk '$2 == "refs/heads/deploy/main" { print $1 }')" \
  "status=$status"

exit "$status"
