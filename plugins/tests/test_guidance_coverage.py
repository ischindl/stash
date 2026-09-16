"""Every supported agent's guidance channel must ship the canonical Skill-model block.

STAS-210 added the guidance prose but shipped it to only a third of the agents:
`grep -ric skill plugins/gemini-plugin/GEMINI.md plugins/hermes-plugin/HERMES.md`
answered 0 for both while the commit claimed 13/13, and the `prompts
agent-guidance` string carried a `stash skills create "<name>" --public --json`
span the CLI itself rejects (no `--description`). This guard makes either
regression fail CI: one canonical block in `stashai/plugin/guidance.py`, and
every channel an agent actually loads must contain it verbatim.

The map is keyed to `cli.main._SUPPORTED_AGENTS`: an agent added to the product
table without registering its guidance channel here fails
`test_channels_are_wired_to_the_supported_agent_table`.

For the Claude session-start hook we import the shipped script module the way
the hook runtime loads it (same-file exec with the scripts dir importable),
evicting the bare `config`/`adapt` names afterwards so the runpy-based hook
tests keep resolving those names to their own plugin's modules.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from cli.main import _SUPPORTED_AGENTS, AGENT_GUIDANCE_PROMPT
from stashai.plugin.guidance import SKILL_MODEL, SKILL_MODEL_MARKER

REPO_ROOT = Path(__file__).resolve().parents[2]

CLAUDE_CANONICAL_SKILLS_CREATE = (
    'stash skills create "<name>" --description "<what it holds>" --public --json'
)
STAS_210_REJECTED_SPAN = 'stash skills create "<name>" --public --json'

_plugin_scripts = REPO_ROOT / "plugins" / "claude-plugin" / "scripts"
_hook_context: str | None = None


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text()


def _claude_hook_context() -> str:
    """The shipped hook's composed CONTEXT, imported the way the hook runtime loads it."""
    global _hook_context
    if _hook_context is None:
        # config.py builds DATA_DIR from CLAUDE_PLUGIN_DATA at import; nothing
        # touches the filesystem during import, so a placeholder path suffices.
        saved_env = os.environ.get("CLAUDE_PLUGIN_DATA")
        os.environ["CLAUDE_PLUGIN_DATA"] = str(REPO_ROOT / ".guidance-coverage-hook-data")
        sys.path.insert(0, str(_plugin_scripts))
        before = set(sys.modules)
        try:
            spec = importlib.util.spec_from_file_location(
                "_guidance_coverage_on_session_start", _plugin_scripts / "on_session_start.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _hook_context = module.CONTEXT
        finally:
            for name in set(sys.modules) - before:
                del sys.modules[name]
            sys.path.remove(str(_plugin_scripts))
            if saved_env is None:
                del os.environ["CLAUDE_PLUGIN_DATA"]
            else:
                os.environ["CLAUDE_PLUGIN_DATA"] = saved_env
    return _hook_context


def _composed(name: str) -> Callable[[], str]:
    def getter() -> str:
        import cli.main

        return getattr(cli.main, name)

    return getter


def _static(relative_path: str) -> Callable[[], str]:
    return lambda: _read(relative_path)


# The file (or composed string) each agent actually loads, per the STAS-211
# preflight channel map. Static entries are repo-relative file paths; composed
# entries are module-level strings the CLI builds at runtime.
STATIC_CHANNELS: dict[str, list[str]] = {
    "cursor": ["plugins/cursor-plugin/stash.mdc"],
    "codex": ["plugins/codex-plugin/AGENTS.md"],
    "opencode": ["plugins/opencode-plugin/AGENTS.md"],
    "gemini": ["plugins/gemini-plugin/GEMINI.md"],
    "hermes": ["plugins/hermes-plugin/HERMES.md"],
    "pi": ["plugins/pi-plugin/AGENTS.md"],
}

COMPOSED_CHANNELS: dict[str, list[tuple[str, Callable[[], str]]]] = {
    "claude": [
        ("project CLAUDE.md block (_CLAUDE_STASH_CONTEXT)", _composed("_CLAUDE_STASH_CONTEXT")),
        ("session-start hook CONTEXT", _claude_hook_context),
    ],
    "openclaw": [("~/.openclaw/workspace AGENTS.md body", _composed("_OPENCLAW_GUIDANCE"))],
}

CHANNELS: dict[str, list[tuple[str, Callable[[], str]]]] = {
    agent: [(path, _static(path)) for path in STATIC_CHANNELS.get(agent, [])]
    + COMPOSED_CHANNELS.get(agent, [])
    for agent in set(STATIC_CHANNELS) | set(COMPOSED_CHANNELS)
}


@pytest.mark.parametrize("agent", sorted(CHANNELS))
def test_every_supported_agent_channel_carries_the_skill_model_verbatim(agent: str) -> None:
    for label, getter in CHANNELS[agent]:
        text = getter()
        assert SKILL_MODEL in text, (
            f"{agent}: {label} does not ship the canonical SKILL_MODEL block verbatim "
            f"(STAS-210 symptom: guidance shipped to only a third of the agents)"
        )
        assert text.count(SKILL_MODEL) == 1, f"{agent}: {label} embeds SKILL_MODEL more than once"


def test_channels_are_wired_to_the_supported_agent_table() -> None:
    assert set(CHANNELS) == set(_SUPPORTED_AGENTS), (
        "_SUPPORTED_AGENTS and the guidance channel map disagree; every supported "
        "agent needs a registered guidance channel"
    )


def test_skill_model_block_is_self_contained_and_parse_valid() -> None:
    assert SKILL_MODEL.startswith(SKILL_MODEL_MARKER)
    assert CLAUDE_CANONICAL_SKILLS_CREATE in SKILL_MODEL
    assert "stash upload <path> --json" in SKILL_MODEL
    assert STAS_210_REJECTED_SPAN not in SKILL_MODEL


def test_runtime_agent_guidance_prompt_carries_the_block() -> None:
    assert SKILL_MODEL in AGENT_GUIDANCE_PROMPT, (
        "`stash prompts agent-guidance` still ships the pre-STAS-211 prose "
        "(rejected skills-create span / bare `stash upload`)"
    )


def test_root_claude_md_dogfoods_the_block() -> None:
    assert SKILL_MODEL in _read("CLAUDE.md"), (
        "root CLAUDE.md must carry the canonical block verbatim, not a paraphrase"
    )


@pytest.mark.parametrize(
    "relative_path",
    sorted(path for paths in STATIC_CHANNELS.values() for path in paths),
)
def test_static_channel_files_have_one_what_a_skill_is_heading(relative_path: str) -> None:
    text = _read(relative_path)
    assert text.lower().count("what a skill is") == 1, (
        f"{relative_path} must keep exactly one 'What a Skill is' heading — "
        "superseded variant sections must be replaced, not left beside the block"
    )
