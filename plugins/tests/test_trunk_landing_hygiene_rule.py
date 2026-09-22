"""The trunk landing rules must stay in the prompt every agent reads.

Why this test exists: three landings advanced the trunk's ref from outside the trunk
checkout — the reflog line carries no message, so no merge ever ran there — and each one
left that checkout's index and files sitting on the previous tip. The next session reading
`git status` there saw a huge staged revert of work that had already landed, and a
`commit -a` in that state would have landed the revert for real. The rules that prevent it
lived only in one agent's private memory notes, which the next session never loads.

Root CLAUDE.md is the prompt every agent session loads (`AGENTS.md` is a symlink to it), so
this guard pins the section's literals: a paraphrase that drops a clause fails here. The
section must sit above the `<!-- stash-context -->` marker, because everything from that
marker down is machine-owned — the CLI's guidance updater regenerates it — and a rule
placed inside it is not ours to keep.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

RULE_HEADING = "### Landing hygiene"
MANAGED_BLOCK_MARKER = "<!-- stash-context -->"

# Each literal pins one behaviour a landing agent must be told: no ref write from elsewhere,
# merge inside the trunk, verify both sides, one merge not a copy, STOP, and the only
# restore form that is not a silent no-op.
REQUIRED_LITERALS = (
    "card work never happens in the trunk checkout",
    "Never advance the trunk ref from a linked worktree",
    "`git merge --ff-only <sha>` run inside the trunk checkout",
    "`status --porcelain` must print nothing on BOTH sides",
    "empty AND its `write-tree` equals `HEAD^{tree}`",
    "never a bare `commit -a`",
    "STOP-and-report condition",
    "explicit `--source=HEAD`",
)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _landing_hygiene_section(text: str) -> str:
    """The rule's own body: from its heading up to the next heading or the managed block."""
    start = text.index(RULE_HEADING) + len(RULE_HEADING)
    ends = [
        candidate
        for candidate in (text.find("\n### ", start), text.find(MANAGED_BLOCK_MARKER, start))
        if candidate != -1
    ]
    return text[start : min(ends) if ends else len(text)]


def test_landing_hygiene_rule_keeps_every_clause() -> None:
    text = _read("CLAUDE.md")
    assert RULE_HEADING in text, (
        "root CLAUDE.md lost its `### Landing hygiene` section — it is the only place these "
        "rules reach every landing agent, and AGENTS.md is just a symlink to this file"
    )
    section = _landing_hygiene_section(text)
    for literal in REQUIRED_LITERALS:
        assert literal in section, (
            f"`### Landing hygiene` no longer states `{literal}` — paraphrasing it away "
            "re-opens the STAS-244 phantom: a trunk checkout whose ref moved without its "
            "index and files following"
        )


def test_landing_hygiene_rule_sits_above_the_machine_owned_block() -> None:
    text = _read("CLAUDE.md")
    assert MANAGED_BLOCK_MARKER in text, (
        "the `<!-- stash-context -->` marker is gone; this guard locates the machine-owned "
        "tail by it and cannot verify placement without it"
    )
    assert text.index(RULE_HEADING) < text.index(MANAGED_BLOCK_MARKER), (
        "`### Landing hygiene` moved into or below the managed block, which the CLI's "
        "guidance updater rewrites — the rule would not survive the next `stash` install"
    )
