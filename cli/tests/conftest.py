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

import importlib.metadata
import importlib.util
import tomllib
from pathlib import Path

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
