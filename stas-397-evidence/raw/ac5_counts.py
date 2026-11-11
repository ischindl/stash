#!/usr/bin/env python3
"""STAS-397 AC5/R6 census, run inside the engine checkout.

Counts only what a release decision actually consumes: which `git status` forms exist, which of
them participate in releasing an already-merged checkout, and whether any of them still lets an
IGNORED entry block. Base-vs-tip counts for every symbol this card retires are printed so an
orphan can be seen rather than asserted away.
"""
import subprocess
import sys
from pathlib import Path

ENGINE_DIR = sys.argv[1] if len(sys.argv) > 1 else "."

BASE = "46b94afa0c"
TIP = "stas-397-delivery"
RETIRE = [
    "REGENERABLE_IGNORED_DIR_NAMES",
    "FUSION_SCRATCH_REGENERABLE_CHILDREN",
    "isRegenerableScratchDirectory",
    "isRegenerableIgnoredPorcelainEntry",
    "regenerable-ignored",
    "worktree:removal-discarded-regenerable-content",
    "worktree:post-landing-ignored-content-discarded",
    "worktree:removal-discarded-ignored-content",
    "postLandingProof",
    "POST_LANDING_PROOF_REASONS",
    "InvalidPostLandingProofUsageError",
    "WorktreeContentPreservationError",
    "--ignored=matching",
    "uncommitted or ignored content present",
]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", ENGINE_DIR, *args], capture_output=True, text=True).stdout


def total(pattern: str, *paths: str) -> int:
    args = ["grep", "-c", "-e", pattern]
    args += list(paths) or ["--"]
    out = git(*args)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line and ":" in line)


def main() -> None:
    print(f"engine checkout: {Path(ENGINE_DIR).resolve()}")
    print(f"HEAD: {git('rev-parse', TIP).strip()} (branch {TIP}); measured base {git('rev-parse', BASE).strip()}")

    print("\n### every `git status` read in tracked sources at the tip (excluding tests and worktree-list reads)")
    lines = git("grep", "-n", "-e", "--porcelain", TIP, "--", "packages", "plugins", "scripts").splitlines()
    for line in lines:
        payload = line.split(":", 2)[2] if line.count(":") >= 2 else line
        if "__tests__" in line or "__test-utils__" in line:
            continue
        if "worktree list" in payload:
            continue
        print(f"  {line}")

    print("\n### forms carrying --ignored=matching (the release probe)")
    for line in git("grep", "-n", "-e", "--ignored=matching", TIP, "--", "packages", "plugins", "scripts").splitlines():
        print(f"  {line}")

    print("\n### blocking uses of ignored content in a release decision")
    print(f"  throws of WorktreeContentPreservationError: {total('throw new WorktreeContentPreservationError', TIP, '--', 'packages')}")
    gates = [
        line for line in git("grep", "-n", 'classification === "ignored-only"', TIP, "--", "packages/engine/src").splitlines()
        if "__tests__" not in line
    ]
    print(f"  conditions gating a REMOVAL on an ignored-only classification: {len([g for g in gates if 'acquisition' not in g])}")
    for gate in gates:
        print(f"    {gate}")
    print(f"  `regenerable-ignored` in engine/plugins/scripts: {total('regenerable-ignored', TIP, '--', 'packages/engine', 'plugins', 'scripts')}")
    print(f"  `regenerable-ignored` in core (out of this card's scope): {total('regenerable-ignored', TIP, '--', 'packages/core')}")

    print(f"\n### base ({BASE}) vs tip ({TIP}) occurrence counts over packages+plugins+scripts")
    for symbol in RETIRE:
        base = total(symbol, BASE, "--", "packages", "plugins", "scripts")
        tip = total(symbol, TIP, "--", "packages", "plugins", "scripts")
        flag = "" if base == tip else "   <-- changed"
        print(f"  {symbol:<52} base={base:<5} tip={tip:<5}{flag}")

    print("\n### the removal gate's own command vector at the tip")
    src = git("show", f"{TIP}:packages/engine/src/worktree/worktree-backend.ts")
    for number, text in enumerate(src.splitlines(), start=1):
        if 'execFileAsync("git", ["status"' in text:
            print(f"  worktree-backend.ts:{number}: {text.strip()}")


if __name__ == "__main__":
    main()
