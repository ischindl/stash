"""Single source of truth for the Skill-model guidance shipped to every agent.

STAS-210 added Skill-model prose to only a third of the agent channels, and the
``prompts agent-guidance`` string told every agent to run
``stash skills create "<name>" --public --json`` — a span the CLI itself rejects
because ``--description`` is a required option. STAS-211 consolidates the block
here; ``plugins/tests/test_guidance_coverage.py`` fails if any channel in
``_SUPPORTED_AGENTS`` stops carrying it verbatim.
"""

# Anchor every channel embeds, so the coverage guard can prove each copy came
# from this module and not from a paraphrase that drifted.
SKILL_MODEL_MARKER = "<!-- stash:skill-model -->"

# Canonical forms every channel must use: the skills-create span carries the
# required --description option, and uploads are only ever shown with an
# explicit <path> and --json. The STAS-207 parse guard
# (plugins/tests/test_guidance_cli_invocations.py) rejects any guidance span
# the shipped CLI cannot actually parse.
SKILL_MODEL = (
    SKILL_MODEL_MARKER + "\n"
    "A Skill is a *special folder* — one containing a SKILL.md — holding related artifacts\n"
    "(pages, files, tables) that shares like any folder and gains a public URL when\n"
    "published. Use one when you're publishing a *collection* of related things together — a\n"
    "project writeup with its supporting files, a research thread with its sources, a session\n"
    "transcript frozen as a page plus the files it produced.\n"
    "\n"
    "A Skill is **not** a wrapper to slap on every single file you happen to share. One-item Skills\n"
    "clutter Discover and defeat the model. Pick the right tool:\n"
    "\n"
    "- Share a single file or a folder/project → `stash upload <path> --json`, hand over `app_url` (no Skill).\n"
    '- Publishing a curated bundle → `stash upload <path> --skill "<title>" --json`.\n'
    '- Creating a fresh skill → `stash skills create "<name>" --description "<what it holds>" --public --json`.\n'
    '- Share a coding session → `stash share` (this one), or `stash share --session "<title>"` for another.\n'
    "\n"
    "Run `stash prompts agent-guidance` to reprint this rule mid-session."
)
