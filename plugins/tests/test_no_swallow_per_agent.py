"""Lock the fail-loud invariant on every plugin's hook scripts.

Each plugin source tree (`plugins/<agent>-plugin/scripts/`) and its shipped
assets (`stashai/plugin/assets/<agent>/scripts/`) must not contain a bare
`except Exception:`/`except BaseException:` or an unnamed `except:` that would
swallow a Stash API failure (upload, session-create, tool-use, session-end).
A swallow lets telemetry vanish without a trace; these must fail loud to the
agent's hook runner instead.

Two legitimate parse/IO guards are allowed to keep a default, but only under a
narrow, specific exception type — never a bare catch-all:
  - cursor adapt.py `_parse_tool_output` -> `except json.JSONDecodeError`
  - claude config.py `get_stdin_data` / `_read_json` -> `except (json.JSONDecodeError, OSError)`
hermes and pi have NO such parse guards — every Stash call in their on_* hooks
is fail-loud, so no narrow guard is allowed or needed there.

This test also asserts the STAS-019 silent-default (`if not cwd: return False`)
is absent from every config.py, so a missing working dir fails loud instead of
silently falling back.

Deltas from the original STAS-071/083 shape, forced by today's layout:

1. Covered agents are DERIVED from the `PLUGIN_DATA_DIRS` registry in
   `cli/main.py` instead of a hardcoded tuple. `hook_run` runpy's the shipped
   asset scripts for every registered agent, so the registry is the real
   surface: an agent added to `PLUGIN_DATA_DIRS` is locked automatically and
   one removed cannot be silently left behind. The registry is parsed as text
   rather than imported to keep `plugins/tests` free of CLI import deps.
2. A drift lock asserts registry keys == `plugins/*-plugin` directories ==
   `stashai/plugin/assets/<agent>` directories, so a new agent cannot escape
   the guard by skipping a test edit, and a missing asset tree also fails loud.
3. Both trees are checked for ALL registered agents. The original `ASSET_AGENTS`
   five-tuple is stale: claude, gemini and openclaw now ship mirrored scripts
   too, and `test_assets_in_sync.py` enforces byte-parity for all eight, so
   checking only the source copy would let a swallow survive in what users
   actually execute.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "plugins"
DST_DIR = REPO_ROOT / "stashai" / "plugin" / "assets"
CLI_MAIN = REPO_ROOT / "cli" / "main.py"

BARE_CATCHALL = re.compile(r"^\s*except (Exception|BaseException):")
UNNAMED_CATCHALL = re.compile(r"^\s*except:")

# STAS-019's silent-default: `if not cwd:` directly falling through to
# `return False` swallows a missing working dir instead of failing loud. We
# match the immediate fallback only (the exact pattern STAS-019 banned) so the
# pi is_fusion_managed walk — which legitimately raises ValueError on an empty
# cwd — is not falsely flagged.
SILENT_DEFAULT = re.compile(r"if not cwd\s*:\s*return False")


def _registered_agents() -> tuple[str, ...]:
    """Agent keys declared in cli/main.py's PLUGIN_DATA_DIRS registry."""
    block = re.search(
        r"^PLUGIN_DATA_DIRS = \{(.*?)^\}", CLI_MAIN.read_text(), re.DOTALL | re.MULTILINE
    )
    assert block, "cli/main.py no longer has a `PLUGIN_DATA_DIRS = {` block; update this guard."
    agents = re.findall(r'^\s*"([^"]+)":', block.group(1), re.MULTILINE)
    assert agents, "PLUGIN_DATA_DIRS registry parsed to zero agents; update this guard."
    return tuple(agents)


AGENTS = _registered_agents()

SOURCE_TREE = "source"
ASSET_TREE = "assets"


def _source_agent_dirs() -> set[str]:
    return {path.name.removesuffix("-plugin") for path in SRC_DIR.glob("*-plugin") if path.is_dir()}


def _asset_agent_dirs() -> set[str]:
    return {path.name for path in DST_DIR.iterdir() if path.is_dir()}


def _tree_root(agent: str, tree: str) -> Path:
    return SRC_DIR / f"{agent}-plugin" if tree == SOURCE_TREE else DST_DIR / agent


def _offending_lines(root: Path) -> list[str]:
    scripts = root / "scripts"
    assert scripts.is_dir(), f"expected {scripts.relative_to(REPO_ROOT)} to exist"
    offenders: list[str] = []
    for path in sorted(p for p in scripts.glob("*.py") if "__pycache__" not in p.parts):
        for idx, line in enumerate(path.read_text().splitlines(), start=1):
            if BARE_CATCHALL.search(line) or UNNAMED_CATCHALL.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{idx}: {line.strip()}")
    return offenders


def test_registry_source_and_asset_agent_sets_are_identical() -> None:
    """Every registered agent must have both a source plugin tree and a shipped
    asset tree, and neither tree may carry an agent the registry does not
    register — otherwise the guard below silently stops covering it."""
    assert _source_agent_dirs() == set(AGENTS), (
        f"plugins/ trees {sorted(_source_agent_dirs())} do not match "
        f"PLUGIN_DATA_DIRS keys {sorted(AGENTS)}"
    )
    assert _asset_agent_dirs() == set(AGENTS), (
        f"shipped asset trees {sorted(_asset_agent_dirs())} do not match "
        f"PLUGIN_DATA_DIRS keys {sorted(AGENTS)}"
    )


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("tree", [SOURCE_TREE, ASSET_TREE])
def test_no_swallow_in_source_and_shipped_assets(agent: str, tree: str) -> None:
    """No hook script — canonical source or shipped asset — may catch-all."""
    offenders = _offending_lines(_tree_root(agent, tree))
    assert not offenders, (
        f"{agent} {tree} hooks swallow errors; Stash API failures must fail loud:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("agent", AGENTS)
def test_config_has_no_silent_default(agent: str) -> None:
    """No plugin config.py may carry STAS-019's `if not cwd: return False`
    silent-default, which makes a missing working dir silently fall back instead
    of failing loud."""
    offenders: list[str] = []
    for tree in (SOURCE_TREE, ASSET_TREE):
        cfg = _tree_root(agent, tree) / "scripts" / "config.py"
        assert cfg.is_file(), f"expected {cfg.relative_to(REPO_ROOT)} to exist"
        for idx, line in enumerate(cfg.read_text().splitlines(), start=1):
            if SILENT_DEFAULT.search(line):
                offenders.append(f"{cfg.relative_to(REPO_ROOT)}:{idx}: {line.strip()}")
    assert not offenders, (
        f"{agent} config silently defaults on an empty cwd; raise ValueError instead:\n"
        + "\n".join(offenders)
    )
