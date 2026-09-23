"""Spawn detached background processes for session artifact upload."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from stashai.plugin.upload_status import record_upload_failure

# Global skills directory each agent loads SKILL.md folders from at session
# start. Codex, Gemini, and OpenCode all read the cross-agent ~/.agents/skills
# standard, so they converge on one synced copy. Claude Code reads its own
# ~/.claude/skills (it does not scan .agents); OpenClaw uses ~/.openclaw/skills.
# Hermes loads ~/.hermes/skills natively; ~/.agents/skills only counts if the
# user opts into skills.external_dirs, so we sync to the always-loaded dir.
# Pi scans both ~/.agents/skills and ~/.pi/agent/skills, so the cross-agent
# copy is whatever the user left there by hand — pi gets its own root.
_SKILLS_DIR_BY_CLIENT = {
    "claude_code": "~/.claude/skills",
    "codex_cli": "~/.agents/skills",
    "gemini_cli": "~/.agents/skills",
    "opencode": "~/.agents/skills",
    "openclaw": "~/.openclaw/skills",
    "hermes": "~/.hermes/skills",
    "pi": "~/.pi/agent/skills",
}

# Agents that load skills from the project alone, with no global directory to
# sync into. Cursor reads project-level .cursor/skills and nothing else.
_PROJECT_ONLY_CLIENTS = {"cursor"}


def spawn_skills_sync(cfg: dict) -> None:
    """Sync the user's skills into the agent's skills directory in the
    background, so they're loaded next session. Detached and silent — a failed
    sync must never break a session.

    An agent that names itself and has no entry in either set is a bug, not a
    no-op: silently skipping it is how pi's skills stayed unsynced for as long
    as its hook already called this. Name its root in `_SKILLS_DIR_BY_CLIENT`,
    or list it in `_PROJECT_ONLY_CLIENTS` if it truly loads skills from the
    project alone. An adapter that never named its client at all is a different
    situation and stays out of this decision."""
    client = cfg.get("client", "")
    if not client or client in _PROJECT_ONLY_CLIENTS:
        return
    skills_dir = _SKILLS_DIR_BY_CLIENT.get(client)
    if not skills_dir:
        raise ValueError(
            f"No skills directory configured for client {client!r}. Add it to "
            "_SKILLS_DIR_BY_CLIENT, or to _PROJECT_ONLY_CLIENTS if that agent "
            "loads skills from the project alone."
        )
    cmd = ["stash", "skills", "sync", "--dir", skills_dir]
    env = dict(os.environ)
    if cfg.get("api_endpoint"):
        env["STASH_URL"] = cfg["api_endpoint"]
    if cfg.get("api_key"):
        env["STASH_API_KEY"] = cfg["api_key"]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    except Exception:
        pass


def spawn_self_upgrade() -> None:
    """Background-upgrade the stashai install at session start. The hook
    scripts ship inside the package (`stash hook run` executes them), so
    upgrading the package keeps library and scripts current in lockstep.
    Detached and silent — an upgrade must never block or break a session."""
    if not shutil.which("uv"):
        return
    subprocess.Popen(
        ["uv", "tool", "install", "--quiet", "stashai@latest"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


def guidance_refresh_command() -> list[str]:
    """The argv that refreshes installed agent guidance.

    Addressed at this interpreter's own package rather than a `stash` found off
    PATH: hooks run under the package interpreter with a PATH that may not carry
    the console script, and the point of the refresh is to run the *installed*
    generation's code, which is the code this interpreter imports.
    """
    return [sys.executable, "-m", "cli.main", "guidance", "refresh"]


def _guidance_needs_refresh(dests: list[Path], version: str) -> bool:
    """Whether any installed guidance file lags `version`.

    This decides only whether to *ask* for a refresh, so it uses the cheapest
    possible probe — the version stamp every managed block carries — and leaves
    the actual comparing and writing to the command. A file that exists but
    cannot be read counts as lagging: unreadable is not proof of currentness, and
    routing it forward hands it to the one codepath that reads these files with a
    parser that fails loud and names the file.
    """
    stamp = f"guidance_version={version}".encode()
    for dest in dests:
        if not dest.is_file():
            continue
        try:
            body = dest.read_bytes()
        except OSError:
            return True
        if stamp not in body:
            return True
    return False


def spawn_guidance_refresh_if_stale(dests: list[Path], version: str) -> None:
    """Rewrite installed agent guidance in the background when it lags this build.

    `dests` is passed in because the target table belongs to the CLI and this
    module must not import it back. Detached and silent like the other
    session-start spawns: a convenience repair must never block or break the
    session it rides along with, and a repair that does not happen leaves the old
    stamp in place, so the next session start asks again. Convergence therefore
    needs no setting anybody has to keep switched on, and the version stamp that
    triggered the spawn is still on disk for `stash guidance refresh` to report.

    Deliberately *not* a seam inside an adapter script or a shipped hook script:
    a generation already deployed to `~/.pi` predates this call and can never
    reach it, which is why pi's copy ran dead for a month while its source was
    fixed. This runs in the CLI itself, which is fresh on every invocation.
    """
    if not _guidance_needs_refresh(dests, version):
        return
    try:
        subprocess.Popen(
            guidance_refresh_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        pass


def spawn_session_watcher(
    agent_pid: int,
    session_id: str,
    agent_name: str,
    base_url: str,
    api_key: str,
    cwd: str,
    data_dir: Path,
    session_row_id: str,
    transcript_path: str = "",
) -> bool:
    """Watch an agent process and finalize session artifacts after it exits."""
    script = Path(__file__).parent / "_session_watcher.py"
    try:
        subprocess.Popen(
            [
                sys.executable,
                str(script),
                str(agent_pid),
                session_id,
                agent_name,
                base_url,
                api_key,
                cwd,
                str(data_dir),
                session_row_id,
                transcript_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        return False
    return True


def spawn_session_upload(
    session_row_id: str,
    transcript_path: str,
    cwd: str,
    files_touched: list[str],
    session_id: str,
    agent_name: str,
    base_url: str,
    api_key: str,
    data_dir: Path | None = None,
) -> bool:
    script = Path(__file__).parent / "_do_session_upload.py"
    env = os.environ.copy()
    env["SESSION_FILES_TOUCHED"] = json.dumps(files_touched)
    upload_status_dir = str(data_dir or "")

    try:
        subprocess.Popen(
            [
                sys.executable,
                str(script),
                session_row_id,
                transcript_path,
                cwd,
                session_id,
                agent_name,
                base_url,
                api_key,
                upload_status_dir,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as e:
        record_upload_failure(data_dir, "artifact_spawn", e)
        return False
    return True
