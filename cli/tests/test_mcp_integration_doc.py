"""Docs-vs-code guard for the MCP integration guide.

The original MCP guide rotted within weeks of shipping: it listed tools that had
already been removed (`stash_vfs_ls`, `get_stash_info`) and a tool count that no
longer matched the server. Nothing caught it because no test read the doc. This
file closes that loop: every tool name, CLI invocation, flag, and heading the
guide claims is checked against the shipped code, parsed with ``ast`` — no MCP
server, no database, no installed ``stash`` binary.

Style precedent: ``test_mcp_registry.py`` pins the tool surface with an explicit
list edited on purpose. Here the pins are structural (headings, obsolete
markers) and the tool roster is compared against ``ast``-derived truth in both
directions, so a tool renamed in code reds the guide and a tool invented in the
guide reds the suite. A failure here means edit the doc or the code in the same
commit — never weaken these assertions.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "stash-mcp-integration.md"
MCP_SERVER_PATH = REPO_ROOT / "cli" / "mcp_server.py"
CLI_MAIN_PATH = REPO_ROOT / "cli" / "main.py"

REQUIRED_H2_HEADINGS = (
    "What Stash exposes over MCP",
    "Install and sign in",
    "MCP client configuration",
    "Tool reference",
    "VFS path format",
    "Bring-your-own MCP servers",
    "Scope, auth, and troubleshooting",
)

# Case-sensitive. The first two name the superseded server-hosted MCP
# generation's route and module; the tool names were removed from the shipped
# surface; "SSE" was that generation's transport. None of them may reappear.
OBSOLETE_MARKERS = (
    "/api/v1/mcp",
    "mcp_service",
    "stash_vfs_ls",
    "stash_vfs_cat",
    "stash_memory_search",
    "stash_session_search",
    "get_stash_info",
    "SSE",
)

# Flags that legitimately appear in documented shell blocks without being a
# literal in cli/main.py, with the reason each is exempt.
DOCUMENTED_FLAG_ALLOWLIST: dict[str, str] = {
    # typer injects --help into every command; no literal declares it.
    "--help": "auto-added by typer to every command",
}

SHELL_BLOCK_RE = re.compile(
    r"^```(?:bash|sh|shell|console)[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE
)
TOOL_NAME_RE = re.compile(r"stash_[a-z][a-z_]*")
TOOL_COUNT_RE = re.compile(r"(\d+)\s+tools\b")
ROSTER_ROW_RE = re.compile(r"^\|\s*`(stash_[a-z][a-z_]*)`", re.MULTILINE)
FLAG_RE = re.compile(r"(--[a-z][a-z0-9-]*)")


def _doc() -> str:
    assert DOC_PATH.exists(), (
        f"missing documentation deliverable: {DOC_PATH.relative_to(REPO_ROOT)}"
    )
    return DOC_PATH.read_text(encoding="utf-8")


def _shipped_tools() -> set[str]:
    """Top-level ``def`` names in cli/mcp_server.py decorated with ``mcp.tool()``."""
    tree = ast.parse(MCP_SERVER_PATH.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                names.add(node.name)
    return names


def _registered_name(decorator: ast.Call, function_name: str) -> str:
    """The command name a ``@<var>.command(...)`` decorator declares.

    Uses the first positional string argument when given, otherwise the
    function name — typer's own resolution rule.
    """
    for arg in decorator.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return function_name


def _cli_registry() -> tuple[set[str], dict[str, set[str]]]:
    """(top-level command names, group name -> subcommand names) from cli/main.py.

    Mirrors how the app is assembled: ``@app.command(...)`` for top-level
    commands and ``app.add_typer(<var>, name=...)`` plus ``@<var>.command(...)``
    for groups such as ``tools`` and its ``add/list/remove/install`` subcommands.
    """
    tree = ast.parse(CLI_MAIN_PATH.read_text(encoding="utf-8"))

    group_var_to_name: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_typer" or not node.args:
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "app":
            continue
        var = node.args[0]
        if not isinstance(var, ast.Name):
            continue
        registered = var.id
        for kw in node.keywords:
            if (
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                registered = kw.value.value
        group_var_to_name[var.id] = registered

    commands: set[str] = set()
    groups: dict[str, set[str]] = {name: set() for name in group_var_to_name.values()}

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr != "command":
                continue
            owner = decorator.func.value
            if not isinstance(owner, ast.Name):
                continue
            if owner.id == "app":
                commands.add(_registered_name(decorator, node.name))
            elif owner.id in group_var_to_name:
                groups[group_var_to_name[owner.id]].add(_registered_name(decorator, node.name))

    return commands, groups


def _shell_blocks(doc: str) -> list[str]:
    return SHELL_BLOCK_RE.findall(doc)


def _documented_invocations(doc: str) -> list[tuple[str, str | None]]:
    """(verb, subcommand-or-None) for every ``stash ...`` line in shell blocks."""
    invocations: list[tuple[str, str | None]] = []
    for block in _shell_blocks(doc):
        for line in block.splitlines():
            line = line.strip().removeprefix("$ ").strip()
            if not line.startswith("stash ") or line.startswith("stash -"):
                continue
            tokens = line.split()[1:]
            verb = tokens[0].strip("\\")
            if verb.startswith("-") or not re.fullmatch(r"[a-z][a-z-]*", verb):
                continue
            sub = None
            if len(tokens) > 1 and not tokens[1].startswith(("-", '"', "<")):
                sub = tokens[1].strip("\\")
            invocations.append((verb, sub))
    return invocations


def _documented_flags(doc: str) -> set[str]:
    flags: set[str] = set()
    for block in _shell_blocks(doc):
        flags.update(FLAG_RE.findall(block))
    return flags


def _roster_tools(doc: str) -> set[str]:
    section = doc.split("## Tool reference", 1)
    assert len(section) == 2, "doc has no '## Tool reference' section"
    body = section[1].split("\n## ", 1)[0]
    return set(ROSTER_ROW_RE.findall(body))


def test_doc_exists_with_required_sections() -> None:
    """The guide exists and carries the agreed H2 skeleton, in order.

    Order is part of the contract: an integrator should read surfaces before
    configuration and configuration before the roster.
    """
    headings = re.findall(r"^## (.+)$", _doc(), re.MULTILINE)
    assert headings == list(REQUIRED_H2_HEADINGS), (
        f"doc H2 headings {headings} != required {list(REQUIRED_H2_HEADINGS)}"
    )


def test_tool_roster_equals_the_shipped_surface() -> None:
    """The Tool reference names exactly the tools that ship — both directions.

    A removed tool left in the guide, or a shipped tool omitted, is a doc/code
    drift the original guide suffered; either direction must red here.
    """
    shipped = _shipped_tools()
    documented = _roster_tools(_doc())
    assert shipped == documented, (
        f"roster/code drift — in doc not shipped: {sorted(documented - shipped)}; "
        f"shipped not in doc: {sorted(shipped - documented)}"
    )


def test_documented_tool_count_matches_the_shipped_surface() -> None:
    """Every tool count stated in prose equals the real roster size.

    The guide's most visible rot was a headline count that no longer matched the
    server. The roster table is checked structurally above; this closes the prose
    gap, so bumping the server's tool count reds the sentence that advertises it.
    """
    shipped = len(_shipped_tools())
    claims = [int(match) for match in TOOL_COUNT_RE.findall(_doc())]
    assert claims, "doc states no tool count; the guide should say how many tools ship"
    wrong = sorted({claim for claim in claims if claim != shipped})
    assert not wrong, f"doc advertises {wrong} tools but cli/mcp_server.py ships {shipped}"


def test_every_stash_token_in_the_doc_is_a_shipped_tool() -> None:
    """No prose escape hatch: anywhere in the guide, a ``stash_*`` token is a tool.

    The original guide rotted exactly this way — mentions of ``stash_vfs_ls`` in
    prose survived roster rewrites. Tool-shaped tokens outside the roster
    (typer's ``@mcp.tool`` form aside) must resolve to real tools.
    """
    shipped = _shipped_tools()
    strays = sorted({token for token in TOOL_NAME_RE.findall(_doc()) if token not in shipped})
    assert not strays, f"doc names non-shipped tool tokens: {strays}"


def test_documented_cli_invocations_resolve() -> None:
    """Every ``stash <verb> [<sub>]`` in a shell block is a real command.

    Invented verbs (the recovered guide's ``stash refine`` disease) must not
    reach the trunk guide.
    """
    commands, groups = _cli_registry()
    problems = []
    for verb, sub in _documented_invocations(_doc()):
        if verb in groups:
            if sub is None:
                problems.append(f"stash {verb} (group invoked without a subcommand)")
            elif sub not in groups[verb]:
                problems.append(f"stash {verb} {sub} (no such subcommand of '{verb}')")
        elif verb not in commands:
            problems.append(f"stash {verb} (no such command)")
    assert not problems, f"doc documents invocations the CLI does not have: {problems}"


def test_documented_flags_exist_in_the_cli() -> None:
    """Every ``--flag`` in a shell block occurs verbatim in cli/main.py.

    Phantom flags are the sibling failure of phantom commands. The allowlist
    covers the documented exemption only; do not grow it to dodge a failure.
    """
    source = CLI_MAIN_PATH.read_text(encoding="utf-8")
    missing = sorted(
        flag
        for flag in _documented_flags(_doc())
        if flag not in source and flag not in DOCUMENTED_FLAG_ALLOWLIST
    )
    assert not missing, f"doc flags absent from cli/main.py: {missing}"


def test_no_obsolete_generation_markers() -> None:
    """The superseded MCP generation stays dead: no routes, modules, or tools.

    ``backend/services/mcp_service.py`` and its server-hosted transport were
    superseded by the stdio server and are unreferenced on trunk; the guide
    must not resurrect their names or the removed tool roster.
    """
    doc = _doc()
    found = sorted(marker for marker in OBSOLETE_MARKERS if marker in doc)
    assert not found, f"doc references the obsolete MCP generation: {found}"
