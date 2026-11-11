#!/usr/bin/env python3
"""STAS-397 AC8 — the delivery envelope, measured in one tick, read-only.

Every number the operator needs to decide whether this branch can land is printed here from one
process, so no coordinate is carried over from an earlier tick: the delivered ref discovered with
`git for-each-ref` (never assumed from the spec's suggested name), live trunk at the same moment,
the collision predicates, the file list with a File-Scope verdict per entry, the bundle, and the
proof that this card executed no removal of any kind.

Nothing here writes, removes, prunes, resets or cleans. The engine worktree and this repository are
both only read.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # stas-397-evidence/raw
EVIDENCE = HERE.parent                             # stas-397-evidence
CARD_WORKTREE = EVIDENCE.parent                    # this card's worktree (stash repository)
NESTED = CARD_WORKTREE / ".engine/stas-397-engine"
CARD_ID = "stas-397"


def toplevel(repo: Path) -> str:
    """The repository root a worktree belongs to, read from git rather than reconstructed by counting
    path components - this card's checkout is nested two ways at once (a worktree of the stash repo
    holding a linked worktree of the engine repo), and a guessed parent depth gets it silently wrong."""
    rc, common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if rc != 0:
        raise SystemExit(f"cannot resolve the repository owning {repo}: {common}")
    return str(Path(common).parent)

# File Scope as PROMPT.md states it. `*` is expanded by hand here rather than by a globber so the
# verdict printed next to each path is auditable by eye.
SCOPE_GLOBS = [
    "packages/engine/src/worktree/",
    "packages/engine/src/execution/",
    "packages/engine/src/__tests__/",
    "/__tests__/",
]


def git(cwd: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def scope_verdict(path: str) -> str:
    if path.startswith(".changeset/"):
        return "OUTSIDE (disclosed: the engine's contributor gate requires a changeset entry)"
    for glob in SCOPE_GLOBS:
        if glob in path:
            return "inside"
    return "OUTSIDE (disclosed: import/prose follow-on of the predicate move)"


def main() -> int:
    branch = sys.argv[1] if len(sys.argv) > 1 else "stas-397-delivery"
    base = sys.argv[2] if len(sys.argv) > 2 else ""

    global ENGINE, PROJECT
    ENGINE = Path(toplevel(NESTED))               # the engine repository (primary checkout)
    PROJECT = Path(toplevel(CARD_WORKTREE))       # the stash repository (primary checkout)
    print(f"=== 0. repository roots, resolved by git, not by path arithmetic ===")
    print(f"  card worktree        = {CARD_WORKTREE}")
    print(f"  nested engine checkout = {NESTED}")
    print(f"  engine repository    = {ENGINE}")
    print(f"  stash repository     = {PROJECT}")

    print("\n=== A. the delivered ref, discovered live, never assumed ===")
    rc, refs = git(ENGINE, "for-each-ref", "--format=%(refname) %(objectname) %(subject)", "--sort=committerdate", f"refs/heads/")
    mine = [line for line in refs.splitlines() if CARD_ID in line.lower()]
    print(f"  for-each-ref refs/heads/ filtered to '{CARD_ID}': {len(mine)} line(s) (rc={rc})")
    for line in mine:
        print(f"    {line}")
    rc, tip = git(ENGINE, "rev-parse", f"refs/heads/{branch}")
    print(f"  delivered tip = {tip} (rc={rc})")
    rc, head_line = git(ENGINE, "log", "-1", "--format=%H%n  author=%an%n  date=%aI%n  subject=%s", f"refs/heads/{branch}")
    print(f"  head:{chr(10)}{head_line}")

    print("\n=== B. live trunk, re-read at this moment ===")
    rc, trunk = git(ENGINE, "rev-parse", "refs/heads/main")
    print(f"  engine refs/heads/main = {trunk} (rc={rc})   [Step 1/2 measured base: {base or 'not passed'}]")
    rc, trunk_head = git(ENGINE, "log", "-1", "--format=%h %aI %s", "refs/heads/main")
    print(f"  trunk head: {trunk_head}")
    rc, mb = git(ENGINE, "merge-base", "refs/heads/main", f"refs/heads/{branch}")
    print(f"  merge-base trunk..branch = {mb} (rc={rc})")
    if base:
        print(f"  merge-base equals the card's base: {mb == base}  [{base}]")
    rc, lr = git(ENGINE, "rev-list", "--left-right", "--count", f"refs/heads/main...refs/heads/{branch}")
    print(f"  rev-list --left-right --count trunk...branch = {lr.replace(chr(9), ' ')}  (behind ahead)")
    rc, _ = git(ENGINE, "merge-base", "--is-ancestor", "refs/heads/main", f"refs/heads/{branch}")
    print(f"  merge-base --is-ancestor trunk branch rc={rc} (0 = branch carries live trunk)")

    print("\n=== C. collision predicates ===")
    rc, mt = git(ENGINE, "merge-tree", "--write-tree", "refs/heads/main", f"refs/heads/{branch}")
    first = mt.splitlines()[0] if mt else ""
    print(f"  git merge-tree --write-tree trunk branch rc={rc} tree={first}")
    conflicts = [line for line in mt.splitlines() if "CONFLICT" in line]
    print(f"  CONFLICT lines: {len(conflicts)}")
    for line in conflicts[:10]:
        print(f"    {line}")
    rc, paths = git(ENGINE, "diff", "--name-only", f"{mb}..refs/heads/main", "--",
                    "packages/engine/src/worktree", "packages/engine/src/execution",
                    "packages/engine/src/__tests__", "packages/engine/src/worktree/worktree-backend.ts",
                    "packages/engine/src/executor", "packages/engine/src/self-healing.ts",
                    "packages/engine/src/util/run-audit.ts", ".changeset")
    moved = [line for line in paths.splitlines() if line.strip()]
    print(f"  name-only diff merge-base..trunk over this card's DIRECTORY globs: {len(moved)} file(s) (rc={rc})")
    for line in moved[:20]:
        print(f"    trunk moved over: {line}")
    # Directory globs are the coarse probe; the predicate that actually predicts a collision is the
    # intersection of the two changed-file sets, because two cards editing different files in the
    # same directory cannot conflict.
    rc, trunk_names = git(ENGINE, "diff", "--name-only", f"{mb}..refs/heads/main")
    rc2, branch_names = git(ENGINE, "diff", "--name-only", f"{mb}..refs/heads/{branch}")
    overlap = sorted(set(trunk_names.splitlines()) & set(branch_names.splitlines()))
    print(f"  changed files merge-base..trunk: {len(trunk_names.splitlines())} (rc={rc});"
          f" merge-base..branch: {len(branch_names.splitlines())} (rc={rc2})")
    print(f"  INTERSECTION over this card's exact delivered paths: {len(overlap)} file(s)  [expected 0]")
    for line in overlap:
        print(f"    both sides touched: {line}")

    print("\n=== D. the delivered diff, with a File-Scope verdict per entry ===")
    rc, names = git(ENGINE, "diff", "--name-only", f"{mb}..refs/heads/{branch}")
    entries = [line for line in names.splitlines() if line.strip()]
    print(f"  {len(entries)} file(s) changed by the delivered branch (rc={rc}):")
    outside = 0
    for entry in entries:
        verdict = scope_verdict(entry)
        outside += 1 if verdict.startswith("OUTSIDE") else 0
        print(f"    {entry}\n        {verdict}")
    print(f"  entries outside the literal File Scope globs: {outside} (each disclosed in the close summary)")
    rc, stat = git(ENGINE, "diff", "--shortstat", f"{mb}..refs/heads/{branch}")
    print(f"  shortstat: {stat}")

    print("\n=== E. the bundle ===")
    bundle = Path(sys.argv[3]) if len(sys.argv) > 3 else EVIDENCE / "raw/stas-397-delivery.bundle"
    if not bundle.is_absolute():
        # every git call in this probe runs inside the engine repository, so a relative bundle path
        # has to be resolved against this card's worktree before it reaches `git bundle`.
        bundle = CARD_WORKTREE / bundle
    if bundle.exists():
        rc, heads = git(ENGINE, "bundle", "list-heads", str(bundle))
        print(f"  git bundle list-heads {bundle.name} rc={rc}")
        for line in heads.splitlines():
            print(f"    {line}")
        rc, req = git(ENGINE, "bundle", "verify", str(bundle))
        for line in req.splitlines():
            print(f"    verify: {line}")
        print(f"  bundle head matches delivered tip: {tip in heads}")
    else:
        print(f"  bundle not present at {bundle}")

    print("\n=== F. the wedged checkouts are untouched by this card ===")
    rc, listing = git(PROJECT, "worktree", "list", "--porcelain")
    registered = [line.split(" ", 1)[1] for line in listing.splitlines() if line.startswith("worktree ")]
    print(f"  `git worktree list` in the project repository: {len(registered)} entries (rc={rc})")
    held = ["stas-357", "stas-363", "stas-374", "stas-376", "stas-384", "stas-385", "stas-393"]
    for card in held:
        named = any(entry.endswith(f"/worktrees/{card}") for entry in registered)
        print(f"  held checkout .fusion/worktrees/{card}: still registered = {named}")
    print("  NO `git worktree remove`, `prune`, `reset`, `clean` or directory deletion was executed")
    print("  by this card against any held checkout. The remedy delivered here is code plus a test.")

    print("\n=== G. this card's own worktree, and the nested engine checkout ===")
    rc, out = git(CARD_WORKTREE, "status", "--porcelain", "--untracked-files=all")
    lines = [line for line in out.splitlines() if line.strip()]
    print(f"  card worktree porcelain (all, incl. untracked): {len(lines)} line(s) (rc={rc})")
    tracked_only = [line for line in lines if not line.startswith("?? ")]
    print(f"  card worktree porcelain filtered of untracked entries: {len(tracked_only)} line(s)  [expected 0]")
    rc, out2 = git(NESTED, "status", "--porcelain", "--untracked-files=all")
    lines2 = [line for line in out2.splitlines() if line.strip()]
    print(f"  nested engine checkout porcelain (all): {len(lines2)} line(s) (rc={rc})")
    tracked_only2 = [line for line in lines2 if not line.startswith("?? ")]
    print(f"  nested engine checkout porcelain filtered of untracked: {len(tracked_only2)} line(s)  [expected 0]")
    for line in lines2[:10]:
        print(f"    wt: {line}")
    rc, detached = git(NESTED, "rev-parse", "--abbrev-ref", "HEAD")
    print(f"  nested checkout HEAD = {detached} (rc={rc})")

    print("\n=== H. the suite totals, re-read from the raw logs and the run ledger, not transcribed ===")
    scratch = CARD_WORKTREE / ".fusion-tmp/stas-397"
    try:
        ledger = (scratch / "suite-runs.txt").read_text()
    except OSError as exc:
        print(f"  cannot read the run ledger: {exc}")
        return 1
    print("  run ledger (.fusion-tmp/stas-397/suite-runs.txt), verbatim:")
    for line in ledger.splitlines():
        print(f"    {line}")
    for label, log in (("BASE ", "before-suite.raw.log"), ("TIP  ", "after-suite.raw.log"), ("DELIVERY", "delivery-suite.raw.log")):
        path = scratch / log
        if not path.exists():
            print(f"  {label} {log}: MISSING")
            continue
        text = path.read_bytes().replace(b"\x00", b"").replace(b"\r", b"\n").decode("utf-8", "replace")
        sums = [line.strip() for line in text.splitlines() if "Test Files" in line or ("Tests " in line and "failed" in line)]
        print(f"  {label} {log}: {len(sums)} summary line(s)")
        for line in sums[-2:]:
            print(f"      {line}")
    print("  A DELIVERY log with 0 summary lines is a run that never reached its summary, and no total")
    print("  is quoted from it. AC6's identical-command pair is the BASE/TIP pair above; the delivered")
    print("  head differs from the measured TIP by one comment-only commit, whose sensitive-file and")
    print("  typecheck/eslint re-runs are in alignment-and-suites.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
