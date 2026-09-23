"""The managed-guidance writer: surgical, idempotent, and loud when confused.

STAS-248 found guidance files written once at install time still shipping an
outdated Skill model on every agent on this box, because nothing rewrote them
afterwards and the old splice silently accepted any marker shape it was handed
(a second `split` pair, an unclosed comment, an inverted block — all produced
*something* and the file stayed broken). The helpers under test therefore own
two properties at once: a well-formed block is replaced in place without
disturbing a single byte of user prose, and a file whose managed state cannot be
identified uniquely is refused with a ValueError naming the path. Silence is the
failure mode being designed out, so most of these tests assert on what the
writer *did not* touch as much as on what it wrote.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cli.main import (
    _AGENTS_MD_BEGIN,
    _AGENTS_MD_BEGIN_MARK,
    _AGENTS_MD_END,
    _AGENTS_MD_END_MARK,
    _INSTALLERS,
    _guidance_body,
    _guidance_dest,
    _guidance_targets,
    _refresh_installed_guidance,
    _upsert_agents_md,
)

BODY = "A Skill is a *special folder*."
STALE_BLOCK = "<!-- stash-plugin:begin -->\nOld guidance.\n<!-- stash-plugin:end -->"

# Exactly the agents with a product-owned global guidance file. Cursor loads
# project-level rules only, Claude's global file is not product-owned, and
# Hermes' guidance block lives in ~/.hermes/config.yaml which Hermes itself
# rewrites — adding one of those to the table would make the refresh clobber a
# file the product does not own, so the list is asserted, not incidental.
TABLE_AGENTS = ["pi", "codex", "opencode", "gemini", "openclaw"]

OPENCLAW_CURRENT_BANNER = "🦞 OpenClaw 2026.7.1 (abc1234)"


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _fake_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _run_installer(agent: str, monkeypatch) -> None:
    """Run the real `connect` installer for `agent` against a fake home."""
    if agent == "openclaw":

        def run(cmd, **kwargs):
            stdout = OPENCLAW_CURRENT_BANNER if "--version" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", run)
    _INSTALLERS[agent](False)


def test_missing_file_is_created_with_the_managed_block(tmp_path):
    target = tmp_path / ".pi" / "AGENTS.md"

    assert _upsert_agents_md(target, BODY) == "refreshed"

    assert target.read_text() == f"{_AGENTS_MD_BEGIN}\n{BODY}\n{_AGENTS_MD_END}\n"


def test_file_without_markers_keeps_user_text_and_appends_the_block(tmp_path):
    target = _write(tmp_path / "AGENTS.md", "# My rules\n")

    assert _upsert_agents_md(target, BODY) == "refreshed"

    text = target.read_text()
    assert text.startswith("# My rules\n")
    assert _AGENTS_MD_BEGIN in text


def test_file_without_markers_and_no_trailing_newline_still_separates(tmp_path):
    target = _write(tmp_path / "AGENTS.md", "# My rules")

    _upsert_agents_md(target, BODY)

    assert target.read_text().startswith("# My rules\n" + _AGENTS_MD_BEGIN)


def test_current_block_reports_unchanged_and_leaves_mtime_alone(tmp_path):
    target = _write(tmp_path / "AGENTS.md", f"{_AGENTS_MD_BEGIN}\n{BODY}\n{_AGENTS_MD_END}\n")
    before = target.read_bytes()
    os.utime(target, (target.stat().st_atime, target.stat().st_mtime - 100))
    mtime_before = target.stat().st_mtime_ns

    assert _upsert_agents_md(target, BODY) == "unchanged"

    assert target.read_bytes() == before
    assert target.stat().st_mtime_ns == mtime_before


def test_stale_block_is_spliced_without_touching_surrounding_prose(tmp_path):
    target = _write(
        tmp_path / "AGENTS.md",
        f"# Mine\n\n{STALE_BLOCK}\n\n# Also mine\nKeep this paragraph.\n",
    )

    assert _upsert_agents_md(target, BODY) == "refreshed"

    text = target.read_text()
    assert text.startswith("# Mine\n\n" + _AGENTS_MD_BEGIN + "\n")
    assert text.endswith(_AGENTS_MD_END + "\n\n# Also mine\nKeep this paragraph.\n")
    assert "Old guidance." not in text
    assert text.count(_AGENTS_MD_BEGIN_MARK) == 1


def test_plain_unversioned_begin_marker_is_stale_content_and_gets_stamped(tmp_path):
    # Acceptance 4/5's no-shim rule in one assertion: the old plain begin line is
    # not special-cased backwards, it is simply not current, so it is rewritten.
    target = _write(tmp_path / "AGENTS.md", f"{STALE_BLOCK}\n")

    assert _upsert_agents_md(target, BODY) == "refreshed"

    assert target.read_text().startswith(_AGENTS_MD_BEGIN)
    assert "guidance_version=" in target.read_text()


def test_body_trailing_whitespace_is_normalized_so_reruns_are_noops(tmp_path):
    target = tmp_path / "AGENTS.md"

    _upsert_agents_md(target, f"{BODY}\n\n\n")

    assert _upsert_agents_md(target, BODY) == "unchanged"


@pytest.mark.parametrize(
    "text",
    [
        # begin with no end
        f"{STALE_BLOCK.split(_AGENTS_MD_END)[0]}",
        # end with no begin
        f"{_AGENTS_MD_END}\n",
        # two begins
        f"{_AGENTS_MD_BEGIN}\na\n{_AGENTS_MD_END}\n{_AGENTS_MD_BEGIN}\nb\n",
        # two ends
        f"{_AGENTS_MD_BEGIN}\na\n{_AGENTS_MD_END}\n{_AGENTS_MD_END}\n",
        # end before begin
        f"{_AGENTS_MD_END}\nx\n{_AGENTS_MD_BEGIN}\n",
        # end marker never closed
        f"{_AGENTS_MD_BEGIN}\na\n<!-- {_AGENTS_MD_END_MARK} no close\n",
    ],
)
def test_malformed_managed_state_raises_and_writes_nothing(tmp_path, text):
    target = _write(tmp_path / "AGENTS.md", text)
    before = target.read_bytes()

    with pytest.raises(ValueError) as excinfo:
        _upsert_agents_md(target, BODY)

    message = str(excinfo.value)
    assert str(target) in message
    assert _AGENTS_MD_BEGIN_MARK in message or _AGENTS_MD_END_MARK in message
    assert target.read_bytes() == before


def test_malformed_state_names_the_file_not_just_the_condition(tmp_path):
    target = _write(tmp_path / "deep" / "nest" / "AGENTS.md", f"{_AGENTS_MD_BEGIN}\nno end\n")

    with pytest.raises(ValueError, match="deep/nest/AGENTS.md"):
        _upsert_agents_md(target, BODY)


def test_parent_directories_are_created_for_a_new_file(tmp_path):
    target = tmp_path / ".config" / "opencode" / "AGENTS.md"

    _upsert_agents_md(target, BODY)

    assert target.is_file()


def test_marker_detection_ignores_attributes_on_the_begin_line(tmp_path):
    # Detection keys on the bare `stash-plugin:begin` substring, so a block
    # stamped by any version is found by the same codepath that finds this one.
    older = f"<!-- {_AGENTS_MD_BEGIN_MARK} guidance_version=0.0.1 -->\nOld.\n{_AGENTS_MD_END}\n"
    target = _write(tmp_path / "AGENTS.md", older)

    assert _upsert_agents_md(target, BODY) == "refreshed"

    assert target.read_text() == f"{_AGENTS_MD_BEGIN}\n{BODY}\n{_AGENTS_MD_END}\n"


# --- the target table both write paths share (STAS-248 Step 2) ---


def test_target_table_owns_exactly_the_product_guidance_files():
    assert [agent for agent, _ in _guidance_targets()] == TABLE_AGENTS


@pytest.mark.parametrize("agent", TABLE_AGENTS)
def test_installer_writes_at_the_table_destination(agent, monkeypatch, tmp_path):
    # The table is only a single source of truth if the installers actually write
    # where it says. An installer that kept its own old destination would leave
    # `guidance refresh` repairing a file that agent never reads.
    _fake_home(monkeypatch, tmp_path)

    _run_installer(agent, monkeypatch)

    assert _guidance_dest(agent).is_file()


@pytest.mark.parametrize("agent", TABLE_AGENTS)
def test_refresh_finds_installer_output_current(agent, monkeypatch, tmp_path):
    # Drift guard in the other direction: install and refresh must compute the
    # same bytes from the same source, or every `connect` would immediately look
    # stale to the next refresh and the two paths would fight forever.
    _fake_home(monkeypatch, tmp_path)
    _run_installer(agent, monkeypatch)

    assert _refresh_installed_guidance()[agent] == "unchanged"


def test_refresh_reports_an_absent_destination_absent_without_creating_it(monkeypatch, tmp_path):
    # Absent means absent: the refresh repairs installs, it does not perform them.
    home = _fake_home(monkeypatch, tmp_path)

    statuses = _refresh_installed_guidance()

    assert statuses == dict.fromkeys(TABLE_AGENTS, "absent")
    assert [dest for _, dest in _guidance_targets() if dest.exists()] == []
    assert home.exists()


def test_refresh_repairs_every_stale_block_then_reports_unchanged(monkeypatch, tmp_path):
    _fake_home(monkeypatch, tmp_path)
    for _, dest in _guidance_targets():
        _write(dest, f"# My own rules\n\n{STALE_BLOCK}\n\nKeep me.\n")

    assert _refresh_installed_guidance() == dict.fromkeys(TABLE_AGENTS, "refreshed")

    for agent, dest in _guidance_targets():
        text = dest.read_text()
        assert text.startswith("# My own rules\n\n")
        assert text.endswith("\n\nKeep me.\n")
        assert "Old guidance." not in text
        assert _guidance_body(agent).rstrip() in text
    assert _refresh_installed_guidance() == dict.fromkeys(TABLE_AGENTS, "unchanged")


def test_refresh_leaves_an_absent_target_alongside_refreshed_ones(monkeypatch, tmp_path):
    home = _fake_home(monkeypatch, tmp_path)
    pi_dest = _guidance_dest("pi")
    _write(pi_dest, f"{STALE_BLOCK}\n")
    _write(_guidance_dest("gemini"), f"{STALE_BLOCK}\n")

    statuses = _refresh_installed_guidance()

    assert statuses["pi"] == "refreshed"
    assert statuses["gemini"] == "refreshed"
    assert statuses["codex"] == "absent"
    assert not _guidance_dest("codex").exists()
    assert pi_dest.read_text().startswith(_AGENTS_MD_BEGIN)
    assert home.exists()


def test_refresh_propagates_malformed_state_naming_the_offending_file(monkeypatch, tmp_path):
    # The command layer turns this into a non-zero exit; what matters here is that
    # one unreadable block stops the whole refresh instead of being skipped quietly
    # — a half-repaired install is the state this task exists to end.
    _fake_home(monkeypatch, tmp_path)
    broken = _write(_guidance_dest("codex"), f"{_AGENTS_MD_BEGIN}\nno end\n")

    with pytest.raises(ValueError, match="AGENTS.md") as excinfo:
        _refresh_installed_guidance()

    assert str(broken) in str(excinfo.value)


def test_a_shipped_guidance_asset_that_is_missing_fails_loud(monkeypatch, tmp_path):
    # The old installers wrapped every write in `if asset.exists()`, so an asset
    # missing from the wheel reported a green row while writing no guidance at all
    # — the exact silence this task removes.
    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: tmp_path / "empty" / agent)

    with pytest.raises(ValueError, match="pi") as excinfo:
        _guidance_body("pi")

    assert "incomplete" in str(excinfo.value)


# --- the automatic seam (STAS-248 Step 4) ------------------------------------
#
# The refresh command only converges guidance if something runs it. The one
# place that provably runs on an installed machine is `stash hook run <agent>
# on_session_start`: the hook command string is byte-identical across upgrades
# (that is what keeps Codex's hook trust), so it is the installed package's own
# code executing before any configuration or plugin state is read. These tests
# pin the seam to that entry point and to that event alone.


def _neutralise_scripts(monkeypatch) -> None:
    """Stop hook_run from executing a real adapter script — the seam is what's
    under test here, and a real script would block on an empty stdin."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "runpy", types.SimpleNamespace(run_path=lambda *a, **k: None))


def _seam_calls(monkeypatch) -> list:
    seen: list = []
    monkeypatch.setattr(
        "cli.main.spawn_guidance_refresh_if_stale",
        lambda dests, version: seen.append((dests, version)),
    )
    return seen


def test_the_session_start_hook_asks_for_a_refresh(monkeypatch):
    import cli.main as main

    calls = _seam_calls(monkeypatch)
    _neutralise_scripts(monkeypatch)

    main.hook_run("codex", "on_session_start")

    assert len(calls) == 1
    dests, version = calls[0]
    # The seam passes the same table the installers write through, so the set of
    # files that can converge can never differ between the two paths.
    assert dests == [dest for _, dest in _guidance_targets()]
    assert version == main.__version__


def test_events_other_than_session_start_do_not_trigger_a_refresh(monkeypatch):
    import cli.main as main

    calls = _seam_calls(monkeypatch)
    _neutralise_scripts(monkeypatch)

    for event in ("on_prompt", "on_tool_use", "on_stop"):
        main.hook_run("codex", event)

    # A spawn per tool call would be churn on the hot path of every session; the
    # guidance an agent reads is loaded at session start, so that is the only
    # moment a rewrite can still be picked up.
    assert calls == []


def test_the_seam_runs_before_the_agent_payload_is_read(monkeypatch, tmp_path):
    import cli.main as main

    _fake_home(monkeypatch, tmp_path)
    order: list[str] = []
    monkeypatch.setattr(
        "cli.main.spawn_guidance_refresh_if_stale",
        lambda dests, version: order.append("seam"),
    )
    import sys
    import types

    def read_the_payload(*args, **kwargs):
        order.append("payload")

    monkeypatch.setitem(sys.modules, "runpy", types.SimpleNamespace(run_path=read_the_payload))

    main.hook_run("codex", "on_session_start")

    # Order matters: an installed generation reads its stdin to build a session
    # record. If the refresh ran after that, a hook that blocks or dies on
    # malformed input would silently take the convergence down with it.
    assert order == ["seam", "payload"]
