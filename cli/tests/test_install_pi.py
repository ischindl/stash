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
    _install_all_hooks,
    _install_pi,
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

# pi event -> the handler its extension feeds, and so the `on_*.py` that must ship.
FORWARDED_EVENTS = {
    "session_start": "on_session_start",
    "before_agent_start": "on_prompt",
    "tool_result": "on_tool_use",
    "agent_settled": "on_stop",
    "session_shutdown": "on_session_end",
}

runner = CliRunner()


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


def test_fresh_install_writes_the_extension_and_guidance(pi_home: Path, monkeypatch) -> None:
    assets = _make_assets(pi_home)
    monkeypatch.setattr("cli.main._assets_dir", lambda agent: assets)

    status, _ = _install_pi(False)

    assert status == "installed"
    installed = _installed_extension(pi_home)
    assert installed.is_file(), "pi discovers extensions only under <agentDir>/extensions/"
    assert not installed.is_symlink(), "the install must be a real copy, not a repo symlink"
    assert installed.read_text() == (assets / "extensions" / "stash.ts").read_text()

    agents_md = pi_home / ".pi" / "AGENTS.md"
    assert "stash-plugin:begin" in agents_md.read_text()
    assert "stash-plugin:end" in agents_md.read_text()

    # The dead generation must not come back: nothing under ~/.pi besides the
    # extension, its dir, and the guidance file.
    stray = {
        p.relative_to(pi_home / ".pi").as_posix()
        for p in (pi_home / ".pi").rglob("*")
        if p.is_file()
    }
    assert stray == {"agent/extensions/stash.ts", "AGENTS.md"}, stray


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

    pi = pi_home / ".pi"
    pi.mkdir(parents=True)
    (pi / "AGENTS.md").write_text("my personal notes\n")

    _install_pi(False)
    body = (pi / "AGENTS.md").read_text()
    assert "my personal notes" in body
    assert "stash-plugin:begin" in body

    _install_pi(False)
    assert (pi / "AGENTS.md").read_text().count("stash-plugin:begin") == 1


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
    assert (pi_home / ".pi" / "AGENTS.md").is_file()
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
    (pi_home / ".pi" / "AGENTS.md").write_text(
        "# my own rules\n\n"
        "<!-- stash-plugin:begin -->\nOld frozen skill model.\n<!-- stash-plugin:end -->\n"
        "\nkeep me\n"
    )

    status, _ = _install_pi(False)

    assert status == "installed"
    assert dead_extension.read_text() == (assets / "extensions" / "stash.ts").read_text()

    guidance = (pi_home / ".pi" / "AGENTS.md").read_text()
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
