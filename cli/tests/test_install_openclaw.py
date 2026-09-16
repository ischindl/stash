"""Tests for `_install_openclaw` — extension install via the openclaw CLI.

The installer checks `openclaw --version`, then shells out to
`openclaw plugins install --force <assets>`, so these tests stub
subprocess.run and assert the skip/installed/failed contract around it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from cli.main import _AGENTS_MD_BEGIN, _assets_dir, _install_openclaw
from stashai.plugin.guidance import SKILL_MODEL

CURRENT_VERSION_BANNER = "🦞 OpenClaw 2026.7.1 (abc1234)"


def _patch_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path / ".openclaw" / "extensions" / "stash"


def _fake_run(calls: list[list[str]], version_banner: str = CURRENT_VERSION_BANNER):
    def run(cmd, **kwargs):
        calls.append(cmd)
        stdout = version_banner if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return run


def test_install_runs_openclaw_cli(monkeypatch, tmp_path: Path) -> None:
    _patch_home(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    status, detail = _install_openclaw(False)

    assert status == "installed"
    assert "openclaw gateway restart" in detail
    assert calls == [
        ["openclaw", "--version"],
        [
            "openclaw",
            "plugins",
            "install",
            "--force",
            "--dangerously-force-unsafe-install",
            str(_assets_dir("openclaw")),
        ],
    ]


def test_outdated_openclaw_fails_with_upgrade_message(monkeypatch, tmp_path: Path) -> None:
    _patch_home(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", _fake_run(calls, version_banner="🦞 OpenClaw 2026.1.29 (6522de6)")
    )

    status, detail = _install_openclaw(False)

    assert status == "failed"
    assert "2026.1.29 is older than 2026.4.0" in detail
    assert "upgrade openclaw" in detail
    assert calls == [["openclaw", "--version"]]


def test_up_to_date_extension_is_skipped_without_running_cli(monkeypatch, tmp_path: Path) -> None:
    ext_dir = _patch_home(monkeypatch, tmp_path)
    shutil.copytree(
        _assets_dir("openclaw"),
        ext_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    def fail_run(cmd, **kwargs):
        raise AssertionError("openclaw CLI must not run when extension is current")

    monkeypatch.setattr(subprocess, "run", fail_run)

    status, _ = _install_openclaw(False)

    assert status == "skipped"


def test_stale_extension_is_reinstalled(monkeypatch, tmp_path: Path) -> None:
    ext_dir = _patch_home(monkeypatch, tmp_path)
    shutil.copytree(
        _assets_dir("openclaw"),
        ext_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (ext_dir / "index.ts").write_text("// old version")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    status, _ = _install_openclaw(False)

    assert status == "installed"
    assert [c[:3] for c in calls] == [
        ["openclaw", "--version"],
        ["openclaw", "plugins", "install"],
    ]


def test_failed_cli_surfaces_error_tail(monkeypatch, tmp_path: Path) -> None:
    _patch_home(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        stdout = CURRENT_VERSION_BANNER if "--version" in cmd else ""
        return subprocess.CompletedProcess(
            cmd, 1, stdout=stdout, stderr="boom\nplugin rejected by gateway"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    status, detail = _install_openclaw(False)

    assert status == "failed"
    assert detail == "plugin rejected by gateway"


def _workspace_agents(tmp_path: Path) -> Path:
    return tmp_path / ".openclaw" / "workspace" / "AGENTS.md"


def test_skipped_extension_still_refreshes_workspace_guidance(monkeypatch, tmp_path: Path) -> None:
    """STAS-211: the AGENTS.md guidance upsert runs BEFORE the content-match
    skip, so users whose extension is already current still receive it."""
    ext_dir = _patch_home(monkeypatch, tmp_path)
    shutil.copytree(
        _assets_dir("openclaw"),
        ext_dir,
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    def fail_run(cmd, **kwargs):
        raise AssertionError("openclaw CLI must not run when extension is current")

    monkeypatch.setattr(subprocess, "run", fail_run)

    status, _ = _install_openclaw(False)

    assert status == "skipped"
    guidance = _workspace_agents(tmp_path).read_text()
    assert _AGENTS_MD_BEGIN in guidance
    assert SKILL_MODEL in guidance


def test_workspace_guidance_is_idempotent_across_runs(monkeypatch, tmp_path: Path) -> None:
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run([]))

    _install_openclaw(False)
    first = _workspace_agents(tmp_path).read_bytes()
    _install_openclaw(False)

    assert _workspace_agents(tmp_path).read_bytes() == first
    assert first.decode().count(_AGENTS_MD_BEGIN) == 1


def test_install_writes_no_stdout(monkeypatch, tmp_path: Path, capsys) -> None:
    """The guidance upsert must stay stdout-silent: setup --json purity."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_run([]))

    _install_openclaw(False)

    assert capsys.readouterr().out == ""
