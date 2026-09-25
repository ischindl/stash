"""Tests for `stash hook` — the stable dispatcher agent hook commands call."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import cli.main
from cli.main import _HOOK_EVENTS, app
from stashai.plugin.upload_status import read_upload_status

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "plugins" / "tests" / "fixtures"

runner = CliRunner()


def _remap_plugin_data_dirs(
    monkeypatch, tmp_path: Path, existing: tuple[str, ...]
) -> dict[str, Path]:
    """Point every registered plugin data dir under `tmp_path`, creating only the
    named agents' directories. `PLUGIN_DATA_DIRS` is built from the real home at
    import time, and a hook must never write outside the test tree."""
    remapped: dict[str, Path] = {}
    for agent in cli.main.PLUGIN_DATA_DIRS:
        data_dir = tmp_path / ".stash" / "plugins" / agent
        if agent in existing:
            data_dir.mkdir(parents=True)
        remapped[agent] = data_dir
    monkeypatch.setattr(cli.main, "PLUGIN_DATA_DIRS", remapped)
    return remapped


def _crashing_agent_assets(monkeypatch, tmp_path: Path, agent: str) -> None:
    """Stand in for a shipped-assets tree whose script dies, the way a copy that
    drifted from the installed package does. Everything from `stash hook run`
    down to the crash — dispatch, recording, exit status — is production code."""
    scripts = tmp_path / "assets" / agent / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "on_stop.py").write_text("raise RuntimeError('synthetic STAS-267 dispatch crash')\n")
    monkeypatch.setattr(cli.main, "_assets_dir", lambda _agent: tmp_path / "assets" / agent)


def test_hook_run_rejects_unknown_agent() -> None:
    result = runner.invoke(app, ["hook", "run", "bogus", "on_stop"])
    assert result.exit_code == 1


@pytest.mark.parametrize("agent", sorted(_HOOK_EVENTS))
def test_hook_run_rejects_unknown_event(agent: str) -> None:
    result = runner.invoke(app, ["hook", "run", agent, "bogus"])
    assert result.exit_code == 1


def test_hook_events_table_matches_script_files() -> None:
    """Every dispatchable (agent, event) must have a script in the shipped
    assets, and every script must be dispatchable — a drifting table means
    hooks that silently do nothing or scripts nobody can run."""
    for agent, events in _HOOK_EVENTS.items():
        scripts_dir = REPO_ROOT / "stashai" / "plugin" / "assets" / agent / "scripts"
        on_disk = {p.stem for p in scripts_dir.glob("on_*.py")}
        assert set(events) == on_disk, f"{agent}: table {sorted(events)} vs files {sorted(on_disk)}"


def test_hook_auto_update_writes_preference(monkeypatch, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("cli.config.USER_CONFIG_FILE", cfg_file)

    assert runner.invoke(app, ["hook", "auto-update", "on"]).exit_code == 0
    assert json.loads(cfg_file.read_text())["codex_auto_update"] is True

    assert runner.invoke(app, ["hook", "auto-update", "off"]).exit_code == 0
    assert json.loads(cfg_file.read_text())["codex_auto_update"] is False

    assert runner.invoke(app, ["hook", "auto-update", "maybe"]).exit_code == 1


def test_hook_run_records_a_fatal_codex_hook_and_keeps_its_nonzero_exit(
    monkeypatch, tmp_path: Path
) -> None:
    """A dispatchable agent's hook that dies must be recorded and must still fail.

    The non-zero exit and the stdout shape are the agent's existing contract —
    recording must not soften them, or Codex/Claude would start seeing a hook
    that failed as one that succeeded.
    """
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("codex",))
    _crashing_agent_assets(monkeypatch, tmp_path, "codex")
    fixture = (FIXTURES / "codex" / "stop.json").read_text()

    result = runner.invoke(app, ["hook", "run", "codex", "on_stop"], input=fixture)

    assert result.exit_code == 1
    status = read_upload_status(data_dirs["codex"])
    assert status["health"] == "failing"
    assert status["consecutive_failures"] == 1
    assert status["last_failure_operation"] == "hook_run"
    assert "synthetic STAS-267 dispatch crash" in status["last_error"]
    assert "Traceback" not in result.stdout


def test_two_consecutive_fatal_hooks_stay_visible_to_status(monkeypatch, tmp_path: Path) -> None:
    """`stash status` reads the same record twice without losing coherence: one
    entry for the agent, the failure count growing, health still failing."""
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("codex",))
    _crashing_agent_assets(monkeypatch, tmp_path, "codex")
    fixture = (FIXTURES / "codex" / "stop.json").read_text()

    for _ in range(2):
        assert runner.invoke(app, ["hook", "run", "codex", "on_stop"], input=fixture).exit_code == 1

    snapshot = cli.main._upload_health_snapshot()
    codex = next(item for item in snapshot if item["agent"] == "codex")
    assert codex["data_dir"] == str(data_dirs["codex"])
    assert codex["health"] == "failing"
    assert codex["consecutive_failures"] == 2
    assert codex["last_failure_operation"] == "hook_run"
    assert [item["agent"] for item in cli.main._failing_upload_agents(snapshot)] == ["codex"]
    assert len([item for item in snapshot if item["agent"] == "codex"]) == 1


def test_a_fatal_hook_for_an_unregistered_agent_records_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    """An agent the CLI does not register has no data dir to record into. It must
    still exit non-zero, and it must not gain a status file or a directory —
    `stash status` would then report a plugin that has never run as failing."""
    _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("codex",))

    result = runner.invoke(app, ["hook", "run", "totally-bogus", "on_stop"], input="{}")

    assert result.exit_code == 1
    assert list(tmp_path.rglob("upload_status.json")) == []
    assert not (tmp_path / ".stash" / "plugins" / "totally-bogus").exists()


def test_dispatching_pi_records_the_mistake_without_dispatching_pi(
    monkeypatch, tmp_path: Path
) -> None:
    """pi has no dispatcher row by design, so `stash hook run pi <event>` is a
    mistake. It is still a hook invocation that produced nothing, and pi's own
    data dir is the sink — the invisibility this task fixes is exactly a
    rejected pi hook that leaves no trace.

    `last_error` reads `Exit` because the CLI's user-error path signals with
    `typer.Exit`, whose message went to stderr; the record's job is that the
    failure is visible at all, not to repeat that line.
    """
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("pi",))

    result = runner.invoke(app, ["hook", "run", "pi", "on_stop"], input="{}")

    assert result.exit_code == 1
    status = read_upload_status(data_dirs["pi"])
    assert status["health"] == "failing"
    assert status["consecutive_failures"] == 1
    assert status["last_failure_operation"] == "hook_run"
    assert status["last_error"]


def test_a_fatal_hook_for_a_known_agent_and_unknown_event_is_recorded(
    monkeypatch, tmp_path: Path
) -> None:
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("codex",))

    result = runner.invoke(app, ["hook", "run", "codex", "bogus"], input="{}")

    assert result.exit_code == 1
    status = read_upload_status(data_dirs["codex"])
    assert status["health"] == "failing"
    assert status["last_failure_operation"] == "hook_run"


def test_a_green_hook_run_records_no_success_for_work_it_did_not_do(
    monkeypatch, tmp_path: Path
) -> None:
    """The dispatcher must not write a success record on a hook's return: the
    recorded success path belongs to the upload itself (the client records an
    event/transcript it actually POSTed). A hook that short-circuited — this
    fixture has no configured endpoint — must leave no status file at all."""
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("codex",))
    monkeypatch.setenv("STASH_CODEX_DATA", str(data_dirs["codex"]))
    for mod in ("adapt", "config"):
        sys.modules.pop(mod, None)
    fixture = (FIXTURES / "codex" / "stop.json").read_text()

    try:
        result = runner.invoke(app, ["hook", "run", "codex", "on_stop"], input=fixture)
    finally:
        for mod in ("adapt", "config"):
            sys.modules.pop(mod, None)

    assert result.exit_code == 0
    assert not (data_dirs["codex"] / "upload_status.json").exists()


def test_hook_run_codex_on_stop_executes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("STASH_CODEX_DATA", str(tmp_path / "codex-data"))
    # The hook scripts import flat sibling modules (`adapt`, `config`); drop
    # cached copies so this run binds the codex ones under this test's env.
    for mod in ("adapt", "config"):
        sys.modules.pop(mod, None)

    fixture = (FIXTURES / "codex" / "stop.json").read_text()
    try:
        result = runner.invoke(app, ["hook", "run", "codex", "on_stop"], input=fixture)
    finally:
        for mod in ("adapt", "config"):
            sys.modules.pop(mod, None)

    assert result.exit_code == 0
