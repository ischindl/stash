"""Lock the import boundary on every shipped plugin hook script.

Hook scripts are installed into a third-party runtime's hook directory (pi,
codex, claude, ...) and are executed by that runtime against *whatever*
``stashai`` package generation happens to be installed on the machine. The
hook's own generation and the installed package's generation are decoupled, so
the only surfaces a shipped hook may depend on are:

  - ``stashai.plugin.*`` — the versioned shared runtime that ships inside the
    same package as the scripts (e.g. ``from stashai.plugin.hooks import
    uploads_enabled``),
  - sibling bare-name modules that ``scripts/_run.sh`` puts on the import path
    at run time (``from adapt import ...``, ``from config import ...``),
  - the stdlib and plain ``print``.

``cli.*`` is the CLI's private internals and is NOT a compatibility surface: it
moves between releases. A ``cli.*`` import is therefore a latent silent
failure on every machine whose installed package predates the symbol.

Why this guard exists: a dev-worktree-era change made the shipped pi hooks do
``from cli.formatting import echo_stdout``. On the dogfood box, whose pinned
package predated that symbol, ``~/.pi/hooks/session_start`` died at import
(``ImportError: cannot import name 'echo_stdout' from 'cli.formatting'``, exit
code 1, zero bytes of stdout). pi ignores failing hooks, so session recording
was silently dead for days (STAS-248 covers the propagation side; this test is
the prevention side).

The guard is a static scan: it never imports or executes a hook script,
because a shipped asset run standalone fails on its sibling imports (only
``_run.sh`` supplies that path) and because importing several plugins'
``config.py``/``adapt.py`` into one process binds the bare names to whichever
plugin was imported first.

Like its sibling ``test_no_swallow_per_agent.py``, this guard DERIVES its agent
list from the ``PLUGIN_DATA_DIRS`` registry in ``cli/main.py`` (parsed as text,
not imported) and drift-locks it against both on-disk trees, so neither adding
nor removing an agent can silently shrink what is covered.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "plugins"
DST_DIR = REPO_ROOT / "stashai" / "plugin" / "assets"
CLI_MAIN = REPO_ROOT / "cli" / "main.py"

SOURCE_TREE = "source"
ASSET_TREE = "assets"

IGNORE_NAMES = {"__pycache__", "node_modules"}
IGNORE_SUFFIXES = {".pyc"}

# Non-.py files (pi's extensionless bash wrappers, scripts/_run.sh,
# scripts/ensure_cli.sh) are matched on import *syntax* with word boundaries,
# never on a bare ``cli`` substring: `[ -d "$REPO_ROOT/cli" ]` is a directory
# probe and ``import cli_config`` is a local module, not the CLI package.
TEXT_IMPORT = re.compile(r"\b(?:import|from)[ \t]+cli\b")
TEXT_LAZY_IMPORT = re.compile(r"""(?:import_module|__import__)[ \t]*\([ \t]*['"]cli\b""")

LAZY_IMPORT_FUNCTIONS = {"import_module", "__import__"}


def _registered_agents() -> tuple[str, ...]:
    """Agent keys declared in cli/main.py's PLUGIN_DATA_DIRS registry.

    ``hook_run`` runpy's the shipped asset scripts for every registered agent,
    so the registry is the real shipped surface. It is parsed as text rather
    than imported to keep ``plugins/tests`` free of CLI import deps.
    """
    block = re.search(
        r"^PLUGIN_DATA_DIRS = \{(.*?)^\}", CLI_MAIN.read_text(), re.DOTALL | re.MULTILINE
    )
    assert block, "cli/main.py no longer has a `PLUGIN_DATA_DIRS = {` block; update this guard."
    agents = re.findall(r'^\s*"([^"]+)":', block.group(1), re.MULTILINE)
    assert agents, "PLUGIN_DATA_DIRS registry parsed to zero agents; update this guard."
    return tuple(agents)


AGENTS = _registered_agents()


def _source_agent_dirs() -> set[str]:
    return {path.name.removesuffix("-plugin") for path in SRC_DIR.glob("*-plugin") if path.is_dir()}


def _asset_agent_dirs() -> set[str]:
    return {path.name for path in DST_DIR.iterdir() if path.is_dir()}


def _tree_root(agent: str, tree: str) -> Path:
    return SRC_DIR / f"{agent}-plugin" if tree == SOURCE_TREE else DST_DIR / agent


def _offender(path: Path, lineno: int, lines: list[str]) -> str:
    return f"{os.path.relpath(path, REPO_ROOT)}:{lineno}: {lines[lineno - 1].strip()}"


def _root_segment(name: str | None) -> str:
    return name.split(".")[0] if name else ""


def _lazy_import_root(node: ast.Call) -> str | None:
    """Constant module name when the call is a literal-string lazy import."""
    func = node.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if called not in LAZY_IMPORT_FUNCTIONS or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return _root_segment(first.value)
    return None


def _forbidden_lines(path: Path) -> list[str]:
    """Every line in `path` that reaches into the CLI's private `cli.*` package."""
    source = path.read_text()
    lines = source.splitlines()
    if path.suffix != ".py":
        return [
            _offender(path, idx, lines)
            for idx, line in enumerate(lines, start=1)
            if TEXT_IMPORT.search(line) or TEXT_LAZY_IMPORT.search(line)
        ]
    # A malformed .py under scripts/ raises here on purpose: a hook we cannot
    # parse is a hook we cannot trust, so the guard fails loud instead of
    # silently skipping it.
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            roots = [(_root_segment(alias.name), node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a sibling-relative import, not the CLI package.
            roots = [(_root_segment(node.module), node.lineno)] if node.level == 0 else []
        elif isinstance(node, ast.Call):
            root = _lazy_import_root(node)
            roots = [(root, node.lineno)] if root else []
        else:
            continue
        offenders.extend(_offender(path, lineno, lines) for root, lineno in roots if root == "cli")
    return offenders


def _scanned_files(scripts: Path) -> list[Path]:
    """Every shipped file under a tree's `scripts/`, recursively.

    Covers `.py` handlers, pi's extensionless bash hook wrappers and both shell
    helpers; only build artifacts are excluded, mirroring `test_assets_in_sync`.
    """
    assert scripts.is_dir(), f"expected {scripts.relative_to(REPO_ROOT)} to exist"
    return sorted(
        p
        for p in scripts.rglob("*")
        if p.is_file()
        and not any(part in IGNORE_NAMES for part in p.parts)
        and p.suffix not in IGNORE_SUFFIXES
    )


def _offenders_in_tree(root: Path) -> list[str]:
    offenders: list[str] = []
    for path in _scanned_files(root / "scripts"):
        offenders.extend(_forbidden_lines(path))
    return offenders


def test_registry_source_and_asset_agent_sets_are_identical() -> None:
    """Every registered agent must have both a source plugin tree and a shipped
    asset tree, and neither tree may carry an agent the registry does not
    register — otherwise this guard silently stops covering it."""
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
def test_shipped_hook_scripts_never_import_the_cli(agent: str, tree: str) -> None:
    """A shipped hook script must survive any installed `stashai` generation."""
    offenders = _offenders_in_tree(_tree_root(agent, tree))
    assert not offenders, (
        f"{agent} {tree} hook scripts import the CLI's private `cli.*` package, "
        f"which breaks at import time on any machine whose installed stashai predates "
        f"the symbol (STAS-249). Depend on `stashai.plugin.*` or the stdlib instead:\n"
        + "\n".join(offenders)
    )


# filename, source, and the exact offender string the guard must produce
# (relative path + line number + the verbatim source line). A guard that cannot
# fail is not a guard, and a guard that cannot point at the line is not usable.
OFFENDING_CASES = [
    pytest.param(
        "on_session_start.py",
        "from cli.formatting import echo_stdout\n",
        "from cli.formatting import echo_stdout",
        1,
        id="eager-from",
    ),
    pytest.param(
        "on_stop.py", "import cli.formatting\n", "import cli.formatting", 1, id="eager-import"
    ),
    pytest.param(
        "on_stop.py",
        "import cli.formatting as fmt\n",
        "import cli.formatting as fmt",
        1,
        id="eager-import-alias",
    ),
    pytest.param("on_stop.py", "import cli\n", "import cli", 1, id="eager-import-root"),
    pytest.param(
        "on_stop.py",
        "import json\n\nfmt = __import__('cli.formatting')\n",
        "fmt = __import__('cli.formatting')",
        3,
        id="lazy-dunder",
    ),
    pytest.param(
        "on_stop.py",
        "import importlib\n\nfmt = importlib.import_module('cli.formatting')\n",
        "fmt = importlib.import_module('cli.formatting')",
        3,
        id="lazy-importlib",
    ),
    pytest.param(
        "session_start",
        '#!/usr/bin/env bash\nexec python3 -c "from cli.formatting import echo_stdout"\n',
        'exec python3 -c "from cli.formatting import echo_stdout"',
        2,
        id="bash-embedded-python",
    ),
    pytest.param(
        "_run.sh",
        "#!/usr/bin/env bash\npython3 - <<'PY'\nimport cli.config\nPY\n",
        "import cli.config",
        3,
        id="bash-heredoc-python",
    ),
]

CONTROL_LINES = [
    "from stashai.plugin.hooks import uploads_enabled",
    "from stashai.plugin.agent_config import cli_config",
    "from stashai.plugin import hooks",
    "from adapt import adapt_event",
    "from config import get_stdin_data",
    "import json",
    "cli = _load_cli_config()",
    'endpoint = cli.get("base_url", PRODUCTION_BASE_URL)',
    "def _load_cli_config() -> dict:\n    return {}",
    "from . import adapt",
    "from .config import get_stdin_data",
    "import cli_config",
    "from cli_config import read_profile",
    'mod = __import__("cli_config")',
    'mod = importlib.import_module("stashai.plugin.hooks")',
    "# the CLI upgrade runs from scripts/ensure_cli.sh",
]


@pytest.mark.parametrize("filename,source,expected_line,expected_lineno", OFFENDING_CASES)
def test_guard_flags_cli_imports(
    filename: str, source: str, expected_line: str, expected_lineno: int, tmp_path: Path
) -> None:
    """Every forbidden form is caught and pinpointed as `path:line: source`."""
    path = tmp_path / filename
    path.write_text(source)

    assert _forbidden_lines(path) == [
        f"{os.path.relpath(path, REPO_ROOT)}:{expected_lineno}: {expected_line}"
    ]


def test_guard_leaves_the_allowed_surface_clean(tmp_path: Path) -> None:
    """Controls: the versioned shared runtime, the sibling bare names bound by
    `_run.sh`, relative imports, and the real shipped lines whose raw `cli`
    substrings an unanchored matcher would false-positive on."""
    path = tmp_path / "on_session_start.py"
    path.write_text("\n".join(CONTROL_LINES) + "\n")
    bash = tmp_path / "_run.sh"
    bash.write_text(
        "#!/usr/bin/env bash\n"
        'if [ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT/stashai" ] && [ -d "$REPO_ROOT/cli" ]; then\n'
        '  export PYTHONPATH="$REPO_ROOT"\n'
        "fi\n"
        'subprocess.run([c, "-c", "import stashai"], check=True)\n'
    )

    assert _forbidden_lines(path) == []
    assert _forbidden_lines(bash) == []


def test_shipped_trees_actually_have_scripts_to_scan() -> None:
    """The walk must not green out on an empty file list: every agent/tree pair
    that the parametrized guard covers really carries scannable files."""
    scanned = {
        (agent, tree): _scanned_files(_tree_root(agent, tree) / "scripts")
        for agent in AGENTS
        for tree in (SOURCE_TREE, ASSET_TREE)
    }

    assert all(files for files in scanned.values()), f"empty scripts tree in {scanned}"
    assert any(path.suffix != ".py" for files in scanned.values() for path in files), (
        "the bash hook wrappers must stay inside the scan"
    )
