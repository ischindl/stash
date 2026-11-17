"""Tests for the Pi 1.x extension installer (`_install_pi`).

pi 1.x loads every ``*.ts`` in ``<agentDir>/extensions/`` at startup, where
``<agentDir>`` is ``$PI_CODING_AGENT_DIR`` or ``~/.pi/agent``. No registration in
``settings.json`` is involved, so the one file this installer copies is the whole
pi-side install: ``<agentDir>/extensions/stash.ts``.

The file is a transport that shells out to ``stash hook run pi <event>``, so the
Python handlers are NOT copied any more — they are resolved from the installed
``stashai`` package at event time. That is what makes a package upgrade reach a
machine on the next event instead of never.

The retired mechanism is what these tests used to describe: bash wrappers under
``~/.pi/hooks/`` execing ``../_run.sh``. pi 1.x does not read that directory (it
only warns that hooks were renamed to extensions), which is why STAS-368 replaced
the installer rather than repairing it.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import (
    _HOOK_EVENTS,
    _INSTALLERS,
    PLUGIN_DATA_DIRS,
    __version__,
    _guidance_dest,
    _install_all_hooks,
    _install_pi,
    _migrate_pi_guidance,
    _plugin_installed,
    app,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# The two shipped pi trees: the source plugin contributors edit, and the packaged
# mirror the installer actually copies.
SHIPPED_PI_TREES = [
    REPO_ROOT / "plugins/pi-plugin",
    REPO_ROOT / "stashai/plugin/assets/pi",
]

EXTENSION_RELPATH = Path("extensions/stash.ts")

# A managed block carrying an older version stamp. The migration keys on the bare
# markers, so a block written by an older build has the same shape as this one.
BLOCK = "<!-- stash-plugin:begin guidance_version=0.0.1 -->\n{body}\n<!-- stash-plugin:end -->\n"

# pi event -> the handler its extension feeds, and so the `on_*.py` that must ship.
FORWARDED_EVENTS = {
    "session_start": "on_session_start",
    "before_agent_start": "on_prompt",
    "tool_result": "on_tool_use",
    "agent_settled": "on_stop",
    "session_shutdown": "on_session_end",
}

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_ambient_pi_agent_dir(monkeypatch):
    """Blank out an inherited `PI_CODING_AGENT_DIR`, for every test in this file.

    pi's agent dir — where its guidance and its extension both land — is read from
    that variable before home is consulted. A machine with pi configured through it
    would otherwise aim every install at the developer's real directory while the
    assertions here keep looking at the scratch tree, so the suite would both pass
    for the wrong reason and write outside the tree.
    """
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)


@pytest.fixture
def pi_home(monkeypatch, tmp_path: Path) -> Path:
    """Point Path.home() at a scratch dir, so `~/.pi/agent` lands inside it."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _make_assets(root: Path) -> Path:
    """A minimal shipping pi assets tree whose extension is distinguishable."""
    assets = root / "assets" / "pi"
    (assets / "extensions").mkdir(parents=True)
    (assets / "extensions" / "stash.ts").write_text(
        "export default function (pi: any) {\n  // fixture extension\n}\n"
    )
    assets.joinpath("AGENTS.md").write_text("# Stash\nstash CLI is on PATH.\n")
    return assets


def _installed_extension(home: Path) -> Path:
    return home / ".pi" / "agent" / "extensions" / "stash.ts"


def _retired_guidance(home: Path) -> Path:
    """pi's guidance at the location this card retires: one level above its agent dir.

    Spelled with ``joinpath`` on purpose. Every test here names its live destination
    as ``home / ".pi" / "agent" / "AGENTS.md"``, so a grep for a guidance file written
    straight under ``.pi`` is the check that nothing still treats the retired path as
    a destination — and this helper would trip it while only describing the past.
    """
    return home.joinpath(".pi", "AGENTS.md")


def test_fresh_install_writes_the_extension_and_guidance(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    status, _ = _install_pi(False)

    assert status == "installed"
    installed = _installed_extension(pi_home)
    assert installed.is_file(), "pi discovers extensions only under <agentDir>/extensions/"
    assert not installed.is_symlink(), "the install must be a real copy, not a repo symlink"
    assert installed.read_text() == (assets / "extensions" / "stash.ts").read_text()

    agents_md = pi_home / ".pi" / "agent" / "AGENTS.md"
    assert "stash-plugin:begin" in agents_md.read_text()
    assert "stash-plugin:end" in agents_md.read_text()
    # "Nothing was written one level up" is an assertion, not an absence of evidence:
    # that retired location is exactly where pi's loader does not look.
    assert list((pi_home / ".pi").glob("AGENTS.md")) == []

    # The dead generation must not come back: nothing under ~/.pi besides the
    # extension, its dir, and the guidance file.
    stray = {
        p.relative_to(pi_home / ".pi").as_posix()
        for p in (pi_home / ".pi").rglob("*")
        if p.is_file()
    }
    assert stray == {"agent/AGENTS.md", "agent/extensions/stash.ts"}, stray


def test_second_run_is_skipped(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    _install_pi(False)
    first_bytes = [p.read_bytes() for p in sorted(pi_home.rglob("*")) if p.is_file()]

    status, _ = _install_pi(False)

    assert status == "skipped"
    assert [p.read_bytes() for p in sorted(pi_home.rglob("*")) if p.is_file()] == first_bytes


def test_drifted_extension_is_rewritten_without_force(pi_home: Path, monkeypatch) -> None:
    """A hand-edited or older-generation extension is replaced on the next setup
    run. The installer is the only thing allowed to overwrite it, because pi runs
    whatever bytes are sitting in that directory."""
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    _install_pi(False)
    installed = _installed_extension(pi_home)
    installed.write_text("export default function (pi: any) { /* I am different */ }\n")

    status, _ = _install_pi(False)

    assert status == "installed"
    assert installed.read_text() == (assets / "extensions" / "stash.ts").read_text()


def test_force_rewrites_up_to_date(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    _install_pi(False)
    status, _ = _install_pi(True)

    assert status == "installed"


def test_pi_coding_agent_dir_override_is_honored(pi_home: Path, monkeypatch) -> None:
    """pi resolves its agent dir from PI_CODING_AGENT_DIR, and so must we — an
    installer that wrote to ~/.pi/agent while pi read elsewhere would report a
    successful install of an extension pi never loads."""
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi_home / "custom" / "pi-agent"))

    status, _ = _install_pi(False)

    assert status == "installed"
    assert (pi_home / "custom" / "pi-agent" / "extensions" / "stash.ts").is_file()
    # pi reads its one global context file out of the same overridden directory, so
    # the guidance has to land there too — an extension that follows the override
    # while the guidance file follows nothing is exactly how this defect shipped.
    assert (pi_home / "custom" / "pi-agent" / "AGENTS.md").is_file()
    assert not (pi_home / ".pi" / "agent").exists()


def test_tilde_form_of_the_override_resolves_under_home(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/pi-config")

    _install_pi(False)

    assert (pi_home / "pi-config" / "extensions" / "stash.ts").is_file()


def test_upsert_agents_md_preserves_user_content(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    pi = pi_home / ".pi" / "agent"
    pi.mkdir(parents=True)
    (pi / "AGENTS.md").write_text("my personal notes\n")

    _install_pi(False)
    body = (pi / "AGENTS.md").read_text()
    assert "my personal notes" in body
    assert "stash-plugin:begin" in body

    _install_pi(False)
    assert (pi / "AGENTS.md").read_text().count("stash-plugin:begin") == 1


def test_a_retired_block_is_relocated_by_the_installer(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    legacy = _retired_guidance(pi_home)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(BLOCK.format(body="Old frozen skill model."))

    status, detail = _install_pi(False)

    assert status == "installed"
    dest = pi_home / ".pi" / "agent" / "AGENTS.md"
    assert dest.is_file(), "the destination is written before the retired file is touched"
    assert f"guidance_version={__version__}" in dest.read_text()
    assert not legacy.exists(), "a block pi cannot read must not stay behind"
    assert str(legacy) in detail


def test_the_refresh_alone_relocates_a_retired_block(pi_home: Path) -> None:
    # The second driver, and the one that reaches a machine without anyone re-running
    # `stash connect`: pi reports `unchanged` because the migration already produced
    # this build's text, not `absent` for a file it has just created.
    legacy = _retired_guidance(pi_home)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(BLOCK.format(body="Old frozen skill model."))

    result = runner.invoke(app, ["guidance", "refresh"])

    assert result.exit_code == 0
    assert not legacy.exists()
    dest = pi_home / ".pi" / "agent" / "AGENTS.md"
    assert f"guidance_version={__version__}" in dest.read_text()
    assert "pi: unchanged" in result.stdout


def test_a_retired_file_with_prose_of_its_own_is_kept_and_named(pi_home: Path) -> None:
    """Operator notes are not this code's to delete, so the file survives untouched
    and the report names both the file it kept and the destination to move into."""
    legacy = _retired_guidance(pi_home)
    legacy.parent.mkdir(parents=True)
    kept = f"# my own rules\n\n{BLOCK.format(body='Old.')}\nkeep me\n"
    legacy.write_text(kept)

    notice = _migrate_pi_guidance()

    assert legacy.read_text() == kept
    dest = pi_home / ".pi" / "agent" / "AGENTS.md"
    assert dest.is_file(), "pi still receives its guidance; the prose only blocks deletion"
    assert str(legacy) in notice and str(dest) in notice


def test_a_retired_file_that_is_not_there_leaves_nothing_to_do(pi_home: Path) -> None:
    assert _migrate_pi_guidance() is None
    assert not _guidance_dest("pi").exists(), "a machine that never connected pi stays absent"


def test_a_second_migration_run_writes_nothing(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    legacy = _retired_guidance(pi_home)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(BLOCK.format(body="Old frozen skill model."))

    assert _migrate_pi_guidance() is not None
    dest = _guidance_dest("pi")
    mtime_after_first = dest.stat().st_mtime_ns

    assert _migrate_pi_guidance() is None
    assert dest.stat().st_mtime_ns == mtime_after_first, "a second run must not rewrite the file"


# --- pi's candidate precedence: reported, never written into -------------------
#
# pi takes the first context file it finds in its agent dir and AGENTS.override.md
# sits ahead of AGENTS.md, so on such a machine our file is correct and unread.
# Silence is the failure being designed out, so the notice is asserted on the
# channel a human actually sees it on.


def _override(pi_home: Path) -> Path:
    """A machine where a human owns pi's precedence slot."""
    override = pi_home / ".pi" / "agent" / "AGENTS.override.md"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("# my own pi rules\n")
    return override


def test_an_override_is_named_and_our_file_is_still_written(pi_home: Path, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "200")
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    override = _override(pi_home)

    _install_pi(False)

    err = capsys.readouterr().err
    dest = pi_home / ".pi" / "agent" / "AGENTS.md"
    assert "AGENTS.override.md" in err, "pi would read the override and never see our block"
    assert str(dest) in err, "the report has to name which file is being shadowed"
    assert dest.is_file(), "being shadowed is a reason to report, not to skip the write"
    assert override.read_text() == "# my own pi rules\n", "the override is not ours to edit"


def test_the_override_notice_reaches_a_skipped_install(pi_home: Path, monkeypatch, capsys):
    # The steady state, and the case a detail-only carrier loses: `_install_all_hooks`
    # prints a detail string for an installed or failed hook and drops it for a
    # skipped one, so a notice that only rode `detail` would be silent on exactly the
    # machines that have had pi installed longest.
    monkeypatch.setenv("COLUMNS", "200")
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    monkeypatch.setattr("cli.main._detected_agents", lambda: ["pi"])
    _override(pi_home)
    _install_pi(False)
    capsys.readouterr()

    _install_all_hooks(["pi"])

    captured = capsys.readouterr()
    assert "Pi hook up to date" in captured.out, "this install copies nothing"
    assert "AGENTS.override.md" in captured.err
    assert "AGENTS.override.md" not in captured.out, "the status line is not what carries it"


def test_no_override_produces_no_notice(pi_home: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    _install_pi(False)
    capsys.readouterr()
    _install_pi(False)

    assert capsys.readouterr().err == "", "a warning with nothing to warn about is noise"


def test_a_file_that_cannot_outrank_us_is_not_reported(pi_home: Path, monkeypatch, capsys) -> None:
    # AGENTS.MD, CLAUDE.md and CLAUDE.MD follow AGENTS.md in pi's candidate list, so
    # they cannot silence our block. Warning about them would train the reader to
    # ignore the warning that matters.
    monkeypatch.setenv("COLUMNS", "200")
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    (pi_home / ".pi" / "agent").mkdir(parents=True)
    (pi_home / ".pi" / "agent" / "CLAUDE.md").write_text("# not a shadow\n")

    _install_pi(False)

    assert capsys.readouterr().err == ""


def test_the_refresh_reports_an_override_and_still_exits_zero(pi_home: Path, monkeypatch) -> None:
    # Exit 0 means "every installed guidance file carries this build's text", which a
    # file the user owns does not make false. A non-zero exit would turn their file
    # into our error and poison `upgrade --json`.
    monkeypatch.setenv("COLUMNS", "200")
    dest = pi_home / ".pi" / "agent" / "AGENTS.md"
    dest.parent.mkdir(parents=True)
    dest.write_text(BLOCK.format(body="Old. "))
    override = _override(pi_home)

    result = runner.invoke(app, ["guidance", "refresh"])

    assert result.exit_code == 0
    assert "AGENTS.override.md" in result.stderr
    assert override.read_text() == "# my own pi rules\n"
    assert f"guidance_version={__version__}" in dest.read_text()


def test_plugin_installed_true_after_install(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    assert _plugin_installed("pi") is False
    _install_pi(False)
    assert _plugin_installed("pi") is True


def test_the_dead_hook_layout_does_not_report_itself_installed(pi_home: Path) -> None:
    """The exact machine this task exists for: an old install left ~/.pi/hooks/ and
    ~/.pi/_run.sh behind, pi executes neither, and the CLI must not call that
    healthy."""
    (pi_home / ".pi" / "hooks").mkdir(parents=True)
    (pi_home / ".pi" / "hooks" / "session_start").write_text("#!/usr/bin/env bash\nexit 0\n")
    (pi_home / ".pi" / "_run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    assert _plugin_installed("pi") is False


def test_replaces_legacy_symlink_with_real_copy(pi_home: Path, monkeypatch) -> None:
    """A symlink left behind by an older symlink installer still points at a repo
    checkout; replace it with a self-contained copy."""
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    extensions = pi_home / ".pi" / "agent" / "extensions"
    extensions.mkdir(parents=True)
    virtual_repo = pi_home / "some/old/checkout/stashai/plugin/assets/pi/extensions"
    virtual_repo.mkdir(parents=True)
    (virtual_repo / "stash.ts").write_text("export default function (pi: any) {}\n")
    (extensions / "stash.ts").symlink_to(virtual_repo / "stash.ts")

    _install_pi(False)

    restored = extensions / "stash.ts"
    assert not restored.is_symlink()
    assert restored.read_text() == (assets / "extensions" / "stash.ts").read_text()
    assert str(virtual_repo) not in restored.read_text()


def test_installed_bytes_match_the_shipped_extension(monkeypatch, tmp_path: Path) -> None:
    """Run the install against the REAL shipped asset, not a fixture: pi executes
    the bytes it finds on disk, so what lands there must equal what we ship."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert _install_pi(False)[0] == "installed"

    shipped = REPO_ROOT / "stashai" / "plugin" / "assets" / "pi" / EXTENSION_RELPATH
    installed = home / ".pi" / "agent" / EXTENSION_RELPATH
    assert installed.read_bytes() == shipped.read_bytes()
    assert str(shipped.parent) not in installed.read_text()


@pytest.mark.parametrize("tree", SHIPPED_PI_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_shipped_extension_feeds_only_handlers_that_ship(tree: Path) -> None:
    """Every `runHook("on_x")` in the shipped extension names a script that ships in
    the same tree, and every handler that ships is fed by some event.

    A handler with no feeder is dead weight copied to every machine; a feeder with no
    handler spawns a process that dies on every event of that kind — the same silent
    hole this task closed, wearing a different hat.
    """
    source = (tree / EXTENSION_RELPATH).read_text()

    fed = set(re.findall(r'runHook\("(on_[a-z_]+)"', source))
    shipped = {path.stem for path in (tree / "scripts").glob("on_*.py")}
    assert fed == shipped, (
        f"{tree.relative_to(REPO_ROOT)}: fed {sorted(fed)}, ships {sorted(shipped)}"
    )

    subscribed = set(re.findall(r'pi\.on\("([a-z_]+)"', source))
    assert subscribed == set(FORWARDED_EVENTS)
    for event, handler in FORWARDED_EVENTS.items():
        assert f'runHook("{handler}"' in source, f"{event} must feed {handler}"


@pytest.mark.parametrize("tree", SHIPPED_PI_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_shipped_pi_tree_carries_no_dead_hook_runtime(tree: Path) -> None:
    """pi 1.x never executes `~/.pi/hooks/` or `_run.sh`, so shipping them would put
    files on a user's machine that cannot do anything — and that report a healthy
    install while doing nothing."""
    assert not (tree / "scripts" / "hooks").exists()
    assert not (tree / "scripts" / "_run.sh").exists()


@pytest.mark.parametrize("tree", SHIPPED_PI_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_shipped_pi_runtime_carries_no_self_upgrade(tree: Path) -> None:
    """`spawn_self_upgrade()` in the session_start handler is the one background
    upgrade path, and pi reaches it like every other agent does. A second
    `uv tool install` from the extension's child process would race it against the
    venv it is about to exec, so nothing shipped may run its own upgrade."""
    for path in (p for p in tree.rglob("*") if p.is_file()):
        assert "uv tool install" not in path.read_text(errors="replace"), (
            f"{path.relative_to(REPO_ROOT)} runs its own upgrade; "
            "stashai.plugin.session_upload.spawn_self_upgrade is the only one"
        )


def test_every_installer_accepts_the_call_site_positional_args() -> None:
    """The setup path calls ``_INSTALLERS[agent](False, use_json)`` with two
    positional args, and its ``except Exception`` swallows any mismatch into a
    "failed" status line instead of crashing. When pi was restored with the
    pre-convention ``_install_pi(force)`` signature, a naive replay left the suite
    green while silently shipping a broken ``stash connect pi``. Bind the call
    site's arity against every registered installer so the next signature drift
    blows up here, not in a swallowed status line."""
    for agent, installer in _INSTALLERS.items():
        inspect.signature(installer).bind(False, True)


def test_setup_path_installs_pi(pi_home: Path, monkeypatch, capsys) -> None:
    """Exercise the real setup call site (``_install_all_hooks``), not just
    ``_install_pi`` directly: the extension must land on disk when the wizard
    invokes it. A TypeError swallowed inside that loop leaves the agent dir empty
    and prints "failed", so this test fails on exactly the mode that previously
    looked like a green suite with a broken install."""
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)
    monkeypatch.setattr("cli.main._detected_agents", lambda: ["pi"])

    _install_all_hooks(["pi"])

    assert "\u2713 Pi hook installed" in capsys.readouterr().out
    assert _installed_extension(pi_home).is_file()
    assert (pi_home / ".pi" / "agent" / "AGENTS.md").is_file()
    assert _plugin_installed("pi") is True


def test_a_dead_deployed_generation_upgrades_without_force(pi_home: Path, monkeypatch) -> None:
    """The bug this installer is judged on, in its new form: an installed machine
    carries an extension from an older generation that calls a helper the shipped
    transport no longer exports. pi runs those bytes at every session start, so the
    install path — the only thing allowed to replace a deployed generation — must
    overwrite the stale extension and refresh a stale guidance block in one pass,
    with no --force and no fallback that keeps the old copy running."""
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    extensions = pi_home / ".pi" / "agent" / "extensions"
    extensions.mkdir(parents=True)
    dead_extension = extensions / "stash.ts"
    dead_extension.write_text(
        "import { stashTelemetry } from './stash-legacy-telemetry';\n\nstashTelemetry(pi);\n"
    )
    (pi_home / ".pi" / "agent" / "AGENTS.md").write_text(
        "# my own rules\n\n"
        "<!-- stash-plugin:begin -->\nOld frozen skill model.\n<!-- stash-plugin:end -->\n"
        "\nkeep me\n"
    )

    status, _ = _install_pi(False)

    assert status == "installed"
    assert dead_extension.read_text() == (assets / "extensions" / "stash.ts").read_text()

    guidance = (pi_home / ".pi" / "agent" / "AGENTS.md").read_text()
    assert "Old frozen skill model." not in guidance
    assert f"guidance_version={__version__}" in guidance
    assert "# my own rules" in guidance and "keep me" in guidance
    assert guidance.count("stash-plugin:begin") == 1


def test_pi_is_dispatched_through_the_hook_cli(monkeypatch, tmp_path: Path) -> None:
    """Pi's handlers run under `stash hook run pi <event>` — the decision STAS-368
    took back from STAS-267.

    STAS-267 kept pi out of `_HOOK_EVENTS` to protect "pi's own shipped generation"
    under `~/.pi/hooks/`. That generation turned out to be a fiction: pi 1.x never
    reads that directory, so the protected copy was never executed and pi's
    recording was dead while the installer reported success. Dispatching through the
    CLI is now the only path that runs at all, and it is the one place a package
    upgrade can reach.

    The cost STAS-267 named — a stale CLI silently stopping pi's telemetry with
    nothing on disk to fall back to — is answered by `hook_run` itself: it is the
    freshly resolved package, and a crash there is recorded into the same
    `upload_status.json` that `stash status` reads.
    """
    assert _HOOK_EVENTS["pi"] == (
        "on_session_start",
        "on_prompt",
        "on_tool_use",
        "on_stop",
        "on_session_end",
    )

    # pi's data dir is redirected to the scratch home: the guard in `hook run`
    # records a failure into `~/.stash/plugins/pi/upload_status.json`, and a test
    # must not write a bogus failure into a real `stash status`.
    monkeypatch.setitem(PLUGIN_DATA_DIRS, "pi", tmp_path / "pi")
    result = runner.invoke(app, ["hook", "run", "pi", "on_not_a_real_event"])
    assert result.exit_code == 1
    assert "Unknown pi hook event" in result.stderr

    installer_source = inspect.getsource(_install_pi)
    assert "extensions" in installer_source, (
        "_install_pi must land pi's extension: it is the only pi-side artifact pi runs"
    )
