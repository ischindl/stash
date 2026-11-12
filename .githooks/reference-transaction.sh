#!/usr/bin/env bash
# The trunk may only advance to a commit that is built on upstream's trunk.
#
# Installed by `backend/scripts/install_hooks.py`, which links this file into the repository's
# shared hook directory. Every checkout on this machine runs the same hook, and that is the
# point: `refs/heads/main` is one ref shared by all of them, so a gate some checkouts obey is
# a convention again.
#
# What it refuses. A trunk that would move to a commit not carrying `origin/main` — the
# engine's squash-merge advance, the absorb job's publish, a hand-run `git merge`, a
# `git update-ref`, a `git push .` into this repository. A lane under `refs/heads/fusion/`
# *created* on a base that is behind the trunk this repository already has — the engine's
# `git worktree add`, which is where a lane's base is chosen. A deleted trunk, which would
# strand every lane based on it.
#
# What it reports instead of refusing, on purpose: a new lane whose base is behind upstream
# *while the trunk is behind upstream too*. Nobody can cut an anchored lane in that state, and
# the lane whose whole job is to clear the upstream debt is cut from exactly that base, so a
# refusal there would lock the repair out of the machine that needs it. reference-transaction
# hooks have no `--no-verify`, so a refusal there is not a deterrent but a wall. The hard gate
# is therefore the trunk ref alone: while `main` cannot advance, no lane can land on it, and
# the unanchored base is named on stderr where the engine's own log will show it.
#
# What it does not refuse at all: writes to an existing lane (commits, rebases, resets). While
# the trunk is unabsorbed every lane is unanchored, so refusing those would freeze the lanes
# already in flight instead of refusing a new mistake. The mistake is visible where the base is
# chosen and where it reaches the trunk, and those are the two writes this gate covers.
#
# It never fetches. A hook that touches the network can hang a merge with no way to cancel it,
# so it judges `refs/remotes/origin/main` exactly as this repository already holds it. `absorb`
# and `check` are the commands that fetch, and both run before a trunk write is reached.
set -uo pipefail

TRUNK=refs/heads/main
GATE=backend/scripts/upstream_gate.py
ZERO=0000000000000000000000000000000000000000

# git calls this hook up to three times per transaction — `preparing` before anything is
# written, `prepared` once the transaction is staged, `committed` afterwards. Refusing at
# any of them fails the command, and refusing at `preparing` is the one that leaves no
# trace; `aborted` means git already gave up, so there is nothing left to judge.
phase=${1:-}
case $phase in
  preparing | prepared | committed) ;;
  *) exit 0 ;;
esac

# `git worktree add` runs this hook with the new worktree as the working directory, so the
# repository root has to come from git rather than from wherever the caller stood.
root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "upstream-gate: refused a ref write; this hook cannot find a repository root, and an unknown base is not a pass." >&2
  exit 1
}

refuse() { # $1 = ref being written, $2 = the commit it would name, $3 = the gate's own words
  local short
  short=$(git rev-parse --short "$2" 2>/dev/null || printf '%s' "$2")
  {
    echo "upstream-gate: refused to write $1 at $short."
    printf '%s\n' "$3"
    echo "Fix: python -m backend.scripts.upstream_gate absorb"
    echo "Conflicts are resolved in a lane of their own with a reviewer; this gate never picks a side."
  } >&2
  exit 1
}

while read -r old new ref; do
  case $ref in
    "$TRUNK") kind=trunk ;;
    refs/heads/fusion/*) kind=lane ;;
    *) continue ;; # HEAD, AUTO_MERGE, refs/stash and every other ref this gate knows nothing about
  esac

  if [ "$new" = "$ZERO" ]; then
    if [ "$kind" = trunk ]; then
      echo "upstream-gate: refused to delete $TRUNK. Every lane on this machine is based on it." >&2
      exit 1
    fi
    continue
  fi

  # A lane is judged where it is born: `old` is all zeros only for a creation, so a commit or
  # a rebase on a lane that already exists is left alone.
  if [ "$kind" = lane ] && [ "$old" != "$ZERO" ]; then
    continue
  fi

  if verdict=$(python3 "$root/$GATE" check --no-fetch "$new" 2>&1); then
    continue
  fi

  if [ "$kind" = trunk ]; then
    refuse "$ref" "$new" "$verdict"
  fi

  # The lane's base is behind upstream. Is that this lane's mistake, or the trunk's? Asked the
  # only way it can be answered here: does the trunk this repository already holds carry upstream?
  # If it does, this base was chosen below the trunk, and that is the fork-in-the-making this gate
  # exists for. If it does not, nobody can cut an anchored lane right now, so the lane is created
  # and the verdict is reported instead.
  if trunk=$(python3 "$root/$GATE" check --no-fetch "$TRUNK" 2>&1); then
    refuse "$ref" "$new" "$verdict
$TRUNK carries upstream, so this base was chosen below it."
  fi

  # Reported once, at `committed`, because git runs this hook three times per transaction and a
  # warning repeated three times per lane is the chatter that gets a gate uninstalled. It is a
  # warning and not a refusal for the reason stated at the top of this file: the trunk is in the
  # same state, so this base is the debt, not a mistake, and the lane that clears the debt has to
  # be cut from it.
  if [ "$phase" = committed ]; then
    {
      echo "upstream-gate: warning, creating $ref at $(git rev-parse --short "$new" 2>/dev/null || printf '%s' "$new") although its base is not built on origin/main."
      printf '%s\n' "$verdict"
      echo "$TRUNK is in the same state, so no lane can be anchored before the trunk is:"
      printf '%s\n' "$trunk"
      echo "This lane cannot reach $TRUNK until upstream is absorbed: python -m backend.scripts.upstream_gate absorb"
    } >&2
  fi
done

exit 0
