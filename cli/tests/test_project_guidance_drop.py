"""STAS-211: a fresh connect delivers repo guidance to every detected agent.

`_auto_connect_repo` writes the manifest + CLAUDE.md block; cursor and hermes
were the two agents that got nothing — Cursor only auto-loads project rules
from `.cursor/rules/*.mdc`, and Hermes reads `<repo>/HERMES.md` (never
AGENTS.md). Both drops are stdout-silent: the connect no-op contract keeps
stdout empty (JSON mode carries only the result document).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cli.main import (
    _AGENTS_MD_BEGIN,
    _AGENTS_MD_END,
    _assets_dir,
    _auto_connect_repo,
    _upsert_agents_md,
)
from stashai.plugin.guidance import SKILL_MODEL


@pytest.fixture(autouse=True)
def _no_agent_binaries(monkeypatch):
    # Presence is decided by fake home dirs only; PATH noise on the test
    # machine (e.g. a hermes binary) must not flip the gate.
    monkeypatch.setattr(shutil, "which", lambda name: None)


@pytest.fixture
def home(monkeypatch, tmp_path):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


@pytest.fixture
def repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    return repo_root


def test_cursor_rule_written_when_cursor_present(home, repo):
    (home / ".cursor").mkdir()

    _auto_connect_repo(repo, {})

    dropped = repo / ".cursor" / "rules" / "stash.mdc"
    assert dropped.read_text() == (_assets_dir("cursor") / "stash.mdc").read_text()
    assert SKILL_MODEL in dropped.read_text()


def test_cursor_rule_not_written_when_cursor_absent(home, repo):
    _auto_connect_repo(repo, {})

    assert not (repo / ".cursor").exists()


def test_hermes_file_written_when_hermes_present(home, repo):
    (home / ".hermes").mkdir()

    _auto_connect_repo(repo, {})

    text = (repo / "HERMES.md").read_text()
    assert text.startswith(f"{_AGENTS_MD_BEGIN}\n")
    assert _AGENTS_MD_END in text
    assert SKILL_MODEL in text
    body = (_assets_dir("hermes") / "HERMES.md").read_text()
    assert body.rstrip() in text


def test_hermes_file_untouched_when_hermes_absent(home, repo):
    _auto_connect_repo(repo, {})

    assert not (repo / "HERMES.md").exists()


def test_hermes_block_is_byte_stable_across_refreshes(home, repo):
    body = (_assets_dir("hermes") / "HERMES.md").read_text()
    target = repo / "HERMES.md"

    _upsert_agents_md(target, body)
    first = target.read_bytes()
    _upsert_agents_md(target, body)

    assert target.read_bytes() == first
    assert target.read_text().count(_AGENTS_MD_BEGIN) == 1


def test_fresh_connect_drops_keep_stdout_empty(home, repo, capsys):
    (home / ".cursor").mkdir()
    (home / ".hermes").mkdir()

    _auto_connect_repo(repo, {}, use_json=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert (repo / ".cursor" / "rules" / "stash.mdc").is_file()
    assert (repo / "HERMES.md").is_file()
