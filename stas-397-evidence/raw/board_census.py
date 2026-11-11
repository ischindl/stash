"""Step 0 board census: read the on-disk Fusion task records and count warning rows.

Paths are derived from `git rev-parse --git-common-dir` rather than written literally so
that this file stays runnable from any task worktree on this host.
"""
import glob
import json
import os
import subprocess
import sys

WARN = "Auto-recovery warning: tip-already-merged cleanup failed"
REFUSAL = "uncommitted or ignored content present"


def repo_root() -> str:
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return os.path.dirname(os.path.abspath(common))


def main() -> int:
    root = repo_root()
    tasks_dir = os.path.join(root, ".fusion", "tasks")
    worktrees_dir = os.path.join(root, ".fusion", "worktrees")
    config_path = os.path.join(root, ".fusion", "config.json")

    print(f"repo_root={root}")
    print(f"tasks_dir_exists={os.path.isdir(tasks_dir)}")

    print("\n=== worktree directory listing (root .fusion/worktrees) ===")
    dirs = sorted(d for d in os.listdir(worktrees_dir) if os.path.isdir(os.path.join(worktrees_dir, d)))
    for d in dirs:
        print(d)
    print(f"worktree_dir_count={len(dirs)}")

    print("\n=== on-disk settings record (worktree capacity) ===")
    with open(config_path) as fh:
        cfg = json.load(fh)
    settings = cfg.get("settings") or {}
    for key in sorted(settings):
        if "orktree" in key or "oncurrent" in key:
            print(f"{key}={settings[key]}")

    print("\n=== per-card census ===")
    rows = []
    for path in sorted(glob.glob(os.path.join(tasks_dir, "*", "task.json"))):
        card_id = os.path.basename(os.path.dirname(path))
        try:
            with open(path) as fh:
                task = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{card_id} UNREADABLE {exc}")
            continue
        log = task.get("log") or []
        warn_n = 0
        refusal_n = 0
        latest = ""
        for row in log:
            action = row.get("action") or ""
            if WARN in action:
                warn_n += 1
                latest = max(latest, row.get("timestamp") or "")
            if REFUSAL in action:
                refusal_n += 1
        # agent log jsonl too, engine writes there
        agent_log = os.path.join(os.path.dirname(path), "agent-log.jsonl")
        agent_refusal_n = 0
        if os.path.isfile(agent_log):
            with open(agent_log, errors="replace") as fh:
                for line in fh:
                    if REFUSAL in line:
                        agent_refusal_n += 1
        rows.append({
            "id": card_id,
            "column": task.get("column"),
            "status": task.get("status"),
            "warn": warn_n,
            "log_refusal": refusal_n,
            "agent_log_refusal": agent_refusal_n,
            "latest_warn": latest,
            "awaiting": task.get("awaitingApprovalReason"),
            "title": (task.get("title") or "")[:70],
        })

    print("id | warn | log_refusal_rows | agent_log_refusal_lines | column | status | awaiting | latest_warn | title")
    for r in sorted(rows, key=lambda x: (-x["warn"], x["id"])):
        if r["warn"] or r["log_refusal"] or r["agent_log_refusal"]:
            print(f'{r["id"]} | {r["warn"]} | {r["log_refusal"]} | {r["agent_log_refusal"]} | '
                  f'{r["column"]} | {r["status"]} | {r["awaiting"]} | {r["latest_warn"]} | {r["title"]}')
    print(f"cards_scanned={len(rows)}")
    print(f"cards_with_warning={sum(1 for r in rows if r['warn'])}")
    print(f"total_warning_rows={sum(r['warn'] for r in rows)}")
    print(f"total_log_refusal_rows={sum(r['log_refusal'] for r in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
