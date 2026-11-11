"""Step 0 / AC1: read-only per-card git reads on the wedged card worktrees.

Every path is derived from `git rev-parse --git-common-dir` at run time. Only read-only
git commands are executed here: rev-parse, status, rev-list, merge-base, worktree list.
"""
import json
import os
import subprocess
import sys

WARN = "Auto-recovery warning: tip-already-merged cleanup failed"
CARDS = ["STAS-357", "STAS-363", "STAS-374", "STAS-376", "STAS-384", "STAS-385", "STAS-393", "STAS-394"]


def repo_root() -> str:
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return os.path.dirname(os.path.abspath(common))


def git(cwd, *args):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def main() -> int:
    root = repo_root()
    worktrees = os.path.join(root, ".fusion", "worktrees")
    tasks = os.path.join(root, ".fusion", "tasks")

    rc, out, _ = git(root, "rev-parse", "refs/heads/main")
    print(f"integration_ref=refs/heads/main trunk_sha={out.strip()} rc={rc}")

    for card in CARDS:
        lane = os.path.join(worktrees, card.lower())
        print(f"\n=== {card} lane={os.path.basename(lane)} exists={os.path.isdir(lane)} ===")
        if not os.path.isdir(lane):
            print("lane_absent")
            continue
        # gitlink proof: .git must be a FILE, never a directory, for a linked worktree
        dotgit = os.path.join(lane, ".git")
        kind = "FILE" if os.path.isfile(dotgit) else ("DIR" if os.path.isdir(dotgit) else "ABSENT")
        print(f"dotgit_kind={kind}")

        rc, branch, err = git(lane, "rev-parse", "--abbrev-ref", "HEAD")
        print(f"branch={branch.strip()} rc={rc} {err.strip()}")
        rc, tip, err = git(lane, "rev-parse", "--short", "HEAD")
        print(f"tip={tip.strip()} rc={rc} {err.strip()}")

        rc, out, _ = git(lane, "status", "--porcelain", "--untracked-files=all")
        lines = [l for l in out.splitlines() if l.strip()]
        print(f"porcelain_untracked_all_lines={len(lines)} rc={rc}")
        for l in lines[:5]:
            print(f"  U> {l}")

        rc, out, _ = git(lane, "status", "--porcelain", "--untracked-files=normal")
        print(f"porcelain_untracked_normal_lines={len([l for l in out.splitlines() if l.strip()])} rc={rc}")

        rc, out, _ = git(lane, "status", "--porcelain=v1", "--ignored=matching", "--untracked-files=normal")
        all_lines = [l for l in out.splitlines() if l.strip()]
        ignored = [l for l in all_lines if l.startswith("!!")]
        print(f"porcelain_ignored_matching_lines={len(all_lines)} ignored_lines={len(ignored)} rc={rc}")
        for l in ignored[:6]:
            print(f"  I> {l}")

        rc, out, _ = git(lane, "rev-list", "--left-right", "--count", "refs/heads/main...HEAD")
        print(f"left_right_main_dot_HEAD={out.strip()} rc={rc}")

        rc, out, err = git(lane, "merge-base", "--is-ancestor", "HEAD", "refs/heads/main")
        print(f"is_ancestor_HEAD_of_main_rc={rc} (0 means HEAD is an ancestor of trunk)")

        rc, out, _ = git(lane, "rev-list", "--count", f"refs/heads/main..HEAD")
        print(f"ahead_of_main={out.strip()} rc={rc}")

        task_path = os.path.join(tasks, card, "task.json")
        with open(task_path) as fh:
            task = json.load(fh)
        log = task.get("log") or []
        warn = sum(1 for row in log if WARN in (row.get("action") or ""))
        latest = max((row.get("timestamp") or "" for row in log if WARN in (row.get("action") or "")), default="")
        print(f"column={task.get('column')} status={task.get('status')} warn_rows={warn} latest={latest}")
        print(f"awaitingApprovalReason={task.get('awaitingApprovalReason')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
