"""Cap-regression guard for the mcp dependency (STAS-152 addendum, STAS-160 class).

A release published with uncapped ``mcp>=1.23.0`` resolves to mcp 2.x, which
removed ``mcp.server.fastmcp`` and breaks ``import cli.mcp_server`` in shipped
wheels — verified live when a dev venv built from the uncapped pyproject got
mcp 2.0.0 and every MCP-server import raised ModuleNotFoundError.

The constraint is read from pyproject.toml, the single source of truth: this
test goes red if the ``<2`` cap is removed or widened, making the silent
breakage class CI-visible instead of wheel-runtime-visible.
"""

from pathlib import Path

from packaging.requirements import Requirement

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _mcp_requirement() -> Requirement:
    """The single declared mcp dependency, parsed from pyproject.toml."""
    import tomllib

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    matches = [
        Requirement(dep)
        for dep in data["project"]["dependencies"]
        if Requirement(dep).name.lower() == "mcp"
    ]
    assert len(matches) == 1, (
        f"expected exactly one mcp dependency in pyproject.toml, got {len(matches)}"
    )
    return matches[0]


def test_mcp_dependency_excludes_2x():
    """The mcp 2.x line must be unresolvable: it lacks mcp.server.fastmcp.

    Removing the cap (``mcp>=1.23.0``) or widening it (``mcp<3``) makes this
    red, which is exactly when a published wheel would start breaking.
    """
    mcp = _mcp_requirement()
    assert not mcp.specifier.contains("2.0.0"), (
        f"mcp dependency {mcp.specifier} admits mcp 2.x, which removed "
        "mcp.server.fastmcp and breaks cli.mcp_server"
    )
