"""Claude Code plugin config. Reads from ~/.stash/config.json (CLI config).

Shared logic lives in `stashai.plugin.agent_config`; this module only
supplies the per-agent constants plus the claude-only `session_link`
opt-in (`stash settings` → Session link) that the shared reader does
not carry. No plugin-env fast path: the hooks run via
`stash hook run claude <event>` from the merged ~/.claude/settings.json,
so they must work identically wherever Claude Code launches them.
"""

from __future__ import annotations

from stashai.plugin import agent_config
from stashai.plugin.agent_config import get_stdin_data
from stashai.plugin.stash_client import StashClient

_CLIENT = "claude_code"
DATA_DIR = agent_config.data_dir_from_env("STASH_CLAUDE_DATA", ".stash/plugins/claude")

__all__ = ["DATA_DIR", "get_client", "get_config", "get_stdin_data", "is_configured"]


def get_config() -> dict:
    cfg = agent_config.get_config(_CLIENT)
    cfg["session_link"] = bool(agent_config.cli_config().get("session_link"))
    return cfg


def get_client() -> StashClient:
    return agent_config.get_client(_CLIENT, DATA_DIR)


def is_configured() -> bool:
    return agent_config.is_configured(_CLIENT)
