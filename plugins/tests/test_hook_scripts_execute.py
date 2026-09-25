"""Execute every `stash hook run` script end-to-end against its fixture.

`stash hook run <agent> <event>` runs these exact files via runpy, so these
tests cover the same lines the dispatcher runs — an arity or import regression
in any script (like the historical `uploads_enabled(cfg, event)` TypeError
that broke every Codex session start) fails loudly here instead of in the
field.
"""

from __future__ import annotations

import io
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from stashai.plugin.upload_status import read_upload_status

PLUGINS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGINS_DIR.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PI_SCRIPTS = PLUGINS_DIR / "pi-plugin" / "scripts"

# Mirrors _HOOK_EVENTS in cli/main.py; test_hook_cli.py asserts the CLI table
# matches the script files these entries point at.
_AGENT_EVENTS = {
    "claude": ("on_session_start", "on_prompt", "on_tool_use", "on_stop", "on_session_end"),
    "codex": ("on_session_start", "on_prompt", "on_tool_use", "on_stop"),
    "cursor": (
        "on_session_start",
        "on_prompt",
        "on_tool_use",
        "on_agent_response",
        "on_session_end",
    ),
    "gemini": ("on_session_start", "on_prompt", "on_tool_use", "on_stop", "on_session_end"),
    "hermes": ("on_session_start", "on_prompt", "on_tool_use", "on_stop", "on_session_end"),
    "openclaw": ("on_session_start", "on_prompt", "on_stop", "on_session_end"),
    "opencode": ("on_session_start", "on_prompt", "on_tool_use", "on_session_end"),
    # Pi is not dispatched by `stash hook run` (no _HOOK_EVENTS row — pi reads
    # executable files from ~/.pi/hooks/ natively), but its handlers are the
    # same shipped scripts, so they must stay API-conformant: execute them here.
    "pi": ("on_session_start", "on_prompt", "on_tool_use", "on_stop", "on_session_end"),
}

# Each agent's DATA_DIR env var (see each plugin's scripts/config.py).
_DATA_DIR_ENV = {
    "claude": "CLAUDE_PLUGIN_DATA",
    "codex": "STASH_CODEX_DATA",
    "cursor": "STASH_CURSOR_DATA",
    "gemini": "STASH_GEMINI_DATA",
    "hermes": "STASH_HERMES_DATA",
    "openclaw": "STASH_OPENCLAW_DATA",
    "opencode": "STASH_OPENCODE_DATA",
    "pi": "STASH_PI_DATA",
}

_CASES = [(agent, event) for agent, events in _AGENT_EVENTS.items() for event in events]


@pytest.mark.parametrize(("agent", "event"), _CASES)
def test_hook_script_executes(agent, event, monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv(_DATA_DIR_ENV[agent], str(tmp_path / "hook-data"))
    fixture = (FIXTURES / agent / f"{event.removeprefix('on_')}.json").read_text()
    monkeypatch.setattr(sys, "stdin", io.StringIO(fixture))
    scripts_dir = PLUGINS_DIR / f"{agent}-plugin" / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))

    # The scripts import flat sibling modules (`adapt`, `config`); drop any
    # cached copy so each run binds this plugin's modules and this test's env.
    for mod in ("adapt", "config"):
        sys.modules.pop(mod, None)
    try:
        runpy.run_path(str(scripts_dir / f"{event}.py"), run_name="__main__")
    finally:
        for mod in ("adapt", "config"):
            sys.modules.pop(mod, None)

    # A dispatchable agent's crash propagates out of runpy and fails this test.
    # pi's crash does not: its entry point answers the host with exit 0, so the
    # traceback on stderr is the only in-process signal that it died. Asserting
    # it here keeps "green means the script actually ran" true for pi too.
    captured = capsys.readouterr()
    assert "Traceback (most recent call last)" not in captured.err
    # Nothing was uploaded in this fixture (no configured endpoint), so a green
    # run must not leave an upload record behind either.
    assert not (tmp_path / "hook-data" / "upload_status.json").exists()


@pytest.mark.parametrize("event", _AGENT_EVENTS["pi"])
def test_pi_fatal_hook_records_and_announces_without_failing_the_host(
    event, tmp_path: Path
) -> None:
    """A shipped pi handler that dies must stay invisible to pi but loud everywhere else.

    pi reads an exit code of zero and one JSON object on stdout; a hook that
    exits non-zero is discarded by the host, which is exactly how a whole
    generation of pi sessions went unrecorded with nobody the wiser. So the
    entry point records the crash in the file `stash status` reads, prints the
    traceback, answers with a `systemMessage`, and still exits 0.

    Run as a subprocess because an in-process harness cannot observe the exit
    code pi depends on. Only the crash is synthetic — the entry point, the
    recording, the stdout contract and the exit code are the shipped ones.
    """
    scripts = tmp_path / "pi-runtime"
    shutil.copytree(PI_SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    handler = scripts / f"{event}.py"
    text = handler.read_text()
    crash = '    raise RuntimeError("synthetic STAS-267 handler crash")\n'
    handler.write_text(text.replace("def main():\n", "def main():\n" + crash, 1))

    data_dir = tmp_path / "pi-data"
    data_dir.mkdir()
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "STASH_PI_DATA": str(data_dir),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    fixture = (FIXTURES / "pi" / f"{event.removeprefix('on_')}.json").read_text()
    result = subprocess.run(
        [sys.executable, str(handler)],
        input=fixture,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, f"pi host must not see a failing hook:\n{result.stderr}"
    message = json.loads(result.stdout)["systemMessage"]
    assert message.strip(), "the pi host must be told the hook failed"
    assert "stash status" in message
    assert "synthetic STAS-267 handler crash" in result.stderr

    status = read_upload_status(data_dir)
    assert status["health"] == "failing"
    assert status["consecutive_failures"] == 1
    assert status["last_failure_operation"] == "hook_run"
    assert "synthetic STAS-267 handler crash" in status["last_error"]


def test_codex_hooks_json_template_shape() -> None:
    """Codex rejects the whole hooks file when it sees unknown top-level keys,
    and trusts hooks by command hash — commands must be machine-independent."""
    data = json.loads((PLUGINS_DIR / "codex-plugin" / "hooks.json").read_text())
    assert set(data.keys()) == {"hooks"}
    for entries in data["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["command"].startswith("stash hook run codex ")


def test_claude_hooks_json_commands_are_machine_independent() -> None:
    """No absolute paths: hooks.json ships to every machine as-is.

    SessionStart additionally runs the plugin-owned CLI floor check first (see
    test_ensure_cli.py) — the only upgrade path a too-old CLI can still reach —
    so it is prefixed with a ${CLAUDE_PLUGIN_ROOT} invocation instead of
    starting at `stash`. That variable is expanded by Claude Code, so the
    command stays machine-independent.
    """
    data = json.loads((PLUGINS_DIR / "claude-plugin" / "hooks" / "hooks.json").read_text())
    for entries in data["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                command = hook["command"]
                assert "stash hook run claude " in command
                assert not command.startswith("/")
                assert "/Users/" not in command and "/home/" not in command
                if "ensure_cli.sh" in command:
                    assert command.startswith("bash ${CLAUDE_PLUGIN_ROOT}/scripts/")


def test_gemini_settings_snippet_commands_are_machine_independent() -> None:
    data = json.loads((PLUGINS_DIR / "gemini-plugin" / "settings.snippet.json").read_text())
    for entries in data["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["command"].startswith("stash hook run gemini ")


def test_cursor_hooks_json_commands_are_machine_independent() -> None:
    data = json.loads((PLUGINS_DIR / "cursor-plugin" / "hooks.json").read_text())
    for entries in data["hooks"].values():
        for entry in entries:
            assert entry["command"].startswith("stash hook run cursor ")


def test_hermes_config_snippet_commands_are_machine_independent() -> None:
    text = (PLUGINS_DIR / "hermes-plugin" / "config.snippet.yaml").read_text()
    assert "stash hook run hermes " in text
    assert "${PLUGIN_ROOT}" not in text
