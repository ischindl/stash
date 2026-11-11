#!/usr/bin/env python3
"""STAS-397 AC6 — compare the two suite runs by FAILING TEST NAMES, not by wallclock.

A package suite's pass count moves for reasons that have nothing to do with a change (a timing
sensitive real-git case flakes once). What has to be provably unchanged is the SET of failing tests:
same names at the base and at the delivered tip means nothing regressed. The hash of the sorted set
is printed for both runs so the comparison is one number, and the file-level diff is printed so a
regression can be named rather than counted.

Usage: suite_diff.py BEFORE.raw.log AFTER.raw.log
"""
from __future__ import annotations

import hashlib
import re
import sys

FAIL = re.compile(r"^\s*FAIL\s+\|(?P<project>[^|]+)\|\s+(?P<file>\S+)\s+>\s+(?P<test>.+?)\s*$")
SUMMARY = re.compile(r"^\s*(Test Files|Tests|Errors|Unhandled)\b.*$")


def failing(path: str) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    summaries: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            bare = line.replace("\x1b", "")
            match = FAIL.match(bare.rstrip())
            if match:
                names.add(f"{match['project']} :: {match['file']} :: {match['test'].strip()}")
            if SUMMARY.match(bare.rstrip()):
                summaries.append(bare.rstrip())
    return names, summaries


def digest(names: set[str]) -> str:
    joined = "\n".join(sorted(names))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def files_of(names: set[str]) -> set[str]:
    return {name.split(" :: ")[1] for name in names}


def main() -> int:
    before_path, after_path = sys.argv[1], sys.argv[2]
    before, before_summary = failing(before_path)
    after, after_summary = failing(after_path)

    print("=== the package suite's own summary lines ===")
    for label, summaries in (("BASE", before_summary), ("TIP", after_summary)):
        for line in summaries:
            print(f"  {label}: {line}")

    print("\n=== failing test names ===")
    print(f"  BASE  count={len(before):3d}  sha256(sorted set)[:16]={digest(before)}")
    print(f"  TIP   count={len(after):3d}  sha256(sorted set)[:16]={digest(after)}")
    print(f"  failing-name-set hash diff rc={'0 (identical)' if digest(before) == digest(after) else 'NONZERO (sets differ)'}")

    new = after - before
    gone = before - after
    print(f"\n  new failing tests at the tip: {len(new)}")
    for name in sorted(new)[:40]:
        print(f"    + {name}")
    print(f"  failing-at-base-only (fixed by this card): {len(gone)}")
    for name in sorted(gone)[:40]:
        print(f"    - {name}")

    new_files = files_of(after) - files_of(before)
    print(f"\n  NEW FAILING FILES at the tip: {len(new_files)}")
    for name in sorted(new_files):
        print(f"    ! {name}")
    fixed_files = files_of(before) - files_of(after)
    print(f"  files failing only at base: {len(fixed_files)}")
    for name in sorted(fixed_files):
        print(f"    = {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
