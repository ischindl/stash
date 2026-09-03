"""Tests for the CLI environment-integrity guard in ``cli/tests/conftest.py``.

The guard exists because a poisoned interpreter makes correct product code look
broken: with the host interpreter's ``typer`` (which vendors a private click)
the same suite reports ~23 failures in ``test_usage_hints.py`` and
``test_exit_boundary.py``, and an operator burns a card "fixing" code that was
never wrong (STAS-178). So the guard must abort *collection*, loudly, before a
single test runs.

Each test poisons a **child** interpreter so the interpreter running this suite
is never mutated, and targets one leaf test file — never ``cli/tests`` as a
whole, which would nest pytest inside pytest.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
CONFTEST = Path(__file__).with_name("conftest.py")

# The remedy, verbatim from CLAUDE.md ("Developing the stash CLI"). The guard
# prints it and these tests assert it, so a reworded remedy is a red test.
REMEDY = "uv venv -p 3.12 && uv pip install -e ."

# A one-test leaf file: enough to prove nothing was collected, cheap to run.
CHILD_TARGET = "cli/tests/test_root_help.py"

# Installed by the child interpreter at startup (it sits on PYTHONPATH) to give
# ``stashai`` a spec that resolves outside this checkout — the precedence a real
# ``pip install -e .`` editable install uses, which a plain sys.path entry
# cannot imitate because the checkout root already outranks it.
SITECUSTOMIZE = """\
import importlib.util
import os
import sys

_other = os.path.join(os.environ["PYTHONPATH"], "other-checkout", "stashai")
_spec = importlib.util.spec_from_file_location(
    "stashai", os.path.join(_other, "__init__.py"), submodule_search_locations=[_other]
)


class _OtherCheckoutFinder:
    def find_spec(self, name, path=None, target=None):
        return _spec if name == "stashai" else None


sys.meta_path.insert(0, _OtherCheckoutFinder())
"""


def _run_child(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the leaf CLI test file in a child interpreter with ``extra_env``."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", CHILD_TARGET, "--no-cov", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env={**os.environ, **extra_env},
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_abandoned_before_collecting(result: subprocess.CompletedProcess[str]) -> str:
    """The guard must stop the run, not add a red test to a red suite."""
    out = result.stdout + result.stderr
    assert result.returncode == 4, f"expected a usage-error abort, got {result.returncode}:\n{out}"
    assert REMEDY in out, f"guard must print the remedy verbatim:\n{out}"
    assert " passed" not in out and " failed" not in out, f"guard must run zero tests:\n{out}"
    return out


def test_guard_refuses_wrong_typer_version(tmp_path: Path) -> None:
    """A ``typer`` that is not the pinned one cannot produce a meaningful verdict.

    ``typer >= 0.25`` vendors ``typer._click``, whose ``MissingParameter`` is not a
    ``click.exceptions.UsageError``, so it escapes ``cli.main.main()``'s boundary
    catch — the failures it yields describe the interpreter, not the product.
    """
    (tmp_path / "typer-9.9.9.dist-info").mkdir()
    (tmp_path / "typer-9.9.9.dist-info" / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: typer\nVersion: 9.9.9\n", encoding="utf-8"
    )

    out = _assert_abandoned_before_collecting(_run_child({"PYTHONPATH": str(tmp_path)}))

    assert "9.9.9" in out, f"guard must name the version it found:\n{out}"


def test_guard_refuses_package_resolved_outside_checkout(tmp_path: Path) -> None:
    """A stale editable install aimed at another checkout must not be testable.

    The hazard in the field was a ``stashai`` editable install left pointing at a
    deleted worktree: the code under test and the code being validated were
    different trees, so the suite's verdict described neither.
    """
    other_package = tmp_path / "other-checkout" / "stashai"
    other_package.mkdir(parents=True)
    (other_package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")

    out = _assert_abandoned_before_collecting(_run_child({"PYTHONPATH": str(tmp_path)}))

    assert "other-checkout" in out, f"guard must name the path it resolved:\n{out}"


def test_guard_reads_the_pin_instead_of_hardcoding_it() -> None:
    """The guard compares against pyproject.toml, so the pin has one home.

    A literal version inside the guard would silently drift the moment the pin
    is bumped: the failure mode would be the guard itself lying.
    """
    assert not re.search(r"\d+\.\d+\.\d+", CONFTEST.read_text(encoding="utf-8")), (
        "cli/tests/conftest.py must read the typer pin from pyproject.toml, never inline it"
    )

    from cli.tests import conftest

    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    typer = [Requirement(dep) for dep in dependencies if Requirement(dep).name.lower() == "typer"]
    assert len(typer) == 1, f"expected exactly one typer dependency, got {len(typer)}"

    (exact_pin,) = typer[0].specifier
    assert exact_pin.operator == "==", f"typer must stay an exact pin, got {exact_pin}"
    assert conftest.pinned_typer_version() == exact_pin.version
