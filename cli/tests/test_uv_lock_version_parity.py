"""Lock the release version ``pyproject.toml`` declares to the one ``uv.lock`` records.

``[project].version`` in ``pyproject.toml`` is the release's identity: setuptools stamps it
into the distribution, ``importlib.metadata`` serves it to the running CLI, and the PyPI
publish job triggers on it. ``uv.lock`` carries that same identity in the ``[[package]]``
block whose source is the checkout itself, so a reproducible install resolves the release it
means. When a bump lands in ``pyproject.toml`` without re-locking, one commit contradicts
itself: the lock pins the previous release of the very package it is locking, and the repair
has to sit uncommitted in somebody's working tree — where the next ``git add -A`` sweeps it
into unrelated work and ``ref == tree`` stops being checkable. STAS-252 is the prevention
side; the fix is always the same single command.

The guard reads both files as text under ``REPO_ROOT`` and derives the package name from
``pyproject.toml`` rather than restating it, so it covers whatever this project is called at
whatever version it is currently at. It never imports the CLI: a parity check that needed the
package it checks could not report on a tree where that package is the thing that is broken.

Like ``plugins/tests/test_hook_scripts_no_cli_imports.py``, the negative cases below are
exercised on ``tmp_path`` fixtures with invented versions, because a guard that cannot fail
is not a guard.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"

PACKAGE_HEADER = "[[package]]"
NAME_LINE = re.compile(r'^name = "(?P<name>[^"]+)"$', re.MULTILINE)
VERSION_LINE = re.compile(r'^version = "(?P<version>[^"]+)"$', re.MULTILINE)

REPAIR = "run `uv lock` in the repo root and commit the regenerated uv.lock with the bump"

# A lock body with one unrelated dependency already in it, so the guard has to select the
# project's own block out of a lock that looks like a real one.
LOCK_HEADER = "version = 1\nrevision = 3\n\n"
UNRELATED_BLOCK = f'{PACKAGE_HEADER}\nname = "an-dependency"\nversion = "1.2.3"\n\n'
EDITABLE_SOURCE = 'source = { editable = "." }\n'


def _project_name_and_version(pyproject: Path) -> tuple[str, str]:
    """``[project].name`` and ``[project].version``, read from the file that declares them."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{pyproject} has no [project] table, so it declares no package version")
    absent = [key for key in ("name", "version") if not isinstance(project.get(key), str)]
    if absent:
        raise ValueError(
            f"{pyproject} declares no [project].{' or [project].'.join(absent)}; "
            "the guard has no version to compare the lock against"
        )
    return project["name"], project["version"]


def _package_blocks(lock_text: str) -> list[str]:
    """Every ``[[package]]`` block in a uv.lock, in file order.

    Splitting on the header is exact here: uv.lock declares no table nested deeper than
    ``[[package]]``, so every chunk after the preamble is one package.
    """
    return lock_text.split(PACKAGE_HEADER)[1:]


def _block_name(block: str) -> str | None:
    match = NAME_LINE.search(block)
    return match.group("name") if match else None


def _locked_version(lock_text: str, package_name: str) -> str:
    """The version uv.lock records for ``package_name``: one block, one version line."""
    named = [block for block in _package_blocks(lock_text) if _block_name(block) == package_name]
    if not named:
        raise ValueError(
            f"uv.lock records no {PACKAGE_HEADER} block named {package_name!r}, so the lock "
            f"does not cover this project at all — {REPAIR}"
        )
    if len(named) > 1:
        raise ValueError(
            f"uv.lock records {len(named)} {PACKAGE_HEADER} blocks named {package_name!r}; "
            "the guarded version has no single meaning"
        )
    versions = VERSION_LINE.findall(named[0])
    if len(versions) != 1:
        raise ValueError(
            f"uv.lock's {package_name!r} block records {len(versions)} version lines "
            f"{versions}, so the guarded version is ambiguous — {REPAIR}"
        )
    return versions[0]


def _assert_version_parity(pyproject: Path, lock: Path) -> None:
    """Fail loud, naming both versions, when the two sources of one identity disagree."""
    package_name, declared = _project_name_and_version(pyproject)
    locked = _locked_version(lock.read_text(encoding="utf-8"), package_name)
    assert declared == locked, (
        f"{package_name}: pyproject.toml declares version {declared} but uv.lock records "
        f"{locked}. The bump was never re-locked, so a reproducible install resolves the "
        f"previous release of the package it is locking. Repair: {REPAIR}."
    )


def _write_pair(root: Path, *, name: str, declared: str | None, locked: str | None) -> None:
    """A pyproject/uv.lock pair on ``root``; ``None`` leaves that side's version out entirely."""
    pyproject = f'[project]\nname = "{name}"\n'
    if declared is not None:
        pyproject += f'version = "{declared}"\n'
    (root / "pyproject.toml").write_text(pyproject)

    lock = f"{LOCK_HEADER}{UNRELATED_BLOCK}"
    if locked is not None:
        lock += f'{PACKAGE_HEADER}\nname = "{name}"\nversion = "{locked}"\n{EDITABLE_SOURCE}'
    (root / "uv.lock").write_text(lock)


def test_uv_lock_records_the_pyproject_version() -> None:
    """The release this checkout declares is the release its lock resolves."""
    package_name, declared = _project_name_and_version(PYPROJECT)
    assert declared, f"{PYPROJECT.name} declares an empty [project].version"
    # Parsed on its own as well, so a green run proves the guard read two real values
    # rather than two absences that happened to agree.
    assert _locked_version(UV_LOCK.read_text(encoding="utf-8"), package_name)
    _assert_version_parity(PYPROJECT, UV_LOCK)


def test_guard_fails_loud_on_mismatch(tmp_path: Path) -> None:
    """A bump without a re-lock is named by package and by both of its version values."""
    _write_pair(tmp_path, name="stashai", declared="9.9.9", locked="8.8.8")

    with pytest.raises(AssertionError) as caught:
        _assert_version_parity(tmp_path / "pyproject.toml", tmp_path / "uv.lock")

    message = str(caught.value)
    assert "stashai" in message
    assert "9.9.9" in message
    assert "8.8.8" in message


def test_guard_passes_on_matching_pair(tmp_path: Path) -> None:
    """The guard is not a constant failure: an agreeing pair stays green."""
    _write_pair(tmp_path, name="stashai", declared="9.9.9", locked="9.9.9")

    _assert_version_parity(tmp_path / "pyproject.toml", tmp_path / "uv.lock")


# A version source that cannot answer must be a red test, never a silently skipped check.
UNANSWERABLE_CASES = [
    pytest.param("9.9.9", None, "stashai", id="lock-block-absent"),
    pytest.param(None, "8.8.8", "[project].version", id="pyproject-version-absent"),
]


@pytest.mark.parametrize(("declared", "locked", "expected"), UNANSWERABLE_CASES)
def test_guard_fails_when_a_version_source_says_nothing(
    tmp_path: Path, declared: str | None, locked: str | None, expected: str
) -> None:
    """No package block in the lock, and no version in pyproject, both refuse the guard."""
    _write_pair(tmp_path, name="stashai", declared=declared, locked=locked)

    with pytest.raises(ValueError) as caught:
        _assert_version_parity(tmp_path / "pyproject.toml", tmp_path / "uv.lock")

    assert expected in str(caught.value)


def test_guard_never_hardcodes_the_current_version() -> None:
    """The guard compares two sources; it must not restate either of them.

    A literal of the live release version anywhere in this file would keep a stale pair
    looking green only until the next bump ships, so the check reads its own source.
    """
    _, declared = _project_name_and_version(PYPROJECT)
    assert declared not in Path(__file__).read_text(encoding="utf-8"), (
        f"this guard names the live release version {declared} as a literal; read it from "
        "pyproject.toml instead"
    )
