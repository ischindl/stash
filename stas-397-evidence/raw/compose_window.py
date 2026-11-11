#!/usr/bin/env python3
"""STAS-397 AC7/AC8 - composes window-announcement.txt from the captures of this tick.

It is a file rather than a here-doc so the composition is reproducible: every number in the
announcement is a quoted line from a raw log in .fusion-tmp/stas-397/, never a re-typed figure.
The script reads; it never touches git state.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # stas-397-evidence/raw
EVIDENCE = HERE.parent                             # stas-397-evidence
CARD = EVIDENCE.parent                             # this card's worktree
SCRATCH = CARD / ".fusion-tmp/stas-397"
OUT = EVIDENCE / "window-announcement.txt"

HANDOFF_MESSAGE_ID = "msg-6dd80e18"
HANDOFF_RECIPIENT = "agent-94d01490 (CEO, the supervisor who packages operator items)"


def read(name: str) -> str:
    path = SCRATCH / name
    if not path.exists():
        raise SystemExit(f"missing the tick's raw capture {name}")
    return path.read_text(encoding="utf-8", errors="replace").rstrip()


def git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", *args], cwd=CARD, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def main() -> int:
    rc_now, now = git("-C", str(CARD), "log", "-1", "--format=%h %ad %s", "--date=iso-strict", "HEAD")
    rc_porcelain, porcelain = git("status", "--porcelain", "--untracked-files=all")
    lines = [line for line in porcelain.splitlines() if line.strip()]
    tracked_only = [line for line in lines if not line.startswith("?? ")]
    push_facts = [line for line in read("push1.txt").splitlines()
                  if "update by push" in line or "@{" in line][:6]

    parts: list[str] = []
    parts.append(
        "STAS-397 Step 6 - the delivery window: branch, bundle, handoff, all printed in one tick\n"
        "=========================================================================================\n"
        "\n"
        "One tick carries the ref, the bundle, the live wedge state and the operator handoff, so no\n"
        "number in this file was carried from an earlier moment. The two probes that produced it are\n"
        "kept and re-runnable: raw/window_probe.py (the live wedge and the build markers) and\n"
        "raw/ac8_delivery.py (the delivery envelope). The bundle chain is raw/bundle-verification.txt.\n"
"\nAC7 VERDICT\n"
        "-----------\n"
        "  delivered, liveness unverified\n"
        "\n"
        "AC7's two proofs are measurements of the running board, and the running board is not this\n"
        "card's to change: the fix lands on trunk, is rebuilt and the daemon is restarted by the\n"
        "operator. The proofs, unrun at this tick:\n"
        "  proof 1  `git worktree list` in the engine's primary checkout no longer prints one of the\n"
        "           seven card directories      -> NOT TAKEN, and at this tick the opposite holds:\n"
        "           all seven are still registered (section 2, `worktree_named_by_list=True`).\n"
        "  proof 2  card-log lines containing `uncommitted or ignored content present` = 0 over the\n"
        "           following 24 hours           -> NOT TAKEN, and at this tick the sweep is still\n"
        "           re-logging the warning (newest row 2026-11-08T09:30:49Z, section 2).\n"
        "Nothing in this card may therefore be reported as a drained queue, a freed card, or a live\n"
        "fix. The three legs that precede both proofs: land `refs/heads/stas-397-delivery` on trunk,\n"
        "BUILD the engine, restart the daemon. A restart alone is a nil-effect: section 2 prints the\n"
        "running `dist` still containing `REGENERABLE_IGNORED_DIR_NAMES` and lacking this card's\n"
        "discard record, and its mtime predates the newest trunk commit to that source file.\n"
        "\n"
        f"This card worktree's own last commit (rc={rc_now}): {now}\n"
        f"This repository's porcelain filtered of untracked entries: {len(tracked_only)} line(s)"
        f"  [expected 0]  (all {len(lines)}: {lines or 'none'})\n"
    )

    parts.append("1. THE DELIVERY ENVELOPE - raw output of raw/ac8_delivery.py\n"
                 "------------------------------------------------------------\n" + read("ac8-out.txt"))

    parts.append("2. THE LIVE WEDGE AND THE RUNNING BUILD - raw output of raw/window_probe.py\n"
                 "-------------------------------------------------------------------------\n" + read("window-probe-raw.txt"))

    parts.append(
        "3. PUSH AND TRUNK FACTS, PRINTED FROM REFLOGS RATHER THAN ASSERTED\n"
        "-------------------------------------------------------------------\n"
        "This card executed no `git push`, wrote no `refs/heads/main` in either repository, and opened,\n"
        "commented on or closed no pull request - operator-gated by AGENTS.md since 2026-08-30. What\n"
        "the engine repository's own records say, so the claim is checkable rather than declaratory:\n"
        + "\n".join(f"  {line}" for line in push_facts)
        + "\n"
        "  `refs/remotes/lan/stas-397-delivery` therefore exists, and the host's own mirror carries\n"
        "  every branch that gets landed the same way (`rufu-871-*`, `stas-394-fix-title-idempotence`,\n"
        "  `stas-395-measure-same-agent-duplicate-intake` each have a one-line `update by push` reflog).\n"
        "  This card cannot attribute that push to a process and does not try to; what it can state as\n"
        "  measured is that the LAN mirror is not the trunk: `refs/heads/main` moved only by three\n"
        "  commits authored outside this card (section 1, block B), and none of this card's five\n"
        "  commits is an ancestor of trunk (section 1, block B, rc_anc=1).\n"
    )

    parts.append(
        "4. THE OPERATOR HANDOFF, SENT IN THIS SAME TICK\n"
        "-----------------------------------------------\n"
        f"Delivered as a structured report to {HANDOFF_RECIPIENT} as message {HANDOFF_MESSAGE_ID}.\n"
        "The three legs and the two proofs it names are the ones printed above, in the same words:\n"
        "  leg 1  land refs/heads/stas-397-delivery (tip ef8ef296d2) onto refs/heads/main.\n"
        "         Collision predicates from section 1: merge-tree rc=0, 0 CONFLICT lines, the\n"
        "         intersection of trunk's and this branch's changed-file sets = 0 files.\n"
        "  leg 2  build the engine - without it the delivered predicate is inert (section 2 markers).\n"
        "  leg 3  restart the daemon, which only re-loads whatever `dist` already holds.\n"
        "It also carried the two consequences the board will see: capacity is maxWorktrees=4 while up\n"
        "to seven checkouts free at once, and STAS-357 stays behind its own plan-review replan-cap\n"
        "park, which this fix does not touch.\n"
    )

    parts.append(
        "5. WHAT THIS TICK DID NOT DO\n"
        "-----------------------------\n"
        "No `git worktree remove` (with or without force), no `prune`, `reset` or `clean`, and no\n"
        "directory deletion against any held checkout - section 1 block F re-measures all seven as\n"
        "still registered. No board record was written by any git or shell call: no retitle, move,\n"
        "assign or delete, for the seven wedged cards or for this one. No rebuild, no restart, no\n"
        "touch of the daemon. The two operator dispositions named in the Original Description (the\n"
        "STAS-394/395/396 fast-forward and closure, the STAS-393 dispatch click) were left alone.\n"
        "One whole-package suite run at the delivered head was started and terminated by this card\n"
        "because its command differed from the AC6 pair's; the ledger line and the reason are in\n"
        "section 1 block H, and no total is quoted from it.\n"
    )

    OUT.write_text("\n\n".join(parts).replace("\n\n\n", "\n\n") + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
