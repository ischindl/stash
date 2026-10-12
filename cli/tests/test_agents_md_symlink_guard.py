"""Anti-merge-revert guard for the root ``AGENTS.md`` symlink.

Merge ``3da82e41`` resolved upstream #1123 ("Remove repository agent
instructions and token budgets") by keeping local ``CLAUDE.md`` while accepting
upstream's deletion of the ``AGENTS.md`` symlink. Nothing failed loudly: the
rules *content* was untouched, so every check that reads it stayed green while
the filename that Codex, OpenCode and pi actually read disappeared from the
tree. A fresh trunk checkout therefore handed those agents an empty ruleset —
including the operator PR gate (directive 2026-08-30) and the no-fallback rule.
STAS-333 restored the symlink; this module makes the same silent drop
impossible to land again.

The guard asserts only the tracked shape of the path, because that is exactly
the property a merge resolution can drop without changing a single byte of
rules content — which is why content-level checks could not catch it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _tracked_entry(path: str) -> str:
    """One ``git ls-files -s`` row for *path*, or "" when it is not tracked."""
    return _git("ls-files", "-s", "--", path).strip()


def test_agents_md_is_tracked_as_symlink():
    entry = _tracked_entry("AGENTS.md")
    assert entry.startswith("120000 "), (
        f"AGENTS.md is not tracked as a symlink (mode 120000); got {entry!r}. "
        "Upstream #1123 deleted it in merge 3da82e41 and no content check "
        "noticed, because CLAUDE.md was byte-identical across that merge. "
        "Restore `AGENTS.md -> CLAUDE.md` instead of deleting this check."
    )


def test_agents_md_symlink_points_at_claude_md():
    entry = _tracked_entry("AGENTS.md")
    assert entry.startswith("120000 "), f"AGENTS.md must be a symlink first; got {entry!r}"
    blob = entry.split()[1]
    assert _git("cat-file", "blob", blob) == "CLAUDE.md", (
        "AGENTS.md must be a symlink whose target is CLAUDE.md, so every "
        f"AGENTS.md-style tool reads the same rules as Claude Code; got {_git('cat-file', 'blob', blob)!r}."
    )


def test_working_tree_agents_md_carries_claude_md_rules():
    agents = REPO_ROOT / "AGENTS.md"
    claude = REPO_ROOT / "CLAUDE.md"
    assert agents.is_symlink(), (
        f"{agents} is not a working-tree symlink — a checkout made with "
        "core.symlinks=false, or a regular file committed instead of the link."
    )
    assert agents.read_bytes() == claude.read_bytes(), (
        f"{agents} does not resolve to {claude}; consumer agents are reading a "
        "different (or empty) ruleset than Claude Code."
    )
