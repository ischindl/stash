"""Make a missing agent payload fail in CI instead of in somebody's install.

``[tool.setuptools.package-data]`` is the only thing carrying the payload trees under
``stashai/plugin/assets/`` into the wheel and sdist a user downloads, and
``[tool.setuptools.packages.find] include`` is the only thing making that package be
discovered at all. Before this file nothing in the suite read either declaration, so a
pattern narrowed to ``*.py``, or a dropped ``stashai*`` include entry, would have shipped a
CLI whose install dies later in ``cli.main._assets_dir`` raising ``FileNotFoundError``.
STAS-478 is the measured case: the published ``stashai`` wheel carried all seven other agent
trees and none of pi's, while the card tracking pi's delivery was closed as merged.

Its own limit, stated so a green run is not overread: this reads the DECLARATION. It cannot
see build-backend behaviour, so a setuptools that stopped honouring ``package-data`` would
keep this green while the artifact regressed. The artifact census in
``docs/trunk-publish-and-deploy.md`` is the backstop, because it counts paths inside the
built and downloaded files themselves.

Like ``test_uv_lock_version_parity.py``, every guarded value is read from
``pyproject.toml`` rather than restated here, and the negative cases run on ``tmp_path``
trees, because a guard that cannot fail is not a guard.
"""

from __future__ import annotations

import fnmatch
import glob
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

from cli.main import _SUPPORTED_AGENTS, _assets_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The assets root comes from the product's own resolver, so this guard cannot drift onto a
# second path expression that the runtime never uses.
ASSETS_ROOT = _assets_dir(_SUPPORTED_AGENTS[0]).parent

# The three files a ``*.py``-shaped pattern silently drops from pi's tree: the TypeScript
# extension pi actually loads, and the two guidance documents it reads.
PI_SCRIPTS_ONLY_DROPS = ("extensions/stash.ts", "AGENTS.md", "README.md")


def _declaration(pyproject: Path) -> tuple[str, str, list[str], list[str]]:
    """``(name, version, package-data patterns, packages.find include)``, all read from the file.

    Restating any of them would let the guard bless a declaration that no longer exists, and
    a declaration that says nothing is refused rather than skipped — an empty answer is how
    a packaging guard starts passing on a tree that ships no payload at all.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str):
        raise ValueError(
            f"{pyproject.name} declares no [project].name, so there is no package to check"
        )
    if not isinstance(project.get("version"), str):
        raise ValueError(
            f"{pyproject.name} declares no [project].version, so there is no release to check"
        )

    setuptools = data.get("tool", {}).get("setuptools", {})
    name = project["name"]
    patterns = setuptools.get("package-data", {}).get(name)
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(
            f"[tool.setuptools.package-data] declares no patterns for {name!r}, so none of its "
            "plugin assets would be packaged"
        )

    include = setuptools.get("packages", {}).get("find", {}).get("include")
    if not isinstance(include, list) or not include:
        raise ValueError(
            f"[tool.setuptools.packages.find] declares no include list, so {name!r} would not be "
            "discovered as a package and its data would have no package to attach to"
        )
    return name, project["version"], patterns, include


def _assert_package_discovered(name: str, include: Iterable[str]) -> None:
    patterns = list(include)
    if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
        raise AssertionError(
            f"{name!r} matches no [tool.setuptools.packages.find] include pattern {patterns}, so "
            "setuptools discovers no package here and none of its package-data would be collected"
        )


def _matched_files(package_dir: Path, patterns: list[str]) -> set[str]:
    """Every file the declared patterns actually reach, named relative to ``package_dir``."""
    return {
        hit
        for pattern in patterns
        for hit in glob.glob(pattern, root_dir=package_dir, recursive=True)
        if (package_dir / hit).is_file()
    }


def _assert_packaging_covers(pyproject: Path, agent_trees: Mapping[str, Path]) -> None:
    """Fail loud, naming every payload file the declaration would leave out of the distribution.

    The comparison is one-directional by construction: the matched side is produced by
    globbing ``package_dir`` itself, so a pattern can never match a file the tree does not
    have. What it can and does catch is the failure that matters — a file the tree has and
    the pattern does not reach.
    """
    name, _version, patterns, include = _declaration(pyproject)
    _assert_package_discovered(name, include)
    package_dir = pyproject.parent / name
    reached = _matched_files(package_dir, patterns)

    for agent, tree in agent_trees.items():
        shipped = {str(path.relative_to(package_dir)) for path in tree.rglob("*") if path.is_file()}
        unclaimed = sorted(shipped - reached)
        assert not unclaimed, (
            f"{name}: {len(unclaimed)} file(s) of {agent}'s payload tree are reachable by no "
            f"[tool.setuptools.package-data] pattern {patterns}: {unclaimed}. They would be absent "
            f"from the wheel and sdist, and `stash install --agents {agent}` would die in "
            "_assets_dir raising FileNotFoundError."
        )


def _write_pyproject(root: Path, *, patterns: list[str] | None, include: list[str] | None) -> Path:
    pyproject = root / "pyproject.toml"
    text = '[project]\nname = "stashai"\nversion = "0.0.0"\n\n'
    text += "[tool.setuptools.packages.find]\n"
    if include is not None:
        text += "include = [" + ", ".join(f'"{p}"' for p in include) + "]\n"
    text += "\n[tool.setuptools.package-data]\n"
    if patterns is not None:
        text += "stashai = [" + ", ".join(f'"{p}"' for p in patterns) + "]\n"
    pyproject.write_text(text, encoding="utf-8")
    return pyproject


def _write_payload_tree(root: Path, agent: str, files: Iterable[str]) -> Path:
    """``root/stashai/plugin/assets/<agent>/`` holding ``files``, the layout the runtime uses."""
    tree = root / "stashai" / "plugin" / "assets" / agent
    for relative in files:
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("payload\n", encoding="utf-8")
    return tree


def _pi_payload_files() -> list[str]:
    """pi's real payload filenames, read through the resolver that ships them."""
    tree = _assets_dir("pi")
    return sorted(str(path.relative_to(tree)) for path in tree.rglob("*") if path.is_file())


def test_every_supported_agent_payload_is_reachable() -> None:
    """Each agent in ``_SUPPORTED_AGENTS`` ships only files the declaration reaches.

    An agent added to ``_SUPPORTED_AGENTS`` whose assets the pattern does not cover reddens
    its own parameter — that is the whole point of binding the guard to that tuple.
    """
    for agent in _SUPPORTED_AGENTS:
        _assert_packaging_covers(PYPROJECT, {agent: _assets_dir(agent)})


def test_supported_agents_and_payload_trees_are_the_same_set() -> None:
    """The payload trees on disk are exactly the agents the CLI claims to support.

    ``_SUPPORTED_AGENTS`` is read as the source of truth, so this closes the other direction:
    a tree committed for an agent that tuple never lists would be packaged and installed by
    nobody, and a listed agent with no tree would fail at install time.
    """
    trees = {entry.name for entry in ASSETS_ROOT.iterdir() if entry.is_dir()}
    assert trees == set(_SUPPORTED_AGENTS), (
        f"payload trees {sorted(trees)} and _SUPPORTED_AGENTS {sorted(_SUPPORTED_AGENTS)} disagree; "
        f"only in trees: {sorted(trees - set(_SUPPORTED_AGENTS))}, "
        f"only in _SUPPORTED_AGENTS: {sorted(set(_SUPPORTED_AGENTS) - trees)}"
    )


def test_package_name_is_matched_by_an_include_pattern() -> None:
    """The live declaration discovers the package it attaches its data to."""
    name, version, _patterns, include = _declaration(PYPROJECT)
    assert version, f"{PYPROJECT.name} declares an empty [project].version"
    _assert_package_discovered(name, include)


def test_guard_fails_when_a_pattern_reaches_only_python(tmp_path: Path) -> None:
    """A ``*.py`` narrowing drops exactly pi's three non-Python payload files, and names them.

    This is the shape of defect STAS-478 shipped with: the handlers keep their ``.py`` and
    look packaged, while the extension and the guidance documents vanish from the artifact.
    """
    pyproject = _write_pyproject(tmp_path, patterns=["plugin/assets/**/*.py"], include=["stashai*"])
    tree = _write_payload_tree(tmp_path, "pi", _pi_payload_files())

    with pytest.raises(AssertionError) as caught:
        _assert_packaging_covers(pyproject, {"pi": tree})

    message = str(caught.value)
    for dropped in PI_SCRIPTS_ONLY_DROPS:
        assert dropped in message, f"the guard failed but did not name {dropped}: {message}"
    assert "on_stop.py" not in message, f"the guard blamed a file the pattern does reach: {message}"


def test_guard_fails_when_the_package_is_not_discovered(tmp_path: Path) -> None:
    """A dropped ``stashai*`` include is caught even though the data pattern is untouched."""
    pyproject = _write_pyproject(
        tmp_path, patterns=["plugin/assets/**"], include=["cli*", "stashvfs*"]
    )
    tree = _write_payload_tree(tmp_path, "pi", _pi_payload_files())

    with pytest.raises(AssertionError, match="stashai") as caught:
        _assert_packaging_covers(pyproject, {"pi": tree})

    assert "packages.find" in str(caught.value)


def test_guard_passes_on_a_matching_synthetic_tree(tmp_path: Path) -> None:
    """The guard is not a constant failure: a pattern that reaches everything stays green.

    The synthetic pattern is deliberately not the live one, so this green is not produced by
    restating the current declaration.
    """
    pyproject = _write_pyproject(tmp_path, patterns=["plugin/assets/**"], include=["stashai*"])
    tree = _write_payload_tree(tmp_path, "pi", _pi_payload_files())

    _assert_packaging_covers(pyproject, {"pi": tree})


UNANSWERABLE_PATTERNS = [
    pytest.param(None, ["stashai*"], "package-data", id="package-data-key-absent"),
    pytest.param(["plugin/assets/**"], None, "packages.find", id="include-list-absent"),
]


@pytest.mark.parametrize(("patterns", "include", "expected"), UNANSWERABLE_PATTERNS)
def test_guard_fails_when_a_declaration_says_nothing(
    tmp_path: Path, patterns: list[str] | None, include: list[str] | None, expected: str
) -> None:
    """Neither half of the contract may answer with an absence."""
    pyproject = _write_pyproject(tmp_path, patterns=patterns, include=include)
    tree = _write_payload_tree(tmp_path, "pi", _pi_payload_files())

    with pytest.raises(ValueError, match=expected):
        _assert_packaging_covers(pyproject, {"pi": tree})
