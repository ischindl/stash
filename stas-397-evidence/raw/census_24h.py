"""Step 0: warning census restricted to the last 24 hours, plus capacity and trunk reads."""
import datetime as dt
import glob
import json
import os
import subprocess
import sys

WARN = "Auto-recovery warning: tip-already-merged cleanup failed"
CARDS = ["STAS-357", "STAS-363", "STAS-374", "STAS-376", "STAS-384", "STAS-385", "STAS-393", "STAS-394"]


def repo_root() -> str:
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    return os.path.dirname(os.path.abspath(common))


def main() -> int:
    root = repo_root()
    tasks = os.path.join(root, ".fusion", "tasks")
    now = dt.datetime.now(dt.timezone.utc)
    print(f"measured_at_utc={now.isoformat()}")
    print(f"trunk_sha_at_step0={subprocess.run(['git','rev-parse','refs/heads/main'],cwd=root,capture_output=True,text=True).stdout.strip()}")

    print("\nid | warn_24h | warn_total | refusal_literal_rows_total | column")
    total_24h = 0
    for card in CARDS:
        path = os.path.join(tasks, card, "task.json")
        if not os.path.isfile(path):
            print(f"{card} | record_absent")
            continue
        task = json.load(open(path))
        log = task.get("log") or []
        n24 = 0
        ntot = 0
        for row in log:
            action = row.get("action") or ""
            if WARN not in action:
                continue
            ntot += 1
            ts = row.get("timestamp") or ""
            try:
                when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            if (now - when) <= dt.timedelta(hours=24):
                n24 += 1
        total_24h += n24
        print(f"{card} | {n24} | {ntot} | {ntot} | {task.get('column')}")
    print(f"total_warning_rows_last_24h={total_24h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
