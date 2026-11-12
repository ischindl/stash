"""The pi extension is a transport, so it must stay logic-free.

`stashai/plugin` holds every Stash call and every rule about pi's event payloads.
The one artifact pi 1.x executes on its own is `<agentDir>/extensions/stash.ts`, and
anything that file decides is a rule that a package upgrade cannot reach: a user's
machine keeps the copy the installer left there. These guards pin the two properties
that make that safe.

1. It has no error handling. `stash hook run` failures must reach the terminal; a
   `catch` here is how a whole generation of pi sessions went unrecorded while the
   installer reported success.
2. It forwards exactly the five events the Python side has handlers for — no more
   (an event with no handler is a wasted process spawn on every keystroke), and no
   earlier than the point where an event carries the payload the handler needs.

Both trees are checked: `plugins/pi-plugin/` is what contributors edit, and
`stashai/plugin/assets/pi/` is what the installer actually copies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "plugins" / "pi-plugin"
ASSET_DIR = REPO_ROOT / "stashai" / "plugin" / "assets" / "pi"

SOURCE_TREE = "source"
ASSET_TREE = "assets"
TREES = (SOURCE_TREE, ASSET_TREE)

# pi event name -> the `stash hook run pi on_*` handler it feeds.
FORWARDED_EVENTS = {
    "session_start": "on_session_start",
    "before_agent_start": "on_prompt",
    "tool_result": "on_tool_use",
    "agent_settled": "on_stop",
    "session_shutdown": "on_session_end",
}


def _extension(tree: str) -> Path:
    root = SRC_DIR if tree == SOURCE_TREE else ASSET_DIR
    path = root / "extensions" / "stash.ts"
    assert path.is_file(), f"{tree} tree ships no {path}"
    return path


def _stripped(tree: str) -> str:
    """The extension with its comments removed, so a guard cannot be satisfied by
    a comment that merely mentions the thing it forbids."""
    text = _extension(tree).read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def test_the_extension_exists_in_both_trees() -> None:
    for tree in TREES:
        assert _extension(tree).read_bytes() == _extension(SOURCE_TREE).read_bytes()


@pytest.mark.parametrize("tree", TREES)
def test_no_error_handling_of_any_kind(tree: str) -> None:
    """No `catch`, no `try`, no optional-chaining fallback: a Stash failure has to
    surface, and this file is the only place a user could not see it."""
    code = _stripped(tree)
    assert not re.search(r"\bcatch\b", code), "stash.ts must never catch"
    assert not re.search(r"\btry\s*\{", code), "stash.ts must never try"
    assert not re.search(r"\?\?", code), "stash.ts must not paper over a missing value"


@pytest.mark.parametrize("tree", TREES)
def test_no_timing_or_retry_logic(tree: str) -> None:
    """A timeout, a debounce, or a retry queue would make the order of recorded
    events depend on this file. Serial awaited dispatch is the whole ordering
    contract, so nothing here may reorder it."""
    code = _stripped(tree)
    for forbidden in ("setTimeout", "setInterval", "retry", "queueMicrotask", "unref("):
        assert forbidden not in code, f"stash.ts must not {forbidden}: ordering is pi's job"


@pytest.mark.parametrize("tree", TREES)
def test_every_forwarded_event_reaches_a_shipped_handler(tree: str) -> None:
    code = _stripped(tree)
    for pi_event, handler in FORWARDED_EVENTS.items():
        assert f'pi.on("{pi_event}"' in code, f"stash.ts no longer subscribes {pi_event}"
        assert f'runHook("{handler}"' in code, f"{pi_event} no longer feeds {handler}"

    shipped = {path.stem for path in (SRC_DIR / "scripts").glob("on_*.py")}
    named = set(re.findall(r'runHook\("(on_[a-z_]+)"', code))
    assert named == shipped, f"forwarded handlers {sorted(named)} != shipped {sorted(shipped)}"


@pytest.mark.parametrize("tree", TREES)
def test_it_subscribes_nothing_else(tree: str) -> None:
    """Each subscription is a process spawn on pi's hot path. `tool_call`,
    `agent_end`, `turn_*` and `message_*` all fire more often than the five events
    we record and none of them has a handler."""
    subscribed = set(re.findall(r'pi\.on\("([a-z_]+)"', _stripped(tree)))
    expected = set(FORWARDED_EVENTS)
    assert subscribed == expected, (
        f"unexpected subscriptions: {sorted(subscribed - expected)}; "
        f"missing: {sorted(expected - subscribed)}"
    )


@pytest.mark.parametrize("tree", TREES)
def test_only_a_user_authored_message_counts_as_a_prompt(tree: str) -> None:
    """pi injects its own messages with `messageType: "custom"`. Recording those as
    prompts would put words in the user's mouth in the session transcript."""
    gate = re.search(r'pi\.on\("before_agent_start".*?\n\s*\}\);', _stripped(tree), re.DOTALL)
    assert gate, "the before_agent_start subscription vanished"
    assert 'event.messageType !== "user"' in gate.group(0)


@pytest.mark.parametrize("tree", TREES)
def test_tools_are_forwarded_from_the_result_side_only(tree: str) -> None:
    """`tool_call` fires before the tool has run, so it carries no result. Feeding
    it to `on_tool_use` would stream a tool use whose answer is always empty."""
    code = _stripped(tree)
    assert "tool_call" not in code
    assert "content: event.content" in code
    assert "toolName: event.toolName" in code


@pytest.mark.parametrize("tree", TREES)
def test_child_output_reaches_the_user_not_pi(tree: str) -> None:
    """`["pipe", "ignore", "inherit"]`: stdin is how the payload travels, stdout is
    dropped because pi draws on the same terminal and reads none of it, and stderr
    is inherited so a handler traceback is visible."""
    code = _stripped(tree)
    assert 'stdio: ["pipe", "ignore", "inherit"]' in code
    assert re.search(r"\.stdout\b", code) is None, "nothing may read the child's stdout"


@pytest.mark.parametrize("tree", TREES)
def test_failures_are_announced(tree: str) -> None:
    """The one thing the extension is allowed to say. Without it a missing or
    failing `stash` binary is indistinguishable from a session with nothing to
    record."""
    code = _stripped(tree)
    assert 'process.stderr.write("stash pi hook failed: "' in code
    assert 'child.on("error"' in code, "a stash binary that cannot start must be reported"
    assert re.search(r'child\.on\("close"[\s\S]{0,120}?code !== 0', code), (
        "a non-zero exit must be reported"
    )
