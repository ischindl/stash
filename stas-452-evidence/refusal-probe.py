#!/usr/bin/env python3
"""STAS-452: what is the live `tip-already-merged cleanup failed` loop actually refusing on?

The engine records that warning in each card's own task-log rows
(`.fusion/tasks/<ID>/task.json`, key `action`), and the sentence it embeds is the removal door's own
refusal, which names the checkout. This script pulls every such line, extracts the checkout, and asks
that checkout the two questions the two gates ask - occupancy, and the base basename allowlist - so the
account says which door owns each historical refusal instead of assuming it was the allowlist.

Read-only. Nothing here deletes, prunes, or resets anything.
"""
import collections
import json
import pathlib
import re
import subprocess

TASKS = pathlib.Path.home() / "git" / "stash" / ".fusion" / "tasks"
# Transcribed from base 81c576a865, worktree-backend.ts:1285-1289.
ALLOW = {"node_modules", "dist", "build", "out", "target", "bin", "obj", "coverage",
         ".next", ".nuxt", ".turbo", ".svelte-kit", ".parcel-cache", ".pytest_cache",
         ".mypy_cache", "__pycache__", ".gradle", ".venv", "venv"}
REFUSAL = re.compile(r"preserving (/home/\S+?): (uncommitted or ignored content present|status probe failed)")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def verdict(path):
    p = pathlib.Path(path)
    if not p.is_dir():
        return "path already gone"
    r = git(str(p), "status", "--porcelain=v1", "--ignored=matching", "--untracked-files=normal")
    if r.returncode != 0:
        return "status probe fails now"
    entries = [line for line in r.stdout.splitlines() if line.strip()]
    if not entries:
        return "clean now, nothing left to refuse"
    occupiers = [e for e in entries if not e.startswith("!! ")]
    if occupiers:
        return f"OCCUPANCY ({len(occupiers)} entries) - both gates refuse"
    unnamed = [e for e in entries if e[3:].rstrip("/").split("/")[-1] not in ALLOW]
    if unnamed:
        return f"IGNORED-NAME gate only: {unnamed[0][:30]}"
    return "allowlisted ignored only - base released it"


def main():
    counts, newest, cards = collections.Counter(), {}, collections.defaultdict(set)
    lines_total = 0
    for row in sorted(TASKS.iterdir()):
        f = row / "task.json"
        if not f.is_file():
            continue
        try:
            task = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for entry in (task.get("log") or []):
            text = " ".join(str(entry.get(key) or "") for key in ("message", "action", "summary"))
            if "tip-already-merged cleanup failed" not in text:
                continue
            lines_total += 1
            match = REFUSAL.search(text)
            if not match:
                continue
            counts[match.group(1)] += 1
            stamp = str(entry.get("timestamp") or entry.get("at") or entry.get("ts") or "")
            newest[match.group(1)] = max(newest.get(match.group(1), ""), stamp)
            cards[match.group(1)].add(row.name)

    print(f"warning lines scanned: {lines_total}")
    print(f"lines naming a checkout: {sum(counts.values())}   distinct checkouts: {len(counts)}")
    print(f"cards involved: {len({c for s in cards.values() for c in s})}")
    print()
    print(f"refusals   cards  newest                      owns the refusal")
    for path, n in counts.most_common(60):
        lane = pathlib.Path(path).name
        print(f"{n:>9}  {len(cards[path]):>6}  {newest.get(path, '')[:26]:26}  {lane}: {verdict(path)}")

    # Rollup: the same verdicts counted by refusal line, which is the unit the loop is measured in.
    rollup = collections.Counter()
    for path, n in counts.items():
        verdict_text = verdict(path)
        head = verdict_text.split(" ")[0] if not verdict_text.startswith("IGNORED-NAME") else "IGNORED-NAME"
        rollup[head] += n
    print()
    print("=== rollup by refusal line (the unit the loop is measured in) ===")
    for head, n in rollup.most_common():
        print(f"{n:>6} lines  {head}")
    print(f"{sum(rollup.values()):>6} of {lines_total} scanned lines carried a checkout path")
    print()
    print("=== verdicts still live on disk (a path exists right now) ===")
    for path, n in counts.most_common():
        verdict_text = verdict(path)
        if verdict_text == "path already gone":
            continue
        print(f"{n:>6}  {pathlib.Path(path).name:16} {verdict_text}")


if __name__ == "__main__":
    main()
