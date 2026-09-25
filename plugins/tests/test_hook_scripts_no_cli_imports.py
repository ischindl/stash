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

Because the same decoupling makes *every* symbol a shipped hook touches a
compatibility surface — not just the ones whose module name is ``cli`` — the
guard resolves the rest of that boundary too: it imports the ``stashai.*``
modules a hook names (importing the *package* is safe; importing the hook is
not), checks each imported name exists in the installed generation, and binds
every call site's arguments against the real signature. The sibling
``adapt``/``config`` names are resolved from those files' ASTs instead, for the
collision reason above. Drift classes this catches that a runtime test only
catches when its fixture happens to reach the call: a renamed or deleted symbol
used on a rarer branch, and a call whose arity or keyword names no longer bind.

Like its sibling ``test_no_swallow_per_agent.py``, this guard DERIVES its agent
list from the ``PLUGIN_DATA_DIRS`` registry in ``cli/main.py`` (parsed as text,
not imported) and drift-locks it against both on-disk trees, so neither adding
nor removing an agent can silently shrink what is covered.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import re
import sys
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


# --- The rest of the compatibility boundary: symbols and call signatures ---

PACKAGE_ROOT = "stashai"
# Bare-name modules that `_run.sh` puts on the import path at run time.
SIBLING_ROOTS = {"adapt", "config"}
CONTRACT_ROOTS = {PACKAGE_ROOT, *SIBLING_ROOTS}

# An imported name that resolves but is not a function: the guard checks the name
# exists and stops there, because its callable shape is its own type's business.
_OPAQUE = "opaque"
_MISSING = object()


class _Sibling:
    """A sibling function's parameters, read from its source.

    `inspect.signature` would be the obvious tool and cannot be used here: it
    needs the module imported, and importing several plugins' `config.py` into one
    process binds the bare name to whichever file was imported first.
    """

    def __init__(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        self.positional = [a.arg for a in args.posonlyargs + args.args]
        self.required = len(self.positional) - len(args.defaults)
        self.keyword_only = [a.arg for a in args.kwonlyargs]
        self.extra_positional = args.vararg is not None
        self.extra_keyword = args.kwarg is not None


def _sibling_surface(scripts: Path, stem: str) -> dict[str, object]:
    """Every name a sibling module defines or re-exports at module level."""
    surface: dict[str, object] = {}
    for node in ast.parse((scripts / f"{stem}.py").read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            surface[node.name] = _Sibling(node)
        elif isinstance(node, ast.ClassDef):
            surface[node.name] = _OPAQUE
        elif isinstance(node, ast.Assign):
            surface.update({t.id: _OPAQUE for t in node.targets if isinstance(t, ast.Name)})
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            surface.update(
                {alias.asname or alias.name.split(".")[0]: _OPAQUE for alias in node.names}
            )
    return surface


def _package_binding(module: str, symbol: str | None) -> tuple[str, object]:
    """Where a `stashai.*` import lands in the installed generation.

    `import stashai.plugin.agent_config` binds only the root name, so that form
    resolves to the root package and its attribute chain is followed at the call
    site. `from stashai.plugin import agent_config` names a submodule the package
    has not imported yet — the attribute is missing while the import is valid — so
    it falls back to importing the submodule.
    """
    if symbol is None:
        # `import a.b.c` imports the whole chain, so the call site's attribute walk
        # needs that chain present before it is followed.
        importlib.import_module(module)
        return PACKAGE_ROOT, importlib.import_module(PACKAGE_ROOT)
    imported = importlib.import_module(module)
    if hasattr(imported, symbol):
        return f"{module}.{symbol}", getattr(imported, symbol)
    return f"{module}.{symbol}", importlib.import_module(f"{module}.{symbol}")


def _imports(node: ast.stmt) -> list[tuple[str, str | None, str]] | None:
    """(module, symbol, local name) for each name an import statement binds."""
    if isinstance(node, ast.Import):
        return [
            (alias.name, None, alias.asname or alias.name.split(".")[0]) for alias in node.names
        ]
    if isinstance(node, ast.ImportFrom) and node.level == 0:
        return [(str(node.module), alias.name, alias.asname or alias.name) for alias in node.names]
    return None


def _bindings(
    tree: ast.Module, path: Path, scripts: Path, lines: list[str]
) -> tuple[dict[str, tuple[str, object]], list[str]]:
    """Map each imported local name to the symbol it must mean.

    Resolution is against the installed generation for `stashai.*` and against the
    sibling file's own source for `adapt`/`config`. A name the surface does not
    provide, and any import root outside stdlib/package/sibling, is reported: an
    unchecked import is the exact hole that took pi's recording out for days.
    """
    offenders: list[str] = []
    bindings: dict[str, tuple[str, object]] = {}
    surfaces: dict[str, dict[str, object]] = {}

    for node in ast.walk(tree):
        for module, symbol, local in _imports(node) or []:
            root = _root_segment(module)
            if root is None or root in sys.stdlib_module_names:
                continue  # relative imports and the stdlib are not this boundary
            if root not in CONTRACT_ROOTS:
                offenders.append(
                    _why(
                        path,
                        node.lineno,
                        lines,
                        f"`{module}` is neither the stdlib, the stashai package, "
                        f"nor a sibling {sorted(SIBLING_ROOTS)}",
                    )
                    + " — a shipped hook runs on whatever interpreter `_run.sh` finds, "
                    "so it cannot depend on anything else being installed"
                )
                continue

            if root == PACKAGE_ROOT:
                try:
                    bindings[local] = _package_binding(module, symbol)
                except (ImportError, AttributeError) as error:
                    offenders.append(
                        _why(
                            path,
                            node.lineno,
                            lines,
                            _import_gap(module, symbol),
                        )
                        + f" ({error}) — a shipped hook is held to the package it ships inside"
                    )
                    continue
                continue

            origin = f"{root}.{local}"
            if not (scripts / f"{root}.py").exists():
                offenders.append(
                    _why(path, node.lineno, lines, f"`{root}.py` is not in this tree")
                    + f", so {origin} cannot resolve"
                )
                continue
            surfaces.setdefault(root, _sibling_surface(scripts, root))
            target = surfaces[root].get(str(symbol), _MISSING)

            if target is _MISSING:
                offenders.append(
                    _why(path, node.lineno, lines, f"{origin} is not defined by the sibling module")
                )
                continue
            bindings[local] = (origin, target)
    return bindings, offenders


def _called_symbol(node: ast.Call, bindings: dict[str, tuple[str, object]]):
    """What a call names, when it names an imported symbol; otherwise None.

    `hooks.stream_tool_use(...)` and `stream_tool_use(...)` both resolve; a chain
    hanging off an `_OPAQUE` name does not, because its type is unknown here.
    """
    attributes: list[str] = []
    expr = node.func
    while isinstance(expr, ast.Attribute):
        attributes.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name) or expr.id not in bindings:
        return None

    origin, target = bindings[expr.id]
    for attribute in reversed(attributes):
        origin = f"{origin}.{attribute}"
        if target is _OPAQUE or isinstance(target, _Sibling):
            return None
        target = getattr(target, attribute, _MISSING)
        if target is _MISSING:
            return origin, _MISSING
    return origin, target


def _bind_failure(target: object, positional: int, keywords: list[str]) -> str | None:
    """Why calling `target` with that shape cannot bind, or None when it can."""
    if isinstance(target, _Sibling):
        unexpected = [k for k in keywords if k not in target.positional + target.keyword_only]
        if unexpected:
            return f"got an unexpected keyword argument: {unexpected}"
        if positional > len(target.positional) and not target.extra_positional:
            return f"takes at most {len(target.positional)} positional arguments, got {positional}"
        if positional < target.required:
            return f"takes at least {target.required} positional arguments, got {positional}"
        clashed = [k for k in keywords if k in target.positional[:positional]]
        if clashed:
            return f"got multiple values for argument: {clashed}"
        return None

    try:
        signature = inspect.signature(target)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    try:
        signature.bind(*[None] * positional, **dict.fromkeys(keywords))
    except TypeError as error:
        return str(error)
    return None


def _import_gap(module: str, symbol: str | None) -> str:
    """How the installed package falls short of one import statement."""
    named = f"does not provide {symbol}" if symbol else "is not importable"
    return f"`{module}` {named} in the installed package"


def _why(path: Path, lineno: int, lines: list[str], what: str) -> str:
    """An offender prefix: `repo/relative/path:line: source line`."""
    return f"{_offender(path, lineno, lines)}: {what}"


def _contract_offenders(path: Path, scripts: Path) -> list[str]:
    """Lines of a shipped hook script the installed package cannot honour.

    Either the symbol named does not exist, or the arguments passed to it cannot
    bind. Call sites that unpack `*args`/`**kwargs` are reported rather than
    skipped: a check that quietly gives up is how a regression reaches a machine.
    """
    source = path.read_text()
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    bindings, offenders = _bindings(tree, path, scripts, lines)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _called_symbol(node, bindings)
        if called is None:
            continue
        origin, target = called
        if target is _MISSING:
            offenders.append(
                _why(path, node.lineno, lines, f"{origin} is not in the installed package")
            )
            continue

        positional = [argument for argument in node.args if not isinstance(argument, ast.Starred)]
        keywords = [keyword.arg for keyword in node.keywords]
        if len(positional) != len(node.args) or None in keywords:
            offenders.append(
                _why(
                    path,
                    node.lineno,
                    lines,
                    f"{origin} is called with unpacked arguments, which this guard cannot bind",
                )
                + " — pass them explicitly so the call site stays checkable"
            )
            continue

        failure = _bind_failure(target, len(positional), [str(name) for name in keywords])
        if failure:
            offenders.append(_why(path, node.lineno, lines, f"{origin} {failure}"))
    return offenders


def _contract_offenders_in_tree(root: Path) -> list[str]:
    """Every contract break in one agent's shipped script tree, both mirrors."""
    offenders: list[str] = []
    scripts = root / "scripts"
    for path in _scanned_files(scripts):
        if path.suffix == ".py":
            offenders.extend(_contract_offenders(path, scripts))
    return offenders


# A pi wrapper must be exactly this and nothing else: locate the tree, exec
# `_run.sh` with the handler name, forward the arguments. Anything else there is
# logic that runs on every pi event, before the handler's own guard is reached.
PI_WRAPPER_TEMPLATE = re.compile(
    r"\A#!/usr/bin/env bash\n"
    r"set -euo pipefail\n"
    r'SCRIPT_DIR="\$\(cd "\$\(dirname "\$0"\)" && pwd\)"\n'
    r'exec "\$SCRIPT_DIR/\.\./_run\.sh" (on_[a-z_]+) "\$@"\n\Z'
)


def _main_tail(path: Path) -> list[ast.stmt]:
    """The body of a script's `if __name__ == "__main__":` block."""
    for node in ast.parse(path.read_text()).body:
        if (
            isinstance(node, ast.If)
            and getattr(getattr(node.test, "left", None), "id", "") == "__name__"
        ):
            return node.body
    return []


def _routes_through_the_guard(main_tail: list[ast.stmt]) -> bool:
    """Whether a `__main__` tail calls the shipped fail-loud guard."""
    return any(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", "") == "guard_hook_main"
        for node in main_tail
    )


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


def _written_tree(
    tmp_path: Path, script: str, siblings: dict[str, str] | None = None
) -> tuple[Path, Path]:
    """A synthetic plugin tree: one hook script plus the siblings it names."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name, body in (siblings or {}).items():
        (scripts / name).write_text(body)
    path = scripts / "on_stop.py"
    path.write_text(script)
    return path, scripts


def _display(path: Path) -> str:
    return os.path.relpath(path, REPO_ROOT)


CONTRACT_CASES: dict[str, tuple[str, dict[str, str], str]] = {
    "package-symbol-renamed": (
        "from stashai.plugin.hooks import guard_hook_mainn\n",
        {},
        "`stashai.plugin.hooks` does not provide guard_hook_mainn in the installed package",
    ),
    "package-module-moved": (
        "from stashai.plugin.hooks_v2 import guard_hook_main\n",
        {},
        "`stashai.plugin.hooks_v2` does not provide guard_hook_main in the installed package",
    ),
    "package-module-chain-missing": (
        "import stashai.plugin.hooks_v2\nstashai.plugin.hooks_v2.guard_hook_main(DATA_DIR, main)\n",
        {},
        "`stashai.plugin.hooks_v2` is not importable in the installed package",
    ),
    "package-call-unknown-keyword": (
        "from stashai.plugin.hooks import guard_hook_main\n"
        'guard_hook_main(DATA_DIR, main, host_exits_zero=True, event="on_stop")\n',
        {},
        "stashai.plugin.hooks.guard_hook_main got an unexpected keyword argument",
    ),
    "package-call-missing-argument": (
        "from stashai.plugin.hooks import guard_hook_main\nguard_hook_main(DATA_DIR)\n",
        {},
        "stashai.plugin.hooks.guard_hook_main missing a required argument: 'hook_main'",
    ),
    "package-attribute-moved": (
        "from stashai.plugin import hooks\nhooks.stream_tool_usex()\n",
        {},
        "stashai.plugin.hooks.stream_tool_usex is not in the installed package",
    ),
    "third-party-dependency": (
        "import requests\n",
        {},
        "`requests` is neither the stdlib",
    ),
    "sibling-symbol-renamed": (
        "from config import read_stdin\n",
        {"config.py": "def get_stdin_data():\n    return {}\n"},
        "config.read_stdin is not defined by the sibling module",
    ),
    "sibling-call-missing-argument": (
        "from config import two\ntwo(1)\n",
        {"config.py": "def two(a, b):\n    return a + b\n"},
        "config.two takes at least 2 positional arguments, got 1",
    ),
    "sibling-call-unknown-keyword": (
        "from config import two\ntwo(1, b=2, c=3)\n",
        {"config.py": "def two(a, b):\n    return a + b\n"},
        "config.two got an unexpected keyword argument: ['c']",
    ),
    "unpacked-arguments-cannot-be-checked": (
        "from config import two\ntwo(**payload)\n",
        {"config.py": "def two(a, b):\n    return a + b\n"},
        "config.two is called with unpacked arguments, which this guard cannot bind",
    ),
    "sibling-module-absent": (
        "from adapt import adapt_event\n",
        {},
        "`adapt.py` is not in this tree",
    ),
}


@pytest.mark.parametrize("case", sorted(CONTRACT_CASES))
def test_the_contract_guard_names_offending_cases(tmp_path: Path, case: str) -> None:
    """Each drift class that used to reach a machine is named by the guard."""
    script, siblings, expected = CONTRACT_CASES[case]
    path, scripts = _written_tree(tmp_path, script, _siblings_for(case, siblings))
    offenders = _contract_offenders(path, scripts)
    assert offenders, f"{case}: the guard found nothing to report"
    assert offenders[0].startswith(f"{_display(path)}:"), offenders
    assert expected in offenders[0], offenders


def _siblings_for(case: str, siblings: dict[str, str]) -> dict[str, str]:
    """The `config.py` every case's tree needs to exist when it is not the subject."""
    return {"config.py": "def get_stdin_data():\n    return {}\n", **siblings}


CONTRACT_CONTROLS: dict[str, tuple[str, dict[str, str]]] = {
    "guard-call-as-shipped": (
        "from stashai.plugin.hooks import guard_hook_main\n"
        "def main():\n    pass\n"
        "guard_hook_main(None, main, host_exits_zero=True)\n",
        {},
    ),
    "package-attribute-call": (
        "from stashai.plugin import agent_config\nagent_config.get_stdin_data()\n",
        {},
    ),
    "package-module-chain-import": (
        "import stashai.plugin.hooks\n"
        "stashai.plugin.hooks.guard_hook_main(None, main, host_exits_zero=True)\n",
        {},
    ),
    "sibling-call-with-defaults": (
        "from config import two\ntwo(1, flag=True)\n",
        {"config.py": "def two(a, b=1, *, flag=False):\n    return a + b\n"},
    ),
    "stdlib-only": ("import json\nimport os.path\nprint(json.dumps({}))\n", {}),
}


@pytest.mark.parametrize("control", sorted(CONTRACT_CONTROLS))
def test_the_contract_guard_passes_scripts_that_use_the_surface_correctly(
    tmp_path: Path, control: str
) -> None:
    """The guard is only useful if correct shipped-style code is silent."""
    script, siblings = CONTRACT_CONTROLS[control]
    path, scripts = _written_tree(tmp_path, script, siblings)
    assert _contract_offenders(path, scripts) == []


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("tree", [SOURCE_TREE, ASSET_TREE])
def test_shipped_hook_scripts_resolve_against_the_installed_package(agent: str, tree: str) -> None:
    """Every symbol and call signature a shipped hook uses exists as written.

    The runtime harness executes a script only along its fixture's path, so a
    symbol used on a rarer branch — or a signature that moved — stayed invisible
    while pi's handlers died on import and lost their recording silently.
    """
    assert _contract_offenders_in_tree(_tree_root(agent, tree)) == []


@pytest.mark.parametrize("tree", [SOURCE_TREE, ASSET_TREE])
def test_pi_wrappers_exec_run_sh_and_nothing_else(tree: str) -> None:
    """A pi wrapper is four lines: find the tree, exec `_run.sh`, forward `$@`.

    Wrapper code runs on every pi event before any handler-level guard exists and
    nothing reports its failure, so a wrapper that grows logic is a silent
    behaviour change on the user's machine.
    """
    scripts = _tree_root("pi", tree) / "scripts"
    handlers = {path.stem for path in scripts.glob("on_*.py")}
    offenders: list[str] = []
    reached: set[str] = set()

    for wrapper in sorted(path for path in (scripts / "hooks").iterdir() if path.is_file()):
        body = wrapper.read_text()
        shape = PI_WRAPPER_TEMPLATE.match(body)
        if shape is None:
            offenders.append(f"{_display(wrapper)} is not the exec-`_run.sh` template:\n{body}")
            continue
        handler = shape.group(1)
        reached.add(handler)
        if handler not in handlers:
            offenders.append(
                f"{_display(wrapper)} execs `{handler}`, which this tree does not ship"
            )

    assert offenders == []
    assert reached == handlers, f"shipped handlers no wrapper reaches: {handlers - reached}"


@pytest.mark.parametrize("tree", [SOURCE_TREE, ASSET_TREE])
def test_pi_handlers_exit_through_the_fail_loud_guard(tree: str) -> None:
    """pi starts the handler itself, so the handler owns its own failure report.

    The dispatched agents get this from `stash hook run`; pi has no dispatcher
    row, so without this call a fatal crash is invisible to `stash status`.
    """
    scripts = _tree_root("pi", tree) / "scripts"
    unrouted = [
        _display(path)
        for path in sorted(scripts.glob("on_*.py"))
        if not _routes_through_the_guard(_main_tail(path))
    ]
    assert unrouted == []
