"""Tests for `stash tools remove` retracting what `stash tools install` wrote.

Defect D-3 (found by STAS-298's pi MCP rehearsal): `tools remove` deleted only
the account-side registry row, so the entry `tools install` had merged into an
agent's own MCP config survived — `pi mcp list` kept listing the server as
connected and its tools stayed callable, and the only way back was hand-restoring
a snapshot of `~/.pi/agent/mcp.json`.

The invariant these tests pin: **after `stash tools remove <name>` exits 0, no
agent config file Stash manages still resolves `<name>`; if any does, `remove`
exits non-zero and has not deleted the registry row.** Entries Stash does not own
are never touched — the same no-clobber property install already honours.
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
CHROME = {"command": "npx", "args": ["-y", "chrome-devtools-mcp"], "directTools": True}
FOREIGN_LINEAR = {"command": "user-owned-linear"}


class FakeClient:
    """Registry stub: one stdio server, and every deletion is recorded."""

    def __init__(self):
        self.deleted: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_mcp_servers(self):
        return [SERVER]

    def delete_mcp_server(self, server_id):
        self.deleted.append(server_id)


@pytest.fixture(autouse=True)
def _fake_wiring(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(main, "_client", lambda: client)
    monkeypatch.setattr(main.telemetry, "record", lambda *a, **k: None)
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    # Rich wraps stderr at 80 columns by default, which would split a scratch
    # tmp_path mid-token; these tests assert that a message *names* a file, so
    # they need the line unwrapped.
    monkeypatch.setenv("COLUMNS", "200")
    return client


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A scratch HOME so agent-dir and ~/.stash resolution are exercised for real."""
    home_dir = tmp_path / "home"
    (home_dir / ".pi" / "agent").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    return home_dir


def _write_settings(home_dir: Path, packages) -> None:
    (home_dir / ".pi" / "agent" / "settings.json").write_text(json.dumps({"packages": packages}))


def _agent_mcp(home_dir: Path) -> Path:
    return home_dir / ".pi" / "agent" / "mcp.json"


def _project_mcp(cwd: Path) -> Path:
    return cwd / ".mcp.json"


def _installs(home_dir: Path) -> Path:
    return home_dir / ".stash" / "mcp-installs.json"


def _stash_entry() -> dict:
    return main._mcp_json_entry(SERVER)


def _seed_agent_config(
    home_dir: Path, *, managed: bool = True, foreign_name: str | None = "chrome-devtools"
) -> Path:
    """Write a pi agent mcp.json shaped exactly like a post-install file.

    With managed=False the `linear` entry belongs to the user instead — the
    name-collision case that removal must refuse rather than resolve.
    """
    servers = {}
    if foreign_name:
        servers[foreign_name] = CHROME
    servers["linear"] = _stash_entry() if managed else FOREIGN_LINEAR
    dest = _agent_mcp(home_dir)
    config: dict = {"settings": {"toolPrefix": "mcp"}, "mcpServers": servers}
    if managed:
        config[STASH_MANAGED_MCP_KEY] = ["linear"]
    dest.write_text(json.dumps(config, indent=2) + "\n")
    return dest


def _invoke(*args):
    return runner.invoke(main.app, ["tools", "remove", *args])


# --- the D-3 reproduction -----------------------------------------------------


def test_remove_retracts_pre_ledger_install_via_known_destinations(home, _fake_wiring):
    """Today's orphan: install wrote the entry, no install record exists.

    Removal must still find the agent config through the agent resolvers, retract
    the entry, leave the neighbour alone, and delete the registry row.
    """
    dest = _seed_agent_config(home)
    assert not _installs(home).exists()

    result = _invoke("linear")

    assert result.exit_code == 0, result.stderr
    config = json.loads(dest.read_text())
    assert "linear" not in config["mcpServers"]
    assert "linear" not in config.get(STASH_MANAGED_MCP_KEY, [])
    assert config["mcpServers"]["chrome-devtools"] == CHROME
    assert config["settings"] == {"toolPrefix": "mcp"}
    assert _fake_wiring.deleted == ["srv-1"]


def test_remove_retracts_every_agent_it_installed_into(home, _fake_wiring, tmp_path, monkeypatch):
    """One removal cleans every destination recorded at install time, even from an
    unrelated directory where the cwd `.mcp.json` no longer resolves."""
    _write_settings(home, ["npm:pi-mcp-adapter"])
    assert (
        runner.invoke(main.app, ["tools", "install", "linear", "--agent", "claude"]).exit_code == 0
    )
    assert runner.invoke(main.app, ["tools", "install", "linear", "--agent", "pi"]).exit_code == 0
    project, agent = _project_mcp(tmp_path), _agent_mcp(home)
    assert "linear" in json.loads(project.read_text())["mcpServers"]

    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = _invoke("linear")

    assert result.exit_code == 0, result.stderr
    assert "linear" not in json.loads(project.read_text())["mcpServers"]
    assert "linear" not in json.loads(agent.read_text())["mcpServers"]
    assert not (elsewhere / ".mcp.json").exists()
    assert _fake_wiring.deleted == ["srv-1"]


# --- no-clobber guarantee -----------------------------------------------------


def test_remove_never_touches_foreign_entry(home, _fake_wiring):
    """A server the user added themselves is byte-identical after removal."""
    dest = _seed_agent_config(home)
    before = json.loads(dest.read_text())["mcpServers"]["chrome-devtools"]

    assert _invoke("linear").exit_code == 0

    config = json.loads(dest.read_text())
    assert config["mcpServers"]["chrome-devtools"] == before
    assert json.dumps(config["mcpServers"]["chrome-devtools"], sort_keys=True) == json.dumps(
        CHROME, sort_keys=True
    )


def test_remove_refuses_loudly_when_a_foreign_entry_holds_the_name(home, _fake_wiring):
    """A name defined outside Stash's ownership is never deleted: removal aborts,
    names the offending file, and leaves the registry row intact."""
    dest = _seed_agent_config(home, managed=False)

    result = _invoke("linear")

    assert result.exit_code == EXIT_USER_ERROR
    assert str(dest) in result.stderr
    assert json.loads(dest.read_text())["mcpServers"]["linear"] == FOREIGN_LINEAR
    assert _fake_wiring.deleted == []


def test_remove_fails_loud_on_unparseable_agent_config(home, _fake_wiring):
    """A target file that is not valid JSON aborts removal with the row intact."""
    dest = _project_mcp(Path.cwd())
    dest.write_text("{not json")

    result = _invoke("linear")

    assert result.exit_code == EXIT_USER_ERROR
    assert str(dest) in result.stderr
    assert dest.read_text() == "{not json"
    assert _fake_wiring.deleted == []


# --- idempotence and the empty state ------------------------------------------


def test_remove_is_idempotent_and_definitive_when_nothing_is_installed(home, _fake_wiring):
    """Never-installed is a success with a plain statement, not silence — twice."""
    result = _invoke("linear")

    assert result.exit_code == 0, result.stderr
    assert _fake_wiring.deleted == ["srv-1"]
    assert "was not installed in any agent config" in result.stdout
    assert not _agent_mcp(home).exists()
    assert not _project_mcp(Path.cwd()).exists()

    again = _invoke("linear")

    assert again.exit_code == 0, again.stderr
    assert "was not installed in any agent config" in again.stdout


def test_remove_retracts_without_the_pi_adapter_gate(home, _fake_wiring):
    """Retraction must not require the install-time adapter gate: an entry written
    earlier is removed even when pi can no longer read it."""
    _write_settings(home, ["npm:pi-context"])
    dest = _seed_agent_config(home)

    result = _invoke("linear")

    assert result.exit_code == 0, result.stderr
    assert "linear" not in json.loads(dest.read_text())["mcpServers"]
    assert _fake_wiring.deleted == ["srv-1"]


# --- the install record -------------------------------------------------------


def test_install_records_the_destination_and_remove_prunes_it(home, _fake_wiring, tmp_path):
    """Install records its absolute destination once per server; a successful
    removal forgets it."""
    _write_settings(home, ["npm:pi-mcp-adapter"])

    assert (
        runner.invoke(main.app, ["tools", "install", "linear", "--agent", "claude"]).exit_code == 0
    )
    record = json.loads(_installs(home).read_text())
    assert record == {"linear": [str(_project_mcp(tmp_path))]}

    assert runner.invoke(main.app, ["tools", "install", "linear", "--agent", "pi"]).exit_code == 0
    assert json.loads(_installs(home).read_text()) == {
        "linear": sorted([str(_project_mcp(tmp_path)), str(_agent_mcp(home))])
    }
    assert runner.invoke(main.app, ["tools", "install", "linear", "--agent", "pi"]).exit_code == 0
    assert json.loads(_installs(home).read_text()) == {
        "linear": sorted([str(_project_mcp(tmp_path)), str(_agent_mcp(home))])
    }

    assert _invoke("linear").exit_code == 0
    assert json.loads(_installs(home).read_text()) == {}
