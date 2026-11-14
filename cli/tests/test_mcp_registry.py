"""Registration guard for the agent-facing MCP tool surface.

This file guards a seam the per-tool command tests structurally cannot reach.
Those tests (``test_mcp_tools_core.py``, ``test_mcp_server.py``) call the tool
*function* directly — ``mcp_server.stash_whoami()`` — so they never consult the
FastMCP registry. Deleting the ``@mcp.tool()`` line above ``def stash_whoami``
therefore leaves every per-tool test green while the tool silently vanishes from
what an MCP client can see. Measured on ``631ff161``: with that decorator
removed, all 259 CLI tests still pass.

So this guard reads the *real* registry through the public
``await mcp.list_tools()`` API (never the private ``mcp._tool_manager``) and
compares it against an explicitly pinned name list. A tool that loses its
decorator, gains one under a new name, or is renamed is a deliberate edit to
``_PINNED_TOOLS`` — never an accident that slips through green.

``cli/mcp_server.py`` registers its tools with bare ``@mcp.tool()`` decorators
(no ``name=`` overrides — verified on the base), so a registered tool name is
always its function name. If a ``name=`` override is ever introduced, this file
must learn about it: the registry name and the module attribute diverge, and the
callable-attribute test below is what surfaces that.
"""

import asyncio

from cli import mcp_server

# The pinned agent-facing tool surface. Sorted, one literal, edited on purpose:
# adding or removing an agent-facing tool means editing this list in the same
# commit that changes the tool. A tuple (not a set literal) so that a
# accidentally duplicated entry is *detectable* — a set literal would silently
# collapse it and the list would look untouched at the same length.
_PINNED_TOOLS = (
    "stash_add_column",
    "stash_add_source",
    "stash_batch_delete",
    "stash_batch_move",
    "stash_batch_restore",
    "stash_browse_source",
    "stash_copy_file",
    "stash_copy_folder",
    "stash_copy_page",
    "stash_create_folder",
    "stash_create_page",
    "stash_create_skill",
    "stash_create_table",
    "stash_delete_column",
    "stash_delete_file",
    "stash_delete_folder",
    "stash_delete_page",
    "stash_delete_row",
    "stash_delete_session",
    "stash_delete_table",
    "stash_edit_file",
    "stash_edit_folder",
    "stash_edit_page",
    "stash_export_table",
    "stash_file_text",
    "stash_fork_skill",
    "stash_get_shared_skill",
    "stash_insert_row",
    "stash_list_agents",
    "stash_list_files",
    "stash_list_folders",
    "stash_list_pages",
    "stash_list_shares",
    "stash_list_skills",
    "stash_list_sources",
    "stash_list_tables",
    "stash_list_trash",
    "stash_list_workspaces",
    "stash_memory_tree",
    "stash_publish_html",
    "stash_publish_markdown",
    "stash_publish_skill",
    "stash_purge",
    "stash_push_event",
    "stash_query_events",
    "stash_query_table",
    "stash_read_page",
    "stash_read_public_skill",
    "stash_read_skill",
    "stash_read_source",
    "stash_remove_source",
    "stash_restore",
    "stash_search",
    "stash_search_public_skills",
    "stash_session_transcript",
    "stash_share_object",
    "stash_snapshot_source",
    "stash_switch_workspace",
    "stash_sync_source",
    "stash_table_schema",
    "stash_tree",
    "stash_unpublish_skill",
    "stash_unshare_object",
    "stash_update_row",
    "stash_update_skill",
    "stash_update_table",
    "stash_upload_file",
    "stash_vfs",
    "stash_whoami",
)

EXPECTED_TOOLS = set(_PINNED_TOOLS)


def _registered_tool_names() -> set:
    """Tool names as an MCP client actually sees them, via the public API."""
    return {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}


def test_registered_tools_match_the_pinned_surface():
    """A lost or renamed ``@mcp.tool()`` shows up here as a named absence.

    The message names the offenders so a failure says which tool vanished or
    appeared, instead of just "68 != 69".
    """
    registered = _registered_tool_names()
    missing = sorted(EXPECTED_TOOLS - registered)
    unexpected = sorted(registered - EXPECTED_TOOLS)

    assert not missing, f"MCP tools no longer registered: {missing}"
    assert not unexpected, f"MCP tools registered but not pinned: {unexpected}"


def test_every_pinned_tool_is_a_callable_module_attribute():
    """A rename that keeps its decorator still fails the guard.

    Registry equality alone would pass if a function were renamed *and* the
    pinned list edited to match while the per-tool test kept calling the old
    name, so the names must also resolve to real callables on the module.
    """
    missing = [
        name for name in sorted(EXPECTED_TOOLS) if not callable(getattr(mcp_server, name, None))
    ]
    assert not missing, f"Pinned tool names are not callable module attributes: {missing}"


def test_pinned_tool_list_is_itself_well_formed():
    """Self-hygiene: the pinned literal must stay duplicate-free and sorted.

    A set literal would swallow a duplicate name silently; keeping the source of
    truth a tuple makes the duplicate visible, and the sorted check keeps the
    list merge-conflict-friendly so additions land in their alphabetical slot.
    """
    assert len(_PINNED_TOOLS) == len(EXPECTED_TOOLS), "duplicate name in _PINNED_TOOLS"
    assert _PINNED_TOOLS == tuple(sorted(_PINNED_TOOLS)), "_PINNED_TOOLS must stay sorted"
