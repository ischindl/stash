"""Tests for `stash tools install --agent pi` (STAS-218).

pi core has no MCP client — its agent-dir mcp.json is only read when the
pi-mcp-adapter extension is registered in the agent dir's settings.json
(runtime-verified against the real pi binaries in Step 1). So the install
path must (a) fail loudly instead of writing a config pi would silently
ignore, (b) land in the agent dir (never the project .mcp.json), and (c)
preserve pi-owned fields (settings, foreign servers with adapter options)
through the shared stash marker/merge discipline.
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


def _write_settings(home_dir: Path, packages) -> None:
    (home_dir / ".pi" / "agent" / "settings.json").write_text(json.dumps({"packages": packages}))


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


# --- fail-loud gate: no adapter registration, no write ------------------------


def test_install_pi_without_settings_json_fails_and_writes_nothing(home):
    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 1
    assert "pi install npm:pi-mcp-adapter" in result.stderr
    assert not _agent_mcp(home).exists()
    assert not (home.parent / ".mcp.json").exists()  # cwd must stay untouched


def test_install_pi_without_registered_adapter_fails(home):
    _write_settings(home, ["npm:pi-context", {"source": "npm:pi-web-access"}])

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 1
    assert "pi-mcp-adapter" in result.stderr
    assert not _agent_mcp(home).exists()


def test_install_pi_fails_loud_on_malformed_settings_json(home):
    (home / ".pi" / "agent" / "settings.json").write_text("{broken")

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 1
    assert "not valid JSON" in result.stderr
    assert not _agent_mcp(home).exists()


# --- gate satisfied: writes into the agent dir, preserving pi's fields --------


def test_install_pi_writes_agent_dir_and_preserves_foreign_pi_config(home):
    _write_settings(home, [{"source": "npm:pi-mcp-adapter", "extensions": ["+index.ts"]}])
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


def test_install_pi_accepts_string_package_registration(home):
    _write_settings(home, ["npm:pi-mcp-adapter"])

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 0, result.stderr
    assert _agent_mcp(home).exists()


def test_install_pi_is_idempotent(home):
    _write_settings(home, ["npm:pi-mcp-adapter"])

    assert _invoke("linear", "--agent", "pi").exit_code == 0
    before = _agent_mcp(home).read_text()
    assert _invoke("linear", "--agent", "pi").exit_code == 0
    assert _agent_mcp(home).read_text() == before


def test_install_pi_refuses_to_clobber_foreign_server_with_same_name(home):
    _write_settings(home, ["npm:pi-mcp-adapter"])
    user_entry = {"command": "user-owned-linear"}
    _agent_mcp(home).write_text(json.dumps({"mcpServers": {"linear": user_entry}}))

    result = _invoke("linear", "--agent", "pi")

    assert result.exit_code == 1
    assert "user-defined" in result.stderr
    assert json.loads(_agent_mcp(home).read_text())["mcpServers"]["linear"] == user_entry


def test_install_pi_respects_agent_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agentdir"))
    (tmp_path / "agentdir").mkdir()
    (tmp_path / "agentdir" / "settings.json").write_text(
        json.dumps({"packages": ["npm:pi-mcp-adapter"]})
    )
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
    _write_settings(home, ["npm:pi-mcp-adapter"])

    result = runner.invoke(main.app, ["tools", "install", "linear", "--agent", "pi", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"ok": True, "name": "linear", "status": "installed"}
