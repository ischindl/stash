#!/usr/bin/env python3
"""STAS-397 Step 5 — reprint every suite/pin measurement this step took, from the raw logs.

The step re-runs three different things: the whole package suite once per rev (the AC6 baseline),
every pin file Step 2 named once per rev, and the six files whose full-run failing status differs
between the two full runs, twice per rev in isolation. The last one is what turns "2 new failing
files" into a named verdict, so it is printed from the logs rather than from memory.

Usage: step5_repeats.py <dir-with-raw-logs>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# A whole-suite run prints these two lines; a targeted run prints the same shape with fewer files.
SUMMARY = re.compile(r"^\s*(Test Files|Tests)\s{2,}\S.*$")
FAIL = re.compile(r"^\s*FAIL\s+\|(?P<project>[^|]+)\|\s+(?P<file>\S+)(?P<rest>.*)$")

# label -> (log file, what it proves)
RUNS: list[tuple[str, str, str]] = [
    ("whole package suite, BASE 46b94afa0c", "before-suite.raw.log",
     "AC6 baseline: the package's own `test` command at the base commit"),
    ("whole package suite, TIP 3ab0e55698", "after-suite.raw.log",
     "AC6 after: the identical command at the delivered tip"),
    ("pin files + guard, TIP", "tip-pins-rerun.log",
     "every pin Step 2 named plus this card's guard, re-run at the delivered tip"),
    ("pin files (no guard), BASE", "base-pins-rerun.log",
     "the same six pin files at the base: green there too, so this card broke no pin"),
    ("pin files, per-file counts, TIP", "tip-pins-verbose.log",
     "verbose reporter, to count tests per pin file at the tip"),
    ("pin files, per-file counts, BASE", "base-pins-verbose.log",
     "verbose reporter, to count tests per pin file at the base"),
    ("guard file alone, TIP", "tip-guard-solo.log",
     "AC3's green-after print: passing count and skipped count for the guard file itself"),
    ("the 2 files newly failing at the tip, in isolation, TIP", "tip-isolated-2files.log",
     "does the delivered code actually fail them, with nothing else on the machine"),
    ("the same 2 files, 3 further isolated repetitions, TIP", "tip-isolated-repeat.log",
     "same rev, same command, repeated: a deterministic failure cannot alternate outcomes"),
    ("the 7 differing files, union run 1, BASE", "base-union7.log",
     "the same 7 files, same command, at the base"),
    ("the 7 differing files, union run 1, TIP", "tip-union7.log",
     "the same 7 files, same command, at the tip"),
    ("the 7 differing files, union run 2, BASE", "base-union7-pass2.log",
     "repeat at the base: does the base's own failing set move between identical runs"),
    ("the 7 differing files, union run 2, TIP", "tip-union7-pass2.log",
     "repeat at the tip: does the tip's own failing set move between identical runs"),
]


def bare_lines(path: Path) -> list[str]:
    text = path.read_bytes().replace(b"\x00", b"").decode("utf-8", "replace")
    return [ANSI.sub("", line).strip() for line in text.replace("\r", "\n").split("\n")]


def per_file_counts(lines: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        if not (line.startswith("[32m✓") or "✓" in line[:4] or line.startswith("✓")):
            continue
        match = re.search(r"(src/\S+?\.test\.ts)", line)
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def main() -> int:
    directory = Path(sys.argv[1])
    for label, name, purpose in RUNS:
        path = directory / name
        print(f"\n=== {label}")
        print(f"    log: .fusion-tmp/stas-397/{name}")
        print(f"    purpose: {purpose}")
        if not path.exists():
            print("    MISSING LOG — nothing measured")
            continue
        lines = bare_lines(path)
        for line in lines:
            if SUMMARY.match(line):
                print(f"    {line.strip()}")
        counts = per_file_counts(lines)
        if counts:
            print("    per-file test counts:")
            for file in sorted(counts):
                print(f"      {counts[file]:4d}  {file}")
            print(f"      ----  total {sum(counts.values())}")
        fails = [FAIL.match(line) for line in lines]
        names = sorted({f"{m['file']}{m['rest'].split(' > ')[0] if m['rest'] else ''}" for m in fails if m})
        print(f"    distinct FAIL entries: {len(names)}")
        for name in names[:12]:
            print(f"      {name}")
        if len(names) > 12:
            print(f"      ... {len(names) - 12} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
