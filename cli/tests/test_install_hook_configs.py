"""One install run, two clients, two files — each client's hooks land in the
file *that* client reads.

Claude reads hooks out of its shared `~/.claude/settings.json`; Codex reads a
dedicated `~/.codex/hooks.json` and rejects the whole file when it carries a
key it does not know (a top-level `_comment` once silently disabled every Codex
hook). Writing the right events into the wrong file looks like a successful
install and records nothing, so this cross-client guard is what keeps the two
installers from being silently swapped.
"""

from __future__ import annotations

import json
from pathlib import Path

import cli.main
from cli.main import _install_all_hooks

CLAUDE_EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"}
CODEX_EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"}


def _install_two_clients(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cli.main, "_detected_agents", lambda: ["claude", "codex"])
    _install_all_hooks(["claude", "codex"], use_json=True)


def test_claude_hooks_land_in_claude_settings(monkeypatch, tmp_path: Path) -> None:
    _install_two_clients(monkeypatch, tmp_path)

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert CLAUDE_EVENTS == set(settings["hooks"])
    rendered = json.dumps(settings["hooks"])
    assert "stash hook run claude" in rendered
    assert "stash hook run codex" not in rendered
    assert "_comment" not in rendered


def test_codex_hooks_land_in_codex_hooks_json(monkeypatch, tmp_path: Path) -> None:
    _install_two_clients(monkeypatch, tmp_path)

    hooks_path = tmp_path / ".codex" / "hooks.json"
    data = json.loads(hooks_path.read_text())
    assert set(data) == {"hooks"}, "Codex rejects the whole file on any unknown top-level key"
    assert CODEX_EVENTS == set(data["hooks"])
    rendered = json.dumps(data)
    assert "stash hook run codex" in rendered
    assert "stash hook run claude" not in rendered


def test_reinstall_is_stable_for_both_clients(monkeypatch, tmp_path: Path) -> None:
    _install_two_clients(monkeypatch, tmp_path)
    claude_before = (tmp_path / ".claude" / "settings.json").read_text()
    codex_before = (tmp_path / ".codex" / "hooks.json").read_text()

    _install_all_hooks(["claude", "codex"], use_json=True)

    assert (tmp_path / ".claude" / "settings.json").read_text() == claude_before
    assert (tmp_path / ".codex" / "hooks.json").read_text() == codex_before


def test_codex_comment_key_is_swept_from_a_pre_existing_file(monkeypatch, tmp_path: Path) -> None:
    """Older installs wrote `"_comment"` at the top level of hooks.json. Codex
    will not load that file at all, so the installer must remove the key while
    keeping the real hook entries."""
    (tmp_path / ".codex").mkdir()
    stale = {
        "_comment": "managed by stash",
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}]},
    }
    (tmp_path / ".codex" / "hooks.json").write_text(json.dumps(stale))

    _install_two_clients(monkeypatch, tmp_path)

    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert "_comment" not in data
    assert CODEX_EVENTS == set(data["hooks"])
