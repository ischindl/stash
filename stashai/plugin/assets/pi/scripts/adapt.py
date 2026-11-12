"""Pi 1.x extension payload -> canonical HookEvent.

Pi 1.x loads TypeScript extensions; its old ``~/.pi/hooks/`` directory is dead
(pi only warns about it and never runs what is in there). The live mechanism is
``extensions/stash.ts``, which pipes one JSON object per event into
``stash hook run pi <event>``. The transport forwards pi's own event fields under
pi's own names — renaming them into our vocabulary is this module's job — and adds
the three values pi exposes only through its context object, never on an event:
``session_id``, ``cwd`` and ``transcript_path``.

Per-leg payload (measured against the installed pi 1.5.2 bundle):

  on_session_start  {session_id, cwd, transcript_path, reason}
  on_prompt         {session_id, cwd, transcript_path, messageType, prompt}
  on_tool_use       {session_id, cwd, transcript_path, toolName, input, content,
                     structuredContent, isError}
  on_stop           {session_id, cwd, transcript_path, aborted}
  on_session_end    {session_id, cwd, transcript_path, reason}

A payload missing one of its required keys is a bug in the shipped transport, so
it fails loud rather than recording an event with invented values.

pi's own tool vocabulary is ``bash powershell read write edit grep find ls`` plus
any registered custom or MCP tool. Its path field is ``path``, and ``find`` is our
``glob``.
"""

from __future__ import annotations

from stashai.plugin.event import HookEvent

_TOOL_MAP = {
    "bash": "bash",
    "powershell": "bash",
    "read": "read",
    "write": "write",
    "edit": "edit",
    "grep": "grep",
    "find": "glob",
    "ls": "ls",
}


def _require(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"pi hook payload has no string {key!r}; keys present: {sorted(data)}")
    return value


def _normalize(name: str) -> str:
    return _TOOL_MAP.get(name.lower(), name.lower())


def _tool_input(input_data: object) -> dict:
    """pi's `input` plus the canonical keys `summarize_tool_use` reads."""
    if not isinstance(input_data, dict):
        raise ValueError(
            f"pi tool payload `input` must be an object, got {type(input_data).__name__}"
        )
    tool_input = dict(input_data)
    path = tool_input.get("path")
    if isinstance(path, str) and path:
        tool_input["file_path"] = path
    first_edit = next((e for e in tool_input.get("edits", []) if isinstance(e, dict)), None)
    if first_edit is not None:
        tool_input["old_string"] = first_edit.get("oldText", "")
        tool_input["new_string"] = first_edit.get("newText", "")
    return tool_input


def _tool_response(data: dict) -> dict:
    """pi's `content` blocks plus the exit code, in the keys summarize.py reads."""
    content = data.get("content")
    if not isinstance(content, list):
        raise ValueError(
            f"pi tool payload `content` must be an array, got {type(content).__name__}"
        )
    text = "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )
    response: dict = {"stdout": text, "is_error": bool(data.get("isError", False))}
    structured = data.get("structuredContent")
    if isinstance(structured, dict) and "exit_code" in structured:
        response["exit_code"] = structured["exit_code"]
    return response


def adapt_session_start(data: dict) -> HookEvent:
    return HookEvent(
        kind="session_start",
        session_id=_require(data, "session_id"),
        cwd=_require(data, "cwd"),
        transcript_path=_require(data, "transcript_path"),
    )


def adapt_prompt(data: dict) -> HookEvent:
    return HookEvent(
        kind="prompt",
        session_id=_require(data, "session_id"),
        cwd=_require(data, "cwd"),
        transcript_path=_require(data, "transcript_path"),
        prompt_text=_require(data, "prompt"),
    )


def adapt_tool_use(data: dict) -> HookEvent:
    return HookEvent(
        kind="tool_use",
        session_id=_require(data, "session_id"),
        cwd=_require(data, "cwd"),
        transcript_path=_require(data, "transcript_path"),
        tool_name=_normalize(_require(data, "toolName")),
        tool_input=_tool_input(data.get("input")),
        tool_response=_tool_response(data),
    )


def adapt_stop(data: dict) -> HookEvent:
    """pi's `agent_settled` carries no assistant text — the transcript upload
    carries the turn, so `last_assistant_message` stays empty by design."""
    return HookEvent(
        kind="stop",
        session_id=_require(data, "session_id"),
        cwd=_require(data, "cwd"),
        transcript_path=_require(data, "transcript_path"),
        extras={"aborted": bool(data.get("aborted", False))},
    )


def adapt_session_end(data: dict) -> HookEvent:
    return HookEvent(
        kind="session_end",
        session_id=_require(data, "session_id"),
        cwd=_require(data, "cwd"),
        transcript_path=_require(data, "transcript_path"),
    )
