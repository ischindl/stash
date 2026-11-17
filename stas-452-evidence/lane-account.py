#!/usr/bin/env python3
"""STAS-452 AC3 + AC5: the three-class lane account, reconciled to ONE `du` pass.

The classes are the card's own, kept apart rather than blended:

  1 releasable-by-this-fix    new gate releases (no occupancy) AND the base gate refused AND the lane's
                              ref is already contained in trunk - the intake's own two-rule gate.
  2 merged-with-occupancy     ref contained in trunk, but git reports an occupier. Release is refused by
                              BOTH gates, correctly; deleting what it holds is an operator leg.
  3 tip-not-in-trunk          the lane's tip is not contained in trunk. Deletion is forbidden without a
                              patch-id proof, because a squash landing leaves the tip uncontained.
  residual                    everything the three classes do not name: registered worktrees that are not
                              task lanes at all (no `fusion/*` branch, no card row). Printed, never
                              folded into a class, because no automatic sweep addresses them.

Every lane lands in exactly one bucket, and the four bucket sums are reconciled against one
`du -sm -c` invocation, which is the only reading that counts an inode shared between two lanes once.

Both gates are asked of the SAME probe the engine runs:
`git status --porcelain=v1 --ignored=matching --untracked-files=normal`
(worktree-backend.ts:1367). The base allowlist is transcribed from base 81c576a865, lines 1285-1307.

Read-only. This script deletes, prunes, resets and cleans nothing.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "git" / "stash"
LANES = REPO / ".fusion" / "worktrees"
TASK_ROWS = REPO / ".fusion" / "tasks"

BASE_ALLOWLIST = {
    "node_modules", "dist", "build", "out", "target", "bin", "obj", "coverage",
    ".next", ".nuxt", ".turbo", ".svelte-kit", ".parcel-cache", ".pytest_cache",
    ".mypy_cache", "__pycache__", ".gradle", ".venv", "venv",
}
BASE_FUSION_SCRATCH_CHILDREN = {"cache"}


def git(*args, cwd=REPO):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def registered():
    """Map lane path -> branch, from the repository that owns the lanes."""
    out = git("worktree", "list", "--porcelain").stdout
    lanes, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur.get("path"):
                lanes.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            cur["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
    if cur.get("path"):
        lanes.append(cur)
    return {l["path"]: l.get("branch") for l in lanes}


def probe(path):
    r = git("status", "--porcelain=v1", "--ignored=matching", "--untracked-files=normal", cwd=path)
    return None if r.returncode != 0 else [line for line in r.stdout.splitlines() if line.strip()]


def base_refuses(entries, path):
    """Reproduce base 81c576a865 classifyWorktreeRemovalContent(); return the refusing entry or None."""
    if entries is None:
        return "status probe failed (base fails closed)"
    if not entries:
        return None
    if any(not e.startswith("!! ") for e in entries):
        return "occupancy (base refuses this too)"
    scratch = Path(path) / ".fusion"
    proven = set()
    if scratch.is_dir():
        children = sorted(p.name for p in scratch.iterdir())
        if children and all(c in BASE_FUSION_SCRATCH_CHILDREN for c in children):
            proven.add(".fusion")
    for entry in entries:
        raw = entry[3:].rstrip("/")
        if raw.startswith('"'):
            return f"quoted ignored path {raw}"
        if raw in proven and entry == f"!! {raw}/":
            continue
        if raw.split("/")[-1] in BASE_ALLOWLIST:
            continue
        return entry
    return None


def occupiers(entries):
    if entries is None:
        return None
    return [e for e in entries if not e.startswith("!! ")]


def card_row(branch):
    if not branch or not branch.startswith("fusion/"):
        return None
    tid = branch.split("/", 1)[1].upper()
    f = TASK_ROWS / tid / "task.json"
    if not f.is_file():
        return {"id": tid, "column": "<no row>"}
    try:
        row = json.loads(f.read_text())
    except (OSError, ValueError):
        return {"id": tid, "column": "<unreadable>"}
    return {"id": tid, "column": row.get("column")}


def main():
    branches = registered()
    targets = [str(p) for p in sorted(LANES.iterdir()) if p.is_dir()]
    du = subprocess.run(["du", "-sm", "-c", *targets], capture_output=True, text=True)
    sizes, total = {}, 0
    for line in du.stdout.splitlines():
        value, _, name = line.partition("\t")
        if name == "total":
            total = int(value)
        else:
            sizes[name] = int(value)

    buckets = {"1-releasable-by-this-fix": [], "2-merged-with-occupancy": [],
               "3-tip-not-in-trunk": [], "residual-not-a-task-lane": [],
               "residual-released-by-both-gates": []}
    for path in targets:
        branch = branches.get(path)
        entries = probe(path)
        refusers = occupiers(entries)
        base_refuser = base_refuses(entries, path)
        card = card_row(branch)
        contained = None if not branch else git("merge-base", "--is-ancestor", branch, "refs/heads/main").returncode == 0
        row = {
            "lane": Path(path).name, "path": path, "mb": sizes.get(path, 0), "branch": branch,
            "card": (card or {}).get("id"), "column": (card or {}).get("column"),
            "contained": contained, "occupiers": len(refusers or []),
            "probe_failed": entries is None, "base_refuser": base_refuser,
        }
        if card is None:
            buckets["residual-not-a-task-lane"].append(row)
        elif contained is False:
            buckets["3-tip-not-in-trunk"].append(row)
        elif not refusers and base_refuser:
            buckets["1-releasable-by-this-fix"].append(row)
        elif refusers:
            buckets["2-merged-with-occupancy"].append(row)
        else:
            # contained ref, nothing occupying the tree, and the base gate released it too: this lane was
            # already reclaimable before this fix, so it belongs to no class and says so under its own name.
            buckets["residual-released-by-both-gates"].append(row)

    print(f"du -sm -c over {len(targets)} lane directories: TOTAL {total} MB "
          f"(one pass, so an inode shared by two lanes is counted once)")
    print(f"sum of the per-directory lines of that same pass: {sum(sizes.values())} MB "
          f"(delta {sum(sizes.values()) - total} MB = hardlinked content reported in more than one lane)")
    print()
    print(f"{'class':28} {'lanes':>5} {'MB':>7}")
    for name in sorted(buckets):
        rows = buckets[name]
        print(f"{name:28} {len(rows):>5} {sum(r['mb'] for r in rows):>7}")
    print(f"{'RECONCILED':28} {sum(len(r) for r in buckets.values()):>5} "
          f"{sum(r['mb'] for rows in buckets.values() for r in rows):>7}")
    print()

    for name in sorted(buckets):
        rows = buckets[name]
        print(f"=== {name} ===")
        for r in sorted(rows, key=lambda r: -r["mb"]):
            extra = (f"base refuser: {r['base_refuser']}" if name.startswith("1")
                     else f"occupiers={r['occupiers']}" if name.startswith("2")
                     else f"branch={r['branch']}")
            print(f"{r['mb']:>7} MB  {r['lane']:22} {str(r['card']):9} {str(r['column']):12} "
                  f"contained={r['contained']}  {extra}")
        print()

    print("=== the ignored names that the base allowlist could not answer for, per lane ===")
    names = {}
    for r in buckets["1-releasable-by-this-fix"]:
        key = (r["base_refuser"] or "").lstrip("! ")
        names[key] = names.get(key, 0) + 1
    for name, count in sorted(names.items(), key=lambda kv: -kv[1]):
        print(f"{count:>3} lanes  {name}")
    print()
    print("note: `.venv` IS on the base allowlist (worktree-backend.ts:1288). The intake named it as the")
    print("unnamed artifact; measured here, the names that actually refused are the ones listed above.")


if __name__ == "__main__":
    sys.exit(main())
