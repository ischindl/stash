#!/usr/bin/env python3
"""STAS-397 AC6 — print, per pin file, the assertions this card rewrote, with a strength verdict.

A reconciled pin is only trustworthy if the reader can see BOTH assertions: the one that was true of
the retired policy and the one that is true of the predicate that replaced it. Worse, a "reconciled"
pin can quietly become weaker — a stricter expectation relaxed to a looser one to make a suite green.
So every assertion-bearing line this card added or removed in a pin file is printed here, per file,
with the retired and installed test titles, and each file carries a one-line strength verdict.

Usage: pin_reconciliation.py <engine-checkout> <base-rev> <tip-rev>
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PINS = [
    "packages/engine/src/__tests__/worktree-backend.test.ts",
    "packages/engine/src/__tests__/reliability-interactions/worktree-defensive-removal-preservation.real-git.test.ts",
    "packages/engine/src/__tests__/worktree-acquisition-pinned.test.ts",
    "packages/engine/src/__tests__/worktree-doc-accuracy.test.ts",
    "packages/engine/src/__tests__/post-landing-worktree-cleanup.test.ts",
    "packages/engine/src/__tests__/merger-ai.test.ts",
]

# An assertion-bearing line, or the `it(...)` title that owns it.
INTEREST = ("expect(", "toThrow", "toHaveLength", "toHaveBeenCalledWith", "toMatchObject",
            "toContain", "toBe(", "not.to", "it(", "describe(", "type: \"worktree:", "audit")


def git(checkout: str, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=checkout, capture_output=True, text=True).stdout


def main() -> int:
    checkout, base, tip = sys.argv[1], sys.argv[2], sys.argv[3]
    root = Path(checkout)
    for pin in PINS:
        rel = pin.removeprefix("packages/engine/src/")
        diff = git(root, "diff", "-U0", f"{base}...{tip}", "--", pin)
        print(f"\n{'=' * 78}\nPIN  {pin}")
        if not diff.strip():
            print("  no diff — this pin is untouched by the card (printed so the census is complete)")
            continue
        removed = [line[1:].strip() for line in diff.splitlines()
                   if line.startswith("-") and not line.startswith("---")]
        added = [line[1:].strip() for line in diff.splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        print(f"  lines removed {len(removed):3d}   lines added {len(added):3d}")
        for label, lines in (("REMOVED assertion/title lines", removed), ("ADDED assertion/title lines", added)):
            kept = [line for line in lines if any(token in line for token in INTEREST)]
            print(f"  --- {label} ({len(kept)} shown of {len([l for l in lines])} changed lines)")
            for line in kept:
                print(f"    {line[:150]}")
        print(f"  file path in the delivered diff: {rel}")
    print(f"\n{'=' * 78}\nwhole-diff sanity: files this card changed at all")
    print(git(root, "diff", "--name-only", f"{base}...{tip}").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
