#!/usr/bin/env python3
"""STAS-397 AC7/AC8 delivery-window probe — read-only.

Everything the operator needs to check the landing sits in one print, measured in the same tick the
handoff is written: the live trunk SHA the branch was cut against, what the wedged cards look like
RIGHT NOW (before any landing), the two predicates that would prove the queue drained, and the
build/restart facts that decide whether a restart alone would do anything at all.

Nothing here writes. No worktree is listed for removal, none is removed, and the card logs are read
as data, never edited.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Relative from this card's worktree, so the probe never hard-codes a machine path.
PROJECT = "../../.."                 # the checkout that owns the wedged card worktrees
ENGINE = "../../../../Fusion"        # the engine repository the delivery branch lands in
CARDS = ["STAS-357", "STAS-363", "STAS-374", "STAS-376", "STAS-384", "STAS-385", "STAS-393", "STAS-394"]
WARNING = "Auto-recovery warning: tip-already-merged cleanup failed"
DELIVERY_BRANCH = sys.argv[1] if len(sys.argv) > 1 else "stas-397-delivery"


def git(cwd: str, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def main() -> None:
    print(f"probe tick (UTC): {utc_now().isoformat(timespec='seconds')}")

    print("\n=== engine repository: trunk and this card's branch ===")
    rc, trunk = git(ENGINE, "rev-parse", "refs/heads/main")
    print(f"  refs/heads/main = {trunk} (rc={rc})")
    rc, subject = git(ENGINE, "log", "-1", "--format=%h %ad %s", "--date=iso-strict", "refs/heads/main")
    print(f"  trunk head: {subject}")
    rc, tip = git(ENGINE, "rev-parse", f"refs/heads/{DELIVERY_BRANCH}")
    print(f"  refs/heads/{DELIVERY_BRANCH} = {tip} (rc={rc})")
    rc, off = git(ENGINE, "rev-list", "--left-right", "--count", f"refs/heads/main...refs/heads/{DELIVERY_BRANCH}")
    print(f"  left_right_main_dot_branch = {off}  (behind ahead; 0 N means the branch is trunk plus N commits)")
    rc, _ = git(ENGINE, "merge-base", "--is-ancestor", "refs/heads/main", f"refs/heads/{DELIVERY_BRANCH}")
    print(f"  merge-base --is-ancestor refs/heads/main branch rc={rc} (0 = cut off live trunk)")

    print("\n=== the wedged checkouts, as they stand at this tick ===")
    rc, listing = git(PROJECT, "worktree", "list", "--porcelain")
    registered = {line.split(" ", 1)[1] for line in listing.splitlines() if line.startswith("worktree ")}
    print(f"  `git worktree list` entries in the project repo: {len(registered)}")
    for card in CARDS:
        task_file = Path(f".fusion/tasks/{card}/task.json")
        if not task_file.exists():
            task_file = Path(f"../../../.fusion/tasks/{card}/task.json")
        if not task_file.exists():
            print(f"  {card}: no task record readable")
            continue
        record = json.loads(task_file.read_text(encoding="utf-8"))
        worktree = record.get("worktree") or ""
        column = record.get("column") or record.get("status") or "?"
        log = record.get("log") or []
        # Task log rows carry their text in `action`, not `message`; match the whole row so a rename
        # of that field cannot silently read as "no warnings happened".
        rows = [entry for entry in log if isinstance(entry, dict) and WARNING in json.dumps(entry)]
        newest = max((str(entry.get("timestamp", "")) for entry in rows), default="")
        recent = 0
        if newest:
            try:
                cutoff = utc_now() - timedelta(hours=24)
                stamp = datetime.fromisoformat(newest.replace("Z", "+00:00"))
                recent = sum(1 for entry in rows if datetime.fromisoformat(str(entry.get("timestamp", "")).replace("Z", "+00:00")) >= cutoff)
                del stamp
            except ValueError:
                recent = -1
        named = worktree in registered if worktree else False
        print(f"  {card}: column={column} worktree_named_by_list={named} "
              f"warning_rows_total={len(rows)} warning_rows_24h={recent} newest={newest or '-'}"
              f" recoveryCount={record.get('branchConflictRecoveryCount')}"
              f" planReviewReplanCount={record.get('planReviewReplanCount')}")
        if worktree:
            wt_dir = Path(worktree) if Path(worktree).is_absolute() else Path(f"../../../{worktree}")
            rc, head = git(str(wt_dir), "rev-parse", "--short", "HEAD") if wt_dir.exists() else (2, "")
            rc2, porcelain = git(str(wt_dir), "status", "--porcelain", "--untracked-files=all") if wt_dir.exists() else (2, "")
            rc3, ignored = git(str(wt_dir), "status", "--porcelain=v1", "--ignored=matching", "--untracked-files=normal") if wt_dir.exists() else (2, "")
            ignored_lines = [line for line in ignored.splitlines() if line.startswith("!! ")]
            print(f"        HEAD={head or '?'} untracked_expanded_lines={len([p for p in porcelain.splitlines() if p])} "
                  f"ignored_lines={len(ignored_lines)} {ignored_lines[:3]}")

    print("\n=== capacity the wedge is measured against ===")
    for candidate in ("../../../.fusion/config.json", ".fusion/config.json"):
        path = Path(candidate)
        if path.exists():
            config = json.loads(path.read_text(encoding="utf-8"))
            flat = json.dumps(config)
            found = {key: value for key, value in (config.get("autoRecovery") or {}).items()} if isinstance(config.get("autoRecovery"), dict) else {}
            print(f"  {candidate}: maxWorktrees={_dig(config, 'maxWorktrees')} "
                  f"globalMaxConcurrent={_dig(config, 'globalMaxConcurrent')} maxConcurrent={_dig(config, 'maxConcurrent')}")
            del flat, found
            break
    else:
        print("  no readable .fusion/config.json")

    print("\n=== build state: does a restart alone change anything? ===")
    dist = Path(ENGINE) / "packages/engine/dist/worktree/worktree-backend.js"
    src = Path(ENGINE) / "packages/engine/src/worktree/worktree-backend.ts"
    for label, path in (("dist", dist), ("src", src)):
        if path.exists():
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            print(f"  live engine {label} mtime: {stamp}  ({path.name})")
        else:
            print(f"  live engine {label}: not present")
    rc, newest_touch = git(ENGINE, "log", "-1", "--format=%h %ad %s", "--date=iso-strict", "refs/heads/main", "--", "packages/engine/src/worktree/worktree-backend.ts")
    print(f"  newest trunk commit touching that file: {newest_touch}")
    print("  (a daemon restart re-loads whatever `dist` already holds; only a build changes it)")
    if dist.exists():
        built = dist.read_text(encoding="utf-8", errors="replace")
        for marker in ("REGENERABLE_IGNORED_DIR_NAMES", "worktree:removal-discarded-ignored-content", "uncommitted or ignored content present"):
            print(f"  live dist {'CONTAINS' if marker in built else 'lacks':9s} {marker!r}")
        print("  (the first and third are the pre-STAS-397 predicate; the second is this card's record. "
              "If the running build contains the old markers and lacks the new one, the fix cannot take "
              "effect before a build, whichever way the branch lands.)")


def _dig(payload: object, key: str) -> object:
    """Find `key` anywhere in a nested config, so a rename cannot silently read as absent."""
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _dig(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _dig(value, key)
            if found is not None:
                return found
    return None


if __name__ == "__main__":
    sys.exit(main())
