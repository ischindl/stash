"""Assert that `stashai/plugin/assets/<agent>/` is a byte-identical copy of
`plugins/<agent>-plugin/`.

`stash connect` reads from the shipped stashai assets so users don't need the
repo. `plugins/<agent>-plugin/` is the canonical source contributors edit.
Drift between the two means `stash connect` would hand out stale hook files.

The covered agents are DERIVED from the `PLUGIN_DATA_DIRS` registry in
`cli/main.py` (parsed as text, not imported — same technique as
`test_no_swallow_per_agent.py`) instead of a hardcoded tuple: a tuple has to be
remembered, and an agent missing from it silently escapes parity coverage. A
newly registered agent flows into this guard automatically; `SCRIPTS_ONLY_PY_AGENTS`
is the one explicit, asserted carve-out.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "plugins"
DST_DIR = REPO_ROOT / "stashai" / "plugin" / "assets"
CLI_MAIN = REPO_ROOT / "cli" / "main.py"

# Agents whose mirror is narrower than a whole-tree byte-for-byte copy, and how
# narrow: claude ships only scripts/*.py (see test_claude_scripts_match_shipped_assets).
SCRIPTS_ONLY_PY_AGENTS = ("claude",)
IGNORE_NAMES = {"__pycache__", "node_modules"}
IGNORE_SUFFIXES = {".pyc"}


def _registered_agents() -> tuple[str, ...]:
    """Agent keys declared in cli/main.py's PLUGIN_DATA_DIRS registry.

    `hook_run` executes the shipped asset scripts for every registered agent,
    so the registry is the real shipped surface. Parsed as text rather than
    imported to keep `plugins/tests` free of CLI import deps.
    """
    block = re.search(
        r"^PLUGIN_DATA_DIRS = \{(.*?)^\}", CLI_MAIN.read_text(), re.DOTALL | re.MULTILINE
    )
    assert block, "cli/main.py no longer has a `PLUGIN_DATA_DIRS = {` block; update this guard."
    agents = re.findall(r'^\s*"([^"]+)":', block.group(1), re.MULTILINE)
    assert agents, "PLUGIN_DATA_DIRS registry parsed to zero agents; update this guard."
    return tuple(agents)


AGENTS = _registered_agents()
BYTE_FOR_BYTE_AGENTS = tuple(agent for agent in AGENTS if agent not in SCRIPTS_ONLY_PY_AGENTS)


def _iter_tracked_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_NAMES for part in path.relative_to(root).parts):
            continue
        if path.suffix in IGNORE_SUFFIXES:
            continue
        yield path.relative_to(root), path


def test_every_agent_has_shipped_assets():
    for agent in AGENTS:
        assert (DST_DIR / agent).is_dir(), (
            f"Missing shipped assets for {agent!r}: expected {DST_DIR / agent} to exist"
        )


def _assert_in_sync(
    agent: str, src_root: Path, dst_root: Path, suffixes: set[str] | None = None
) -> None:
    def _tracked(root: Path) -> dict:
        return {
            rel: path
            for rel, path in _iter_tracked_files(root)
            if suffixes is None or path.suffix in suffixes
        }

    src_map = _tracked(src_root)
    dst_map = _tracked(dst_root)

    assert set(src_map) == set(dst_map), (
        f"{agent}: file set drift between {src_root} and {dst_root}. "
        f"Only in source: {sorted(set(src_map) - set(dst_map))}; "
        f"only in assets: {sorted(set(dst_map) - set(src_map))}"
    )

    for rel in src_map:
        src_bytes = src_map[rel].read_bytes()
        dst_bytes = dst_map[rel].read_bytes()
        assert src_bytes == dst_bytes, (
            f"{agent}: {rel} differs between {src_root} and {dst_root}. "
            f"Re-run the vendor copy when editing plugin sources."
        )


def test_assets_match_plugin_sources_byte_for_byte():
    for agent in BYTE_FOR_BYTE_AGENTS:
        _assert_in_sync(agent, SRC_DIR / f"{agent}-plugin", DST_DIR / agent)


def test_claude_scripts_match_shipped_assets():
    """Claude ships hooks.json and docs through the Claude Code marketplace,
    but `stash hook run claude` executes the scripts from the shipped assets —
    only scripts/ is mirrored (the marketplace manifest's version field is
    CI-bumped, so a whole-dir mirror would drift on every release).

    Only `.py` is mirrored: `hook_run` resolves `<event>.py` and nothing else,
    so a shell helper here would be unreachable. ensure_cli.sh is invoked by
    Claude Code from ${CLAUDE_PLUGIN_ROOT}, which is the marketplace copy, and
    is deliberately not vendored.

    This is the dedicated parity path for the sole member of
    `SCRIPTS_ONLY_PY_AGENTS`; the partition test below pins that set to claude
    so a carve-out agent can never end up covered by neither loop.
    """
    _assert_in_sync(
        "claude",
        SRC_DIR / "claude-plugin" / "scripts",
        DST_DIR / "claude" / "scripts",
        suffixes={".py"},
    )


def test_parity_loops_partition_the_registered_agents():
    """Every registered agent is covered by exactly one parity loop, and no
    agent can slip between them: the byte-for-byte loop and the narrower
    scripts-only loop must partition the registry. Adding an agent to
    `PLUGIN_DATA_DIRS` therefore lands in coverage automatically, and moving
    one between loops stays a deliberate, visible edit."""
    assert SCRIPTS_ONLY_PY_AGENTS == ("claude",), (
        f"SCRIPTS_ONLY_PY_AGENTS {SCRIPTS_ONLY_PY_AGENTS} changed: every member needs a "
        f"dedicated narrower parity test (claude has test_claude_scripts_match_shipped_assets), "
        f"otherwise it is covered by neither loop"
    )
    assert set(BYTE_FOR_BYTE_AGENTS) & set(SCRIPTS_ONLY_PY_AGENTS) == set(), (
        "no agent in both loops"
    )
    assert set(BYTE_FOR_BYTE_AGENTS) | set(SCRIPTS_ONLY_PY_AGENTS) == set(AGENTS), (
        f"parity loops {sorted(BYTE_FOR_BYTE_AGENTS)} + {sorted(SCRIPTS_ONLY_PY_AGENTS)} "
        f"do not cover the registered agents {sorted(AGENTS)}"
    )
