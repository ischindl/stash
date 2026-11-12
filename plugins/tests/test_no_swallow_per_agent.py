"""Lock the fail-loud invariant on every plugin's hook scripts.

Each plugin source tree (`plugins/<agent>-plugin/scripts/`) and its shipped
assets (`stashai/plugin/assets/<agent>/scripts/`) must not contain a bare
`except Exception:`/`except BaseException:` or an unnamed `except:` that would
swallow a Stash API failure (upload, session-create, tool-use, session-end).
A swallow lets telemetry vanish without a trace; these must fail loud to the
agent's hook runner instead.

One legitimate parse guard is allowed to keep a default, but only under a narrow,
specific exception type - never a bare catch-all:
  - cursor adapt.py `_parse_tool_output` -> `except json.JSONDecodeError`
This allowance used to name claude's `config.py get_stdin_data` / `_read_json` as
well. Those functions no longer exist in claude's tree: the stdin reader moved into
the shared `stashai/plugin/agent_config.py`, which this file's directory scan never
covered, so the permission outlived its subject and the real catch-all sat
unscanned. `test_shared_stdin_reader_has_no_catch_all` below now locks that module,
and `get_stdin_data` raises instead of defaulting (STAS-417).
hermes and pi have NO parse guards - every Stash call in their on_* hooks
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

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "plugins"
DST_DIR = REPO_ROOT / "stashai" / "plugin" / "assets"
CLI_MAIN = REPO_ROOT / "cli" / "main.py"
SHARED_READER = REPO_ROOT / "stashai" / "plugin" / "agent_config.py"

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


def _reads_sys_stdin(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and child.attr == "stdin"
            and isinstance(child.value, ast.Name)
            and child.value.id == "sys"
        ):
            return True
    return False


def _catch_all_lines(func: ast.AST) -> list[int]:
    """Line numbers of handlers inside `func` that catch everything."""
    lines: list[int] = []
    for handler in ast.walk(func):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        if handler.type is None:
            lines.append(handler.lineno)
            continue
        caught = (
            handler.type.id if isinstance(handler.type, ast.Name) else ast.unparse(handler.type)
        )
        if caught in {"Exception", "BaseException"}:
            lines.append(handler.lineno)
    return lines


def test_shared_stdin_reader_has_no_catch_all() -> None:
    """The shared stdin reader every agent re-exports must never swallow a parse.

    `stashai/plugin/agent_config.py` is the single `get_stdin_data` behind all eight
    agents' `config.py`, so a catch-all there out-ranks every per-agent hook script -
    and it sat outside the directory scan above, which only walks
    `plugins/<agent>-plugin/scripts/` and `stashai/plugin/assets/<agent>/scripts/`.
    That is how `except Exception: return {}` survived there while this file policed
    the trees around it: every malformed, truncated, or absent payload became `{}` and
    was then reported as a payload problem ("keys present: []"), which is how a test
    payload came to be counted as a failure of the operator's own install (STAS-417).

    Locking the rule by shape (a function that reads `sys.stdin` may not catch all)
    rather than by name, so a renamed or added reader is covered too.
    """
    # The lock has to be able to fail, so first prove the detector sees the exact
    # shape this card removed. Without this, a scan that silently matched nothing
    # would look identical to a clean module.
    poisoned = ast.parse(
        "import json, sys\n"
        "def get_stdin_data():\n"
        "    try:\n"
        "        return json.loads(sys.stdin.read())\n"
        "    except Exception:\n"
        "        return {}\n"
    )
    removed_shape = poisoned.body[1]
    assert _reads_sys_stdin(removed_shape), "the detector misses a stdin reader"
    assert _catch_all_lines(removed_shape) == [5], "the detector misses the old swallow"

    source = SHARED_READER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SHARED_READER))
    readers = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _reads_sys_stdin(node)
    ]
    relative = SHARED_READER.relative_to(REPO_ROOT)
    assert readers, (
        f"no function in {relative} reads sys.stdin any more; this lock is now "
        "vacuous - move it with the reader instead of deleting it"
    )

    offenders = [
        f"{relative}:{line}: bare catch-all inside {reader.name}()"
        for reader in readers
        for line in _catch_all_lines(reader)
    ]
    assert not offenders, (
        "a stdin reader swallows the payload error; a required input must fail loud "
        "with what actually arrived:\n" + "\n".join(offenders)
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
