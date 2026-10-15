"""Tests for `_install_claude` — hooks merge into ~/.claude/settings.json.

Claude's hooks used to ship through the marketplace plugin, which never
touched settings.json (the "installed via marketplace" path silently
recorded nothing on machines where the plugin did not fire), and the
plugin double-fires every event once the merged file exists. So the
installer merges into settings.json — preserving every unrelated key the
user keeps there — and retires the leftover plugin best-effort.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import cli.main
from cli.main import _install_claude

EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"}


def _hermetic_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path / ".claude"


def _arm_installed_plugin(home: Path) -> None:
    registry = home / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"plugins": {"stash@stash-plugins": {}}}))


def _capture_runs(monkeypatch, result: subprocess.CompletedProcess) -> list[list[str]]:
    argvs: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        argvs.append(list(cmd))
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    return argvs


def test_fresh_install_writes_only_hooks_into_settings(monkeypatch, tmp_path: Path) -> None:
    """A fresh settings.json is created containing only the hooks key.

    A top-level junk key poisons the whole file for the client (Codex
    rejects ~/.codex/hooks.json wholesale when it sees `_comment`); Claude
    settings.json must ship with nothing but real settings keys.
    """
    claude_dir = _hermetic_home(monkeypatch, tmp_path)

    status, _ = _install_claude(False)

    assert status == "installed"
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert set(settings) == {"hooks"}
    assert EVENTS == set(settings["hooks"])
    rendered = json.dumps(settings)
    assert "stash hook run claude" in rendered
    assert "_comment" not in rendered
    # Machine-independent commands: no absolute install paths.
    assert "/home/" not in rendered and "/Users/" not in rendered
    assert "${CLAUDE_PLUGIN_ROOT}" not in rendered


def test_install_preserves_user_settings_and_hooks(monkeypatch, tmp_path: Path) -> None:
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    claude_dir.mkdir()
    user_hook = {"hooks": [{"type": "command", "command": "echo mine"}]}
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "env": {"A": "1"},
                "model": "opus",
                "enabledPlugins": {"other@market": True},
                "hooks": {"SessionStart": [user_hook]},
            }
        )
    )

    status, _ = _install_claude(False)

    assert status == "installed"
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["env"] == {"A": "1"}
    assert settings["model"] == "opus"
    assert settings["enabledPlugins"] == {"other@market": True}
    session_start = settings["hooks"]["SessionStart"]
    assert user_hook in session_start
    assert any("stash hook run claude" in json.dumps(e) for e in session_start)


def test_install_sweeps_stale_absolute_path_entries(monkeypatch, tmp_path: Path) -> None:
    """Pre-`stash hook run` installs embedded the install dir's absolute path;
    reinstalling must replace those with the stable command, not stack a
    second stash entry per event."""
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    claude_dir.mkdir()
    stale = {
        "hooks": [
            {
                "type": "command",
                "command": "python3 /old/venv/stashai/plugin/assets/claude/scripts/on_session_end.py",
            }
        ]
    }
    (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"SessionEnd": [stale]}}))

    _install_claude(False)

    session_end = json.loads((claude_dir / "settings.json").read_text())["hooks"]["SessionEnd"]
    stash_entries = [e for e in session_end if "stash" in json.dumps(e)]
    assert len(stash_entries) == 1
    assert "stash hook run claude on_session_end" in json.dumps(stash_entries[0])


def test_second_run_is_noop(monkeypatch, tmp_path: Path) -> None:
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    _install_claude(False)
    before = (claude_dir / "settings.json").read_text()

    status, _ = _install_claude(False)

    assert status == "skipped"
    assert (claude_dir / "settings.json").read_text() == before


def test_unparseable_settings_fails_without_touching_the_file(monkeypatch, tmp_path: Path) -> None:
    """A user's hand-broken settings.json is reported, not clobbered — and the
    teardown must not run either: with the merge refused, the plugin is still
    the only thing streaming, so removing it would cut the user off."""
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    claude_dir.mkdir()
    broken = claude_dir / "settings.json"
    broken.write_text("{not json")
    _arm_installed_plugin(claude_dir)
    monkeypatch.setattr(cli.main, "_claude_binary", lambda: "/bin/claude")
    argvs = _capture_runs(monkeypatch, subprocess.CompletedProcess([], 0, "", ""))

    status, _ = _install_claude(False)

    assert status == "failed"
    assert broken.read_text() == "{not json"
    assert argvs == []


def test_installed_plugin_is_retired_via_the_resolved_binary(monkeypatch, tmp_path: Path) -> None:
    """The whole point of resolving the binary is the install that parks claude
    outside PATH; a bare "claude" in any one call fails for exactly that user."""
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    _arm_installed_plugin(claude_dir)
    binary = str(tmp_path / ".claude" / "local" / "claude")
    monkeypatch.setattr(cli.main, "_claude_binary", lambda: binary)
    argvs = _capture_runs(monkeypatch, subprocess.CompletedProcess([], 0, "ok", ""))

    status, detail = _install_claude(False)

    assert status == "installed"
    assert argvs, "expected the teardown to shell out"
    assert set(argv[0] for argv in argvs) == {binary}, f"a call used a bare name: {argvs}"
    assert ["plugin", "uninstall", "stash@stash-plugins"] == argvs[0][1:]
    assert "marketplace plugin removed" in detail


def test_missing_binary_keeps_install_green_and_names_the_manual_step(
    monkeypatch, tmp_path: Path
) -> None:
    """The merged hooks fire from settings.json regardless of the `claude`
    CLI; a machine without it must not see the install fail — only a note
    telling the user to drop the double-firing plugin by hand."""
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    _arm_installed_plugin(claude_dir)
    monkeypatch.setattr(cli.main, "_claude_binary", lambda: None)
    argvs = _capture_runs(monkeypatch, subprocess.CompletedProcess([], 0, "", ""))

    status, detail = _install_claude(False)

    assert status == "installed"
    assert argvs == []
    assert "claude plugin uninstall stash@stash-plugins" in detail
    assert (claude_dir / "settings.json").exists()


@pytest.mark.parametrize("boom", [subprocess.CalledProcessError(1, "claude"), OSError("exec fail")])
def test_failing_teardown_is_a_note_not_a_failure(boom, monkeypatch, tmp_path: Path) -> None:
    """A marketplace uninstall that cannot run leaves a double-firing plugin —
    annoying, but the hooks the user asked for ARE installed; failing the
    install would take their streaming down with it."""
    claude_dir = _hermetic_home(monkeypatch, tmp_path)
    _arm_installed_plugin(claude_dir)
    monkeypatch.setattr(cli.main, "_claude_binary", lambda: "/bin/claude")

    def fake_run(cmd, *args, **kwargs):
        raise boom

    monkeypatch.setattr(subprocess, "run", fake_run)

    status, detail = _install_claude(False)

    assert status == "installed"
    assert "did not run" in detail
    assert json.loads((claude_dir / "settings.json").read_text())["hooks"]["SessionEnd"]


def test_no_teardown_when_no_plugin_is_installed(monkeypatch, tmp_path: Path) -> None:
    """Clean machines never see a `claude plugin uninstall` call."""
    _hermetic_home(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.main, "_claude_binary", lambda: "/bin/claude")
    argvs = _capture_runs(monkeypatch, subprocess.CompletedProcess([], 0, "", ""))

    status, detail = _install_claude(False)

    assert status == "installed"
    assert argvs == []
    assert "marketplace plugin removed" not in detail
