"""Shared config helpers for per-agent Stash plugins.

Every CLI-style agent plugin (claude, codex, cursor, gemini, opencode,
openclaw) piggybacks on the user-scoped CLI config in
`~/.stash/config.json`. The per-agent `config.py` modules used to be
near-identical copies of this logic; they now delegate here and only
supply the things that actually differ between agents: the data-dir
env var, the data-dir default path, the `client` label sent with
each event, and (claude only) the `session_link` opt-in this reader
does not carry.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .stash_client import StashClient

PRODUCTION_BASE_URL = "https://api.joinstash.ai"


def data_dir_from_env(env_var: str, default_subpath: str) -> Path:
    return Path(os.environ.get(env_var, Path.home() / default_subpath))


def get_stdin_data() -> dict:
    """Parse the agent's one-JSON-object payload from stdin, or say what arrived.

    The payload is a required input, so an absent one is a broken transport and the
    only useful fact about it is what actually landed on stdin. This used to return
    `{}` for anything unparseable, which made an absent payload report itself as
    "keys present: []" - the message a payload that genuinely WAS an empty object
    produces - so no reader could tell a broken transport from an empty event, and a
    fabricated failure count could not be traced to whoever sent it (STAS-417).

    An empty object is still returned: `{}` is valid JSON, and refusing a payload
    without a `session_id` belongs to the adapter, not here. One validator.
    """
    raw = sys.stdin.read()
    if not raw:
        raise ValueError(
            "stash hook run: stdin carried 0 bytes; the agent transport must pipe one "
            "JSON payload object"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"stash hook run: stdin is not valid JSON ({len(raw)} bytes, starts "
            f"{raw[:40]!r}): {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ValueError(
            f"stash hook run: stdin payload must be a JSON object, got {type(payload).__name__}"
        )
    return payload


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def cli_config() -> dict:
    """User-scoped CLI config (`~/.stash/config.json`)."""
    user_path = Path.home() / ".stash" / "config.json"
    if user_path.exists():
        return _read_json(user_path)
    return {}


def get_config(client: str) -> dict:
    cli = cli_config()
    return {
        "api_endpoint": cli.get("base_url", PRODUCTION_BASE_URL),
        "api_key": cli.get("api_key", ""),
        "agent_name": cli.get("username", ""),
        "stopped_streaming": bool(cli.get("stopped_streaming")),
        "client": client,
    }


def get_client(client: str, data_dir: Path) -> StashClient:
    cfg = get_config(client)
    return StashClient(base_url=cfg["api_endpoint"], api_key=cfg["api_key"], data_dir=data_dir)


def is_configured(client: str) -> bool:
    cfg = get_config(client)
    return bool(cfg["api_key"] and cfg["agent_name"])
