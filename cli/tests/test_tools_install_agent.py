"""Tests for `stash tools install --agent pi` (STAS-218, re-measured by STAS-368).

pi 1.x reads `<agentDir>/mcp.json` itself: verified against the installed
1.5.2 bundle, where `pi mcp list` connects to a stdio server named in that file
with no other extension registered. STAS-218's premise — that pi core has no MCP
client and needs the pi-mcp-adapter extension registered first — held for the pi
build of its day and is gone now, so the install path must (a) write the agent dir
directly (never the project .mcp.json) and (b) preserve pi-owned fields
(`settings`, a foreign server's own options) through the shared marker/merge
discipline.

`mcp list` also proves an unknown top-level key (`stashManagedServers`, which our
merge writes) does not stop pi from parsing the file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import main
from cli.exit_codes import EXIT_USER_ERROR
from cli.main import STASH_MANAGED_MCP_KEY

runner = CliRunner()

SERVER = {
    "id": "srv-1",
    "name": "linear",
    "transport": "stdio",
    "command": "npx -y linear-mcp",
    "url": None,
    "headers": {},
    "env": {"LINEAR_API_KEY": "lin_x"},
}


class FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_mcp_servers(self):
        return [SERVER]


@pytest.fixture(autouse=True)
def _fake_wiring(monkeypatch):
    monkeypatch.setattr(main, "_client", lambda: FakeClient())
    monkeypatch.setattr(main.telemetry, "record", lambda *a, **k: None)
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch HOME so ~/.pi/agent resolution is exercised for real."""
    home_dir = tmp_path / "home"
    (home_dir / ".pi" / "agent").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def _agent_mcp(home_dir: Path) -> Path:
    return home_dir / ".pi" / "agent" / "mcp.json"


def _invoke(*args):
    return runner.invoke(main.app, ["tools", "install", *args])


# --- agent-dir resolution ----------------------------------------------------


def test_pi_agent_dir_defaults_to_home_pi_agent(home):
    assert main._pi_agent_dir() == home / ".pi" / "agent"


def test_pi_agent_dir_honors_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom-agent-dir"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(override))
    assert main._pi_agent_dir() == override.resolve()


def test_pi_agent_dir_expands_tilde_home(home, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~")
    assert main._pi_agent_dir() == home
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/boxes/pi")
    assert main._pi_agent_dir() == home / "boxes" / "pi"


# --- no registration step: pi reads the file we write -------------------------


def test_install_pi_needs_no_settings_json_and_writes_only_the_agent_dir(home):
    """The gate this replaces refused the install until pi-mcp-adapter was
    registered, telling the user to `pi install npm:pi-mcp-adapter` for a config
    pi 1.x would have read anyway. An empty agent dir must now just work — and must
    still not touch the project's .mcp.json."""
    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 0, result.stderr
    assert json.loads(_agent_mcp(home).read_text())["mcpServers"]["linear"]["command"] == "npx"
    assert not (home / ".pi" / "agent" / "settings.json").exists()
    assert not (home.parent / ".mcp.json").exists()  # cwd must stay untouched


def test_install_pi_writes_agent_dir_and_preserves_foreign_pi_config(home):
    chrome = {"command": "npx", "args": ["-y", "chrome-devtools-mcp"], "directTools": True}
    _agent_mcp(home).write_text(
        json.dumps({"settings": {"toolPrefix": "mcp"}, "mcpServers": {"chrome-devtools": chrome}})
    )

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 0, result.stderr
    config = json.loads(_agent_mcp(home).read_text())
    assert config["settings"] == {"toolPrefix": "mcp"}
    assert config["mcpServers"]["chrome-devtools"] == chrome
    assert config["mcpServers"]["linear"] == {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "linear-mcp"],
        "env": {"LINEAR_API_KEY": "lin_x"},
    }
    assert config[STASH_MANAGED_MCP_KEY] == ["linear"]
    assert not (home.parent / ".mcp.json").exists()  # pi leg never writes Claude's file


def test_install_pi_is_idempotent(home):
    assert _invoke("linear", "--agent", "pi").exit_code == 0
    before = _agent_mcp(home).read_text()
    assert _invoke("linear", "--agent", "pi").exit_code == 0
    assert _agent_mcp(home).read_text() == before


def test_install_pi_refuses_to_clobber_foreign_server_with_same_name(home):
    user_entry = {"command": "user-owned-linear"}
    _agent_mcp(home).write_text(json.dumps({"mcpServers": {"linear": user_entry}}))

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 1
    assert "user-defined" in result.stderr
    assert json.loads(_agent_mcp(home).read_text())["mcpServers"]["linear"] == user_entry


def test_install_pi_respects_agent_dir_env_override(tmp_path, monkeypatch):
    # Scratch HOME: a successful install records its destination under ~/.stash,
    # and this test must not write into the developer's real home.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agentdir"))
    (tmp_path / "agentdir").mkdir()
    monkeypatch.chdir(tmp_path)

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "agentdir" / "mcp.json").exists()


# --- command surface ----------------------------------------------------------


def test_install_unknown_agent_fails_with_supported_list(home):
    result = _invoke("linear", "--agent", "cursor")

    assert result.exit_code == EXIT_USER_ERROR
    assert "No MCP install path for agent 'cursor' yet" in result.stderr
    assert "claude, pi" in result.stderr


def test_install_pi_json_reports_status(home):
    result = runner.invoke(main.app, ["tools", "install", "linear", "--agent", "pi", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True, "name": "linear", "status": "installed"}
