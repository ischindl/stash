from __future__ import annotations

import json
import subprocess
import sys

import pytest

from stashai.plugin.event import HookEvent
from stashai.plugin.hooks import create_session_record, finalize_session_upload


class _FakeClient:
    def __init__(self):
        self.created = []

    def create_session(self, **kwargs):
        self.created.append(kwargs)
        return {
            "id": "session-row-1",
            "app_url": f"https://app.joinstash.ai/sessions/{kwargs['session_id']}",
        }


def _cfg() -> dict:
    return {
        "agent_name": "alice-agent",
        "client": "codex_cli",
        "api_endpoint": "https://joinstash.ai",
        "api_key": "key",
    }


def test_create_session_record_saves_url_and_transcript_path(tmp_path):
    state = {"session_id": "s1"}
    event = HookEvent(
        kind="session_start",
        session_id="s1",
        cwd="/repo",
        transcript_path="/tmp/s1.jsonl",
    )
    client = _FakeClient()

    url = create_session_record(client, _cfg(), state, event, tmp_path)

    assert url == "https://app.joinstash.ai/sessions/s1"
    assert client.created[0]["session_id"] == "s1"
    assert client.created[0]["cwd"] == "/repo"
    assert state["session_row_id"] == "session-row-1"
    assert state["session_url"] == "https://app.joinstash.ai/sessions/s1"
    assert state["uploaded_session_id"] == "s1"
    assert state["transcript_path"] == "/tmp/s1.jsonl"


def test_finalize_session_upload_spawns_upload_with_transcript(monkeypatch, tmp_path):
    calls = []

    def fake_spawn(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("stashai.plugin.hooks.spawn_session_upload", fake_spawn)

    state = {
        "session_id": "s1",
        "session_row_id": "session-row-1",
        "uploaded_session_id": "s1",
        "cwd": "/repo",
        "stats": {
            "tool_count": 1,
            "tools_used": ["edit"],
            "files_touched": ["app.py"],
        },
    }
    event = HookEvent(
        kind="session_end",
        session_id="s1",
        transcript_path="/tmp/s1.jsonl",
    )

    assert finalize_session_upload(_FakeClient(), _cfg(), state, event, tmp_path)

    assert calls == [
        {
            "session_row_id": "session-row-1",
            "transcript_path": "/tmp/s1.jsonl",
            "cwd": "/repo",
            "files_touched": ["app.py"],
            "session_id": "s1",
            "agent_name": "alice-agent",
            "base_url": "https://joinstash.ai",
            "api_key": "key",
            "data_dir": tmp_path,
        }
    ]


def test_finalize_session_upload_spawns_history_fallback_without_transcript(monkeypatch):
    calls = []

    def fake_spawn(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("stashai.plugin.hooks.spawn_session_upload", fake_spawn)

    state = {
        "session_id": "s1",
        "session_row_id": "session-row-1",
        "uploaded_session_id": "s1",
        "cwd": "/repo",
    }
    event = HookEvent(kind="session_end", session_id="s1")

    assert finalize_session_upload(_FakeClient(), _cfg(), state, event)
    assert calls[0]["transcript_path"] == ""
    assert calls[0]["session_id"] == "s1"


def test_do_session_uploads_artifacts(monkeypatch, tmp_path):
    from stashai.plugin import _do_session_upload

    artifact = tmp_path / "app.py"
    artifact.write_text("print('hi')\n")
    uploads = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs == {
                "base_url": "https://joinstash.ai",
                "api_key": "key",
                "data_dir": str(tmp_path),
            }

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def upload_session_artifact(self, session_row_id, display_path, content):
            uploads.append((session_row_id, display_path, content))

    monkeypatch.setattr(_do_session_upload, "StashClient", FakeClient)
    monkeypatch.setattr(_do_session_upload, "_collect_git_files", lambda cwd: [])
    monkeypatch.setenv("SESSION_FILES_TOUCHED", json.dumps([str(artifact)]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "_do_session_upload.py",
            "session-row-1",
            "",
            str(tmp_path),
            "s1",
            "alice-agent",
            "https://joinstash.ai",
            "key",
            str(tmp_path),
        ],
    )

    _do_session_upload.main()

    assert uploads == [("session-row-1", "app.py", b"print('hi')\n")]


def _capture_spawn(monkeypatch) -> list:
    """Capture the argv + env passed to subprocess.Popen by spawn_skills_sync."""
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append({"cmd": cmd, "env": kwargs.get("env", {})})

        class _P:
            pass

        return _P()

    monkeypatch.setattr("stashai.plugin.session_upload.subprocess.Popen", fake_popen)
    return calls


def test_skills_sync_targets_per_agent_dir(monkeypatch):
    from stashai.plugin.session_upload import spawn_skills_sync

    # Codex/Gemini/OpenCode share the cross-agent ~/.agents/skills standard;
    # Claude and OpenClaw have their own dirs; Pi is synced into its own root
    # even though it also scans .agents, because that copy is hand-made.
    cases = {
        "claude_code": "~/.claude/skills",
        "codex_cli": "~/.agents/skills",
        "gemini_cli": "~/.agents/skills",
        "opencode": "~/.agents/skills",
        "openclaw": "~/.openclaw/skills",
        "hermes": "~/.hermes/skills",
        "pi": "~/.pi/agent/skills",
    }
    for client, expected_dir in cases.items():
        calls = _capture_spawn(monkeypatch)
        spawn_skills_sync(
            {"client": client, "api_endpoint": "https://joinstash.ai", "api_key": "k"},
        )
        assert len(calls) == 1, client
        cmd = calls[0]["cmd"]
        assert cmd[:3] == ["stash", "skills", "sync"]
        assert cmd[cmd.index("--dir") + 1] == expected_dir
        assert calls[0]["env"]["STASH_URL"] == "https://joinstash.ai"
        assert calls[0]["env"]["STASH_API_KEY"] == "k"


def test_skills_sync_noop_for_project_only_agents(monkeypatch):
    from stashai.plugin.session_upload import spawn_skills_sync

    # Cursor only loads project-level .cursor/skills — no global dir, no spawn.
    # An empty client means the adapter never named itself, which is also not
    # this function's decision to make.
    for client in ("cursor", ""):
        calls = _capture_spawn(monkeypatch)
        spawn_skills_sync({"client": client})
        assert calls == [], client


def test_skills_sync_refuses_a_client_it_does_not_know(monkeypatch):
    from stashai.plugin.session_upload import spawn_skills_sync

    # A named client with no skills root is a missing entry, and silence here
    # is how an agent's skills sat unsynced while its hook called this every
    # session. The refusal has to say what to add.
    calls = _capture_spawn(monkeypatch)
    with pytest.raises(ValueError) as refused:
        spawn_skills_sync({"client": "unknown_agent"})
    assert "unknown_agent" in str(refused.value)
    assert "_SKILLS_DIR_BY_CLIENT" in str(refused.value)
    assert calls == []


# --- guidance convergence seam (STAS-248) ------------------------------------
#
# Installed guidance used to be written once, at connect time, and then frozen:
# a release that changed the skill model reached nobody until someone re-ran an
# installer. The seam that fixes it rides the session-start hook, so these tests
# pin both halves of that promise — it spawns when an installed file lags this
# build, and it stays silent when nothing does.


def _stamp(version: str) -> str:
    return f"guidance_version={version}"


def _seed_guidance(tmp_path, named: dict) -> list:
    """Guidance files under a fake home, keyed by agent name."""
    dests = []
    for agent, content in named.items():
        dest = tmp_path / agent / "AGENTS.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        dests.append(dest)
    return dests


def test_a_target_that_lags_this_build_triggers_one_refresh(monkeypatch, tmp_path):
    from stashai.plugin.session_upload import (
        guidance_refresh_command,
        spawn_guidance_refresh_if_stale,
    )

    calls = _capture_spawn(monkeypatch)
    dests = _seed_guidance(
        tmp_path,
        {
            "pi": f"<!-- stash-plugin:begin {_stamp('0.1.0')} -->\nold\n<!-- stash-plugin:end -->\n",
            "codex": f"<!-- stash-plugin:begin {_stamp('0.1.368')} -->\nnew\n<!-- stash-plugin:end -->\n",
        },
    )

    spawn_guidance_refresh_if_stale(dests, "0.1.368")

    assert len(calls) == 1
    assert calls[0]["cmd"] == guidance_refresh_command()


def test_guidance_current_across_every_target_spawns_nothing(monkeypatch, tmp_path):
    from stashai.plugin.session_upload import spawn_guidance_refresh_if_stale

    calls = _capture_spawn(monkeypatch)
    dests = _seed_guidance(
        tmp_path,
        {
            agent: f"<!-- stash-plugin:begin {_stamp('0.1.368')} -->\ntext\n<!-- stash-plugin:end -->\n"
            for agent in ("pi", "codex", "opencode")
        },
    )

    spawn_guidance_refresh_if_stale(dests, "0.1.368")

    # A spawn per session start would be the churn this card exists to remove:
    # the probe has to be able to prove "nothing to do", not just "something to do".
    assert calls == []


def test_a_guidance_file_that_was_never_installed_is_not_staleness(monkeypatch, tmp_path):
    from stashai.plugin.session_upload import spawn_guidance_refresh_if_stale

    calls = _capture_spawn(monkeypatch)
    installed = _seed_guidance(
        tmp_path, {"codex": f"<!-- stash-plugin:begin {_stamp('0.1.368')} -->\nx\n<!-- ... -->\n"}
    )
    never_installed = tmp_path / "gemini" / "GEMINI.md"

    spawn_guidance_refresh_if_stale([*installed, never_installed], "0.1.368")

    # Absent means the user never connected that agent. Refreshing does not
    # install, and a session start certainly does not.
    assert calls == []
    assert not never_installed.exists()


def test_a_target_that_cannot_be_read_is_sent_to_the_command_that_reports_it(monkeypatch, tmp_path):
    from stashai.plugin import session_upload

    calls = _capture_spawn(monkeypatch)
    dests = _seed_guidance(
        tmp_path, {"codex": f"<!-- stash-plugin:begin {_stamp('0.1.368')} -->\nx\n<!-- ... -->\n"}
    )

    def unreadable(self):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(type(dests[0]), "read_bytes", unreadable)

    session_upload.spawn_guidance_refresh_if_stale(dests, "0.1.368")

    # "Couldn't look" is not "already current": skipping here would keep a stale
    # copy running silently. The refresh reads the same file with a parser that
    # fails loud and names it.
    assert len(calls) == 1


def test_the_refresh_is_detached_silent_and_addressed_at_this_package(monkeypatch, tmp_path):
    from stashai.plugin.session_upload import spawn_guidance_refresh_if_stale

    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(kwargs)

        class _P:
            pass

        return _P()

    monkeypatch.setattr("stashai.plugin.session_upload.subprocess.Popen", fake_popen)
    dests = _seed_guidance(tmp_path, {"pi": "no markers at all"})

    spawn_guidance_refresh_if_stale(dests, "0.1.368")

    kwargs = calls[0]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_a_refresh_that_cannot_even_start_does_not_break_the_session(monkeypatch, tmp_path):
    from stashai.plugin.session_upload import spawn_guidance_refresh_if_stale

    def explode(*args, **kwargs):
        raise OSError("no such file or directory")

    monkeypatch.setattr("stashai.plugin.session_upload.subprocess.Popen", explode)
    dests = _seed_guidance(tmp_path, {"pi": "stale"})

    spawn_guidance_refresh_if_stale(dests, "0.1.368")

    # The stamp stays stale, so the next session start asks again. Nothing here
    # may propagate into a hook whose stdout is an agent's session payload.
