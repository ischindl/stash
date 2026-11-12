"""Refuse to run the CLI suite in an interpreter that is not this checkout's.

A poisoned interpreter makes correct product code look broken. Past its pin,
``typer`` vendors a private click, so ``MissingParameter`` stops being a
``click.exceptions.UsageError``, escapes the boundary catch in ``cli.main``, and
prints raw tracebacks — two dozen red tests describing the interpreter rather
than the product (STAS-178 spent a card cycle "fixing" exactly that). A stale
editable install left pointing at another, usually deleted, checkout validates
one tree while running a different one.

Both are refused here, before a single test is collected, and the message names
the setup command that repairs them. A correct environment never notices
this file exists.
"""

import contextlib
import importlib.metadata
import importlib.util
import json
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
from packaging.requirements import Requirement

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]

# Verbatim from CLAUDE.md ("Developing the stash CLI"); test_env_guard.py asserts
# this exact string, so rewording the recipe is a red test rather than drift.
REMEDY = "uv venv -p 3.12 && uv pip install -e ."

CHECKED_PACKAGES = ("cli", "stashai")


def _refuse(problems: list[str]) -> None:
    raise RuntimeError(
        "The CLI tests must run in this checkout's own Python environment.\n"
        "  " + "\n  ".join(problems) + f"\n\n  Remedy, once per checkout:\n    {REMEDY}"
    )


def pinned_typer_version() -> str:
    """The exact ``typer`` pin, read from ``pyproject.toml`` — never restated here.

    Parsing mirrors ``test_mcp_cap_guard.py``, comparing the requirement's own
    name so ``typer-slim`` can never satisfy the lookup. A range instead of an
    exact pin is refused rather than evaluated: the guard would otherwise bless
    a version nothing has validated.
    """
    text = (CHECKOUT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = tomllib.loads(text)["project"]["dependencies"]
    typer = [Requirement(dep) for dep in dependencies if Requirement(dep).name.lower() == "typer"]
    if len(typer) != 1:
        _refuse([f"pyproject.toml declares {len(typer)} typer dependencies"])
    clauses = list(typer[0].specifier)
    if len(clauses) != 1 or clauses[0].operator != "==":
        _refuse([f"typer must stay an exact pin, found `{typer[0]}`"])
    return clauses[0].version


def _typer_problem() -> str | None:
    """Why this interpreter's ``typer`` cannot give a trustworthy verdict."""
    pinned = pinned_typer_version()
    try:
        installed = importlib.metadata.version("typer")
    except importlib.metadata.PackageNotFoundError:
        return f"typer is not installed here, but pinned to {pinned}"
    if installed != pinned:
        return f"typer is {installed}, but pinned to {pinned}"
    return None


def _package_problem(name: str) -> str | None:
    """Why ``name`` is not the code in this working tree."""
    spec = importlib.util.find_spec(name)
    origin = spec.origin if spec is not None else None
    if not origin:
        return f"{name} resolves to no file, so this checkout is not installed"
    resolved = Path(origin).resolve()
    if CHECKOUT_ROOT not in resolved.parents:
        return f"{name} resolves outside this checkout:\n    {resolved}"
    return None


def _assert_test_environment() -> None:
    problems = [_typer_problem()] + [_package_problem(name) for name in CHECKED_PACKAGES]
    blocking = [problem for problem in problems if problem]
    if blocking:
        _refuse(blocking)


_assert_test_environment()


@pytest.fixture(autouse=True)
def _plugin_data_dirs_stay_inside_the_test(monkeypatch, tmp_path):
    """Point every registered agent's data dir at this test's own ``tmp_path``.

    ``hook run`` records what it saw into ``<agent data dir>/upload_status.json``
    — the file ``stash status`` reads — and it records the invocations it refuses
    on purpose, because a wrapper calling an event this release dropped is drift
    worth seeing. So a test that merely exercises a reject path wrote a failing
    row into whoever's home directory ran the suite: the machine this feature was
    built on ended up with 44 failures attributed to a hook that never ran
    (STAS-267). A test that asserts on a data dir sets it to its own path.

    Scope limit, stated so the name does not overpromise: this covers the
    dispatcher's ``PLUGIN_DATA_DIRS`` map only. It cannot redirect the second
    recorder a dispatch has — the shipped handler's own ``__main__`` tail records
    into the directory its ``config.py`` bound from ``STASH_<AGENT>_DATA`` — so a
    test that dispatches a real shipped handler needs ``isolated_hook_dispatch``
    below, not this fixture alone (STAS-417).
    """
    import cli.main as cli_main

    monkeypatch.setattr(
        cli_main,
        "PLUGIN_DATA_DIRS",
        {agent: tmp_path / agent for agent in cli_main.PLUGIN_DATA_DIRS},
    )


# Each plugin's config.py reads its own data dir from this env var at import time.
_AGENT_DATA_DIR_ENV = {
    "claude": "STASH_CLAUDE_DATA",
    "codex": "STASH_CODEX_DATA",
    "cursor": "STASH_CURSOR_DATA",
    "gemini": "STASH_GEMINI_DATA",
    "hermes": "STASH_HERMES_DATA",
    "openclaw": "STASH_OPENCLAW_DATA",
    "opencode": "STASH_OPENCODE_DATA",
    "pi": "STASH_PI_DATA",
}


def _remap_plugin_data_dirs(
    monkeypatch, tmp_path: Path, existing: tuple[str, ...]
) -> dict[str, Path]:
    """Point every registered plugin data dir under `tmp_path`, creating only the
    named agents' directories. `PLUGIN_DATA_DIRS` is built from the real home at
    import time, and a hook must never write outside the test tree."""
    import cli.main

    remapped: dict[str, Path] = {}
    for agent in cli.main.PLUGIN_DATA_DIRS:
        data_dir = tmp_path / ".stash" / "plugins" / agent
        if agent in existing:
            data_dir.mkdir(parents=True)
        remapped[agent] = data_dir
    monkeypatch.setattr(cli.main, "PLUGIN_DATA_DIRS", remapped)
    return remapped


def _fresh_hook_modules() -> None:
    """Drop the cached flat hook modules.

    The shipped hook scripts import flat siblings (``adapt``, ``config``), and
    ``config.py`` fixes its data dir from the environment at import time. Leaving
    a cached copy behind makes the next dispatch bind whichever agent imported
    first, on whichever machine ran it.
    """
    for mod in ("adapt", "config"):
        sys.modules.pop(mod, None)


@contextlib.contextmanager
def isolated_hook_dispatch(
    monkeypatch, tmp_path: Path, agent: str, *, configured: bool
) -> Iterator[dict[str, Path]]:
    """Install every binding one in-process ``stash hook run <agent> <event>`` reads.

    A dispatch has two recorders, both load-bearing: ``cli.main.hook_run`` guards
    the dispatch into ``PLUGIN_DATA_DIRS[agent]``, and because that dispatch runpy's
    the shipped handler with ``run_name="__main__``, the handler's own tail guards
    again into the directory its ``config.py`` bound from ``STASH_<AGENT>_DATA``.
    Redirecting only the first is what let a test write a fabricated failure into a
    real home, where ``stash status`` then blamed it on the operator's agent install
    (STAS-417). So this redirects both sinks, fakes ``HOME``/``Path.home``, writes a
    config when the test needs ``is_configured()`` to answer instead of inheriting
    the developer's, repoints both import-time ``_CONFIG_FILE`` bindings, and drops
    the flat hook modules on entry and exit so this environment is the one that
    binds.

    The two sinks are deliberately separate, both existing. ``guard_hook_main``
    records only into a directory that is already there, so one record per entry
    point is what a caller can assert; collapsing them into one directory would
    double a count another test pins to one.

    Yields ``{"dispatcher": ..., "handler": ..., "home": ...}``.
    """
    import cli.main
    import stashai.plugin.hooks as plugin_hooks
    import stashai.plugin.scope as plugin_scope

    dispatcher_dir = tmp_path / "dispatcher-sink" / agent
    handler_dir = tmp_path / "handler-sink" / agent
    dispatcher_dir.mkdir(parents=True)
    handler_dir.mkdir(parents=True)

    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=())
    data_dirs[agent] = dispatcher_dir
    monkeypatch.setattr(cli.main, "PLUGIN_DATA_DIRS", data_dirs)

    home = tmp_path / "fake-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    config_file = home / ".stash" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    if configured:
        # ``.invalid`` is the RFC 2606 TLD that never resolves, so a hook that got
        # further than the test intends can never reach the real service. (A
        # dotted-quad literal here would also read as a hardcoded version to
        # test_env_guard's own text lock on this file.)
        config_file.write_text(
            json.dumps(
                {
                    "api_key": "test-key",
                    "username": "alice-laptop",
                    "base_url": "http://stash-tests.invalid",
                }
            )
        )
    for binding in (plugin_hooks, plugin_scope):
        # Both bind the config path to the real home at import time, so patching
        # ``Path.home`` does not reach them.
        monkeypatch.setattr(binding, "_CONFIG_FILE", config_file)

    env_var = _AGENT_DATA_DIR_ENV[agent]
    monkeypatch.setenv(env_var, str(handler_dir))

    _fresh_hook_modules()
    try:
        yield {"dispatcher": dispatcher_dir, "handler": handler_dir, "home": home}
    finally:
        _fresh_hook_modules()
