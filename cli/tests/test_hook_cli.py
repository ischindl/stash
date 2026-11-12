"""Tests for `stash hook` — the stable dispatcher agent hook commands call."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import cli.main
from cli.main import _HOOK_EVENTS, app
from cli.tests.conftest import _fresh_hook_modules, _remap_plugin_data_dirs, isolated_hook_dispatch
from stashai.plugin.upload_status import read_upload_status

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "plugins" / "tests" / "fixtures"

runner = CliRunner()


def _real_home_pi_status() -> Path:
    """The operator's own pi health file, resolved from the real home.

    Must be called BEFORE a test fakes ``HOME``: ``expanduser`` reads the
    environment, so resolving it later silently points the snapshot at a fake home
    and the comparison passes vacuously. Read as a snapshot, never as a fixture:
    the assertion is that a test run leaves it exactly as it found it.
    """
    return Path(os.path.expanduser("~")) / ".stash" / "plugins" / "pi" / "upload_status.json"


def _fingerprint(path: Path) -> tuple[object, ...]:
    """Bytes plus mtime, or the fact of absence — so "nothing changed" cannot pass
    vacuously on a machine where the file was never there."""
    if not path.exists():
        return ("absent",)
    stat = path.stat()
    return (path.read_bytes(), stat.st_mtime_ns, stat.st_size)


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


def test_no_hook_record_can_reach_the_real_home(tmp_path: Path) -> None:
    """Every data dir this suite can record into must live under the test's own path.

    ``hook run`` records what it saw, including the invocations it refuses, so
    without the suite-wide redirect a reject-path test writes a failing row into
    the home directory of whoever ran the suite. That is how dozens of failures
    attributed to a hook that never ran appeared on the machine this record was
    introduced on (STAS-267). If the fixture goes away, this says so here rather
    than ``stash status`` saying so later.
    """
    assert cli.main.PLUGIN_DATA_DIRS, "every registered agent must be covered"
    for agent, dest in cli.main.PLUGIN_DATA_DIRS.items():
        assert dest.is_relative_to(tmp_path), f"{agent} would record into {dest}"


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
    """An empty pi payload is a caller defect, and it must be recorded, twice: once
    per entry point.

    STAS-368 gave pi a dispatcher row, so `stash hook run pi <event>` is no longer
    a mistake — it is the supported path, and it runpy's the shipped handler as
    `__main__`. That handler's own tail is a second recorder, kept because it is
    the only one for `python on_stop.py`. One dispatch through two existing
    directories therefore leaves one record in each, and `consecutive_failures`
    staying at 1 per directory is the proof the two sinks were not collapsed.

    Recording stays on purpose (STAS-267): a hook invocation that produced nothing
    is drift worth seeing. What STAS-417 removed is this test writing one of those
    records into a real home, where `stash status` blamed it on the operator's own
    pi install.

    `last_error` reads `Exit` because the CLI's user-error path signals with
    `typer.Exit`, whose message went to stderr; the record's job is that the
    failure is visible at all, not to repeat that line.
    """
    real_status = _real_home_pi_status()
    real_status_before = _fingerprint(real_status)

    with isolated_hook_dispatch(monkeypatch, tmp_path, "pi", configured=True) as sinks:
        result = runner.invoke(app, ["hook", "run", "pi", "on_stop"], input="{}")

        assert result.exit_code == 1
        dispatcher = read_upload_status(sinks["dispatcher"])
        assert dispatcher["health"] == "failing"
        assert dispatcher["consecutive_failures"] == 1
        assert dispatcher["last_failure_operation"] == "hook_run"
        assert dispatcher["last_error"]
        # The handler's own guard is a separate recorder, not a second write into
        # the dispatcher's file.
        handler = read_upload_status(sinks["handler"])
        assert handler["consecutive_failures"] == 1
        assert handler["last_failure_operation"] == "hook_run"

    assert _fingerprint(real_status) == real_status_before, (
        "a test moved the operator's real pi health counter"
    )
    assert "config" not in sys.modules, "the flat hook modules outlived the dispatch"

    assert "config" not in sys.modules, "the flat hook modules outlived the dispatch"


def test_empty_stdin_names_the_byte_count_and_stays_out_of_the_real_home(
    monkeypatch, tmp_path: Path
) -> None:
    """`stash hook run pi <event>` with nothing on stdin must fail loud at the parse,
    naming how many bytes arrived — and must never touch the operator's health file.

    `~/.stash/plugins/pi/upload_status.json` is a health signal the operator reads
    to decide whether THEIR agent install is losing conversations. It is not a
    queue for faults no transport of theirs produced. A `{}` default in the stdin
    parser made those two cases indistinguishable: an absent payload and a payload
    that was an empty object both surfaced as "keys present: []", so the file
    carried a rising failure count for invocations that pi never sent (STAS-417
    measured 6 -> 15 -> 21 in a day, all of them one pytest node).

    Both entry points still record, in their own directories inside the test tree,
    because a hook that produced nothing is worth seeing. The byte-count message is
    what makes the caller nameable instead of invented.
    """
    real_status = _real_home_pi_status()
    real_status_before = _fingerprint(real_status)

    with isolated_hook_dispatch(monkeypatch, tmp_path, "pi", configured=True) as sinks:
        result = runner.invoke(app, ["hook", "run", "pi", "on_stop"], input="")

        assert result.exit_code == 1
        dispatcher = read_upload_status(sinks["dispatcher"])
        assert dispatcher["consecutive_failures"] == 1
        assert "stdin carried 0 bytes" in dispatcher["last_error"], dispatcher["last_error"]
        assert "keys present: []" not in dispatcher["last_error"], (
            "an absent payload must not be reported as an empty object"
        )
        assert read_upload_status(sinks["handler"])["consecutive_failures"] == 1

    assert _fingerprint(real_status) == real_status_before, (
        "a test moved the operator's real pi health counter"
    )


def test_payload_that_is_an_empty_object_still_blames_the_payload(
    monkeypatch, tmp_path: Path
) -> None:
    """A `{}` payload must keep failing in the payload layer, not the parse layer.

    `{}` is a valid JSON object, so the parser returns it and `adapt._require`
    stays the single place that rejects a missing `session_id` — one codepath, no
    second validator. This is the companion that keeps the two causes tellable
    apart: "keys present: []" now means a caller really sent an empty object, which
    is the reading the operator's file was already being given for a payload that
    never existed.
    """
    with isolated_hook_dispatch(monkeypatch, tmp_path, "pi", configured=True) as sinks:
        result = runner.invoke(app, ["hook", "run", "pi", "on_stop"], input="{}")

        assert result.exit_code == 1
        last_error = read_upload_status(sinks["dispatcher"])["last_error"]
        assert "keys present: []" in last_error, last_error
        assert "stdin carried" not in last_error


def test_malformed_stdin_names_the_bytes_that_arrived(monkeypatch, tmp_path: Path) -> None:
    """Truncated or non-JSON stdin is a transport bug, so it must say what arrived
    rather than become an empty dict and be blamed on the payload."""
    with isolated_hook_dispatch(monkeypatch, tmp_path, "pi", configured=True) as sinks:
        result = runner.invoke(app, ["hook", "run", "pi", "on_stop"], input='{"session_id": "a"')

        assert result.exit_code == 1
        last_error = read_upload_status(sinks["dispatcher"])["last_error"]
        assert "not valid JSON" in last_error, last_error
        assert "keys present: []" not in last_error
        assert read_upload_status(sinks["handler"])["consecutive_failures"] == 1


def test_unconfigured_machine_short_circuits_empty_stdin_green_without_recording(
    monkeypatch, tmp_path: Path
) -> None:
    """The unconfigured short-circuit is a contract, not a swallow: an unsetup
    machine exits 0 and leaves no record anywhere, even on stdin nothing about.

    Both sinks are created existing so this stays a real assertion — a guard only
    records into a directory already present, and had the test left them absent it
    would pass for the wrong reason.
    """
    with isolated_hook_dispatch(monkeypatch, tmp_path, "pi", configured=False) as sinks:
        result = runner.invoke(app, ["hook", "run", "pi", "on_stop"], input="")

        assert result.exit_code == 0
        assert not (sinks["dispatcher"] / "upload_status.json").exists()
        assert not (sinks["handler"] / "upload_status.json").exists()


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


def test_hook_run_claude_on_session_end_without_plugin_env_is_green(
    monkeypatch, tmp_path: Path
) -> None:
    """`stash hook run claude on_session_end` must exit 0 with NOTHING set up.

    The hook scripts used to read CLAUDE_PLUGIN_DATA from the environment —
    set only when Claude Code launches the marketplace plugin — so the exact
    command ~/.claude/settings.json carries crashed with a KeyError (and Claude
    shows no error) whenever anything else ran it, which is every run once
    hooks merge into settings.json. An unconfigured machine short-circuits
    green, it does not crash.
    """
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("claude",))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("STASH_CLAUDE_DATA", str(data_dirs["claude"]))
    _fresh_hook_modules()
    fixture = (FIXTURES / "claude" / "session_end.json").read_text()

    try:
        result = runner.invoke(app, ["hook", "run", "claude", "on_session_end"], input=fixture)
    finally:
        _fresh_hook_modules()

    assert result.exit_code == 0
    assert not (data_dirs["claude"] / "upload_status.json").exists()


def test_hook_run_claude_on_session_end_pushes_the_session_end_event(
    monkeypatch, tmp_path: Path
) -> None:
    """A configured machine's Claude session end must reach the server.

    This is the failure the task started from: on the marketplace path Claude
    never emitted session_end at all, and every layer (install output, status
    files) looked fine. The capture is on httpx.Client.request, so dispatch,
    the rewritten config, adapt, and stream_session_end all run for real.
    """
    data_dirs = _remap_plugin_data_dirs(monkeypatch, tmp_path, existing=("claude",))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("STASH_CLAUDE_DATA", str(data_dirs["claude"]))
    (tmp_path / ".stash").mkdir(parents=True, exist_ok=True)
    config_file = tmp_path / ".stash" / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "api_key": "test-key",
                "username": "alice-laptop",
                "base_url": "http://127.0.0.1:9",
            }
        )
    )
    # scope.py binds the config path at import; point the in-process gate at
    # this test's config so the run can't read the real machine's recording
    # scope.
    import stashai.plugin.scope as plugin_scope

    monkeypatch.setattr(plugin_scope, "_CONFIG_FILE", config_file)
    posted: list[dict] = []

    def fake_request(self, method, url, **kwargs):
        body = kwargs.get("json")
        if isinstance(body, dict):
            posted.append(body)
        return httpx.Response(
            200, json={}, request=httpx.Request(method, "http://127.0.0.1:9" + str(url))
        )

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    _fresh_hook_modules()
    fixture = (FIXTURES / "claude" / "session_end.json").read_text()

    try:
        result = runner.invoke(app, ["hook", "run", "claude", "on_session_end"], input=fixture)
    finally:
        _fresh_hook_modules()

    assert result.exit_code == 0
    session_ends = [b for b in posted if b.get("event_type") == "session_end"]
    assert session_ends, f"no session_end POST among {posted}"
    assert session_ends[0]["session_id"] == "abc-123"
    assert session_ends[0]["agent_name"] == "alice-laptop"
    assert session_ends[0]["metadata"]["client"] == "claude_code"
