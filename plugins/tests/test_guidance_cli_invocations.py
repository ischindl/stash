"""Every `stash ...` command documented in the shipped guidance must parse.

Why this test exists: `test_assets_in_sync.py` only proves `plugins/<agent>-plugin/`
and `stashai/plugin/assets/<agent>/` are byte-identical. Two identical copies of a
command the CLI rejects satisfy it, so pi shipped `stash share <session_id>` green
while the parser refused the positional at exit 2, before auth — pi agents could not
share a session at all (STAS-207). Correctness of the *documented invocations* needs
its own guard, and that is this file.

Parse-only contract: the validator resolves commands and builds click contexts, but
never invokes a command body. So this test needs no auth, no network, and no backend.

Known limit: it checks the argv shape the CLI accepts, not what a command then does
with an opaque payload. `stash vfs "cat x | sed -n '1,80p'"` parses however the quoted
shell script is spelled; the pipeline grammar itself is covered by cli/tests/test_app_vfs.py.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import typer.main

from cli.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]

INLINE_CODE = re.compile(r"`([^`]+)`")

# Fewer spans than this means extraction broke, not that the docs got shorter.
MIN_INVOCATIONS = 60

# Files the guard was written for. Losing one means the sweep silently shrank.
EXPECTED_GUIDANCE_FILES = (
    "CLAUDE.md",
    "plugins/pi-plugin/AGENTS.md",
    "plugins/codex-plugin/AGENTS.md",
    "plugins/opencode-plugin/AGENTS.md",
    "plugins/cursor-plugin/stash.mdc",
)


def _corpus() -> list[Path]:
    """Canonical plugin guidance, the shipped asset mirrors, and the repo guidance.

    Cursor ships its guidance as `.mdc`, which is markdown with a different extension;
    guarding only `.md` would leave that agent's documented forms unchecked.
    """
    guidance = ("*.md", "*.mdc")
    files = [REPO_ROOT / "CLAUDE.md"]
    for pattern in guidance:
        files += [*(REPO_ROOT / "plugins").glob(f"*-plugin/{pattern}")]
        files += [*(REPO_ROOT / "stashai" / "plugin" / "assets").rglob(pattern)]
    return sorted(files)


def _documented_spans(path: Path) -> list[str]:
    """Every inline-backtick span, plus every whole line inside a fenced block."""
    spans: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            spans.append(line)
        else:
            spans.extend(INLINE_CODE.findall(line))
    return [span.strip() for span in spans if span.strip()]


def _tokenise(span: str) -> list[str]:
    """Shell-shaped tokens, with trailing `# comments` dropped like a shell would."""
    lexer = shlex.shlex(span, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _invocations(spans: list[str]) -> list[tuple[str, list[str]]]:
    """Spans that are a `stash` command with at least one further token, minus `stash`."""
    found = []
    for span in spans:
        tokens = _tokenise(span)
        if len(tokens) > 1 and tokens[0] == "stash":
            found.append((span, tokens[1:]))
    return found


def _corpus_invocations() -> list[tuple[Path, str, list[str]]]:
    return [
        (path, span, tokens)
        for path in _corpus()
        for span, tokens in _invocations(_documented_spans(path))
    ]


ROOT_COMMAND = typer.main.get_command(app)

# The parser answers these by printing and exiting before it judges the arguments
# around them, so a documented form carrying one proves nothing about its validity.
EARLY_EXIT_FLAGS = {"--help", "--version"}


def parse_error(tokens: list[str]) -> str | None:
    """Return None when the documented tokens parse, else the parser's own message.

    Walks the group tree while the next token names a subcommand, then lets that
    command's parser judge what is left. Never invokes, so no auth or network.
    """
    command: Any = ROOT_COMMAND
    remaining = list(tokens)
    while hasattr(command, "get_command"):  # only a group resolves subcommands
        if not remaining or remaining[0].startswith("-"):
            break
        name, remaining = remaining[0], remaining[1:]
        command = command.get_command(None, name)
        if command is None:
            # click raises "No such command" only inside invoke, which we never run.
            return f"No such command {name!r}."
    if EARLY_EXIT_FLAGS & set(remaining):
        return None
    try:
        command.make_context(command.name or "stash", remaining)
    except Exception as error:
        # A bad documented form surfaces as a usage error. click spells that
        # `click.exceptions.UsageError`, but typer >= 0.25 parses through its own
        # vendored click whose usage error is not a click class — the same divergence
        # the typer pin in pyproject.toml exists to contain. Both report the reason
        # through `format_message()`, so match on that rather than on a class that
        # silently stops matching and lets every form pass. Anything else is a bug in
        # this test and must propagate instead of counting as a rejection.
        report = getattr(error, "format_message", None)
        if report is None:
            raise
        return report()
    return None


INVOCATIONS = _corpus_invocations()
FORMS: dict[str, list[str]] = {span: tokens for _, span, tokens in INVOCATIONS}
DOCUMENTED_FORMS = sorted(FORMS)


@pytest.mark.parametrize("span", DOCUMENTED_FORMS, ids=DOCUMENTED_FORMS)
def test_every_documented_invocation_parses(span: str) -> None:
    files = sorted({str(p.relative_to(REPO_ROOT)) for p, s, _ in INVOCATIONS if s == span})
    error = parse_error(FORMS[span])
    assert error is None, (
        f"Guidance documents `{span}` ({', '.join(files)}) but the CLI parser rejects it: {error}"
    )


def test_corpus_covers_the_guidance_files_that_taught_the_bug() -> None:
    covered = {str(path.relative_to(REPO_ROOT)) for path, _, _ in INVOCATIONS}
    for expected in EXPECTED_GUIDANCE_FILES:
        assert expected in covered, f"corpus lost guidance file {expected}"


def test_corpus_found_a_plausible_number_of_invocations() -> None:
    assert len(INVOCATIONS) >= MIN_INVOCATIONS, (
        f"only {len(INVOCATIONS)} documented invocations found; extraction likely broke"
    )


def test_historical_pi_form_is_rejected() -> None:
    """The guard's own bite check: the exact form STAS-207 was filed for must fail.

    If `stash share` ever gains a positional argument, this test failing is the
    deliberate alarm that the documented contract changed.
    """
    error = parse_error(_tokenise("stash share <session_id>")[1:])
    assert error is not None and "unexpected extra argument" in error, (
        f"`stash share <session_id>` was supposed to be rejected, got: {error!r}"
    )


def test_the_supported_share_forms_documented_for_pi_parse() -> None:
    for form in ("stash share", 'stash share --session "<title>"'):
        assert parse_error(_tokenise(form)[1:]) is None, form


def test_unknown_subcommand_fails_explicitly() -> None:
    assert parse_error(["frobnicate", "--loud"]) is not None


def test_the_walk_descends_into_a_subcommand_group() -> None:
    """Validating `stash skills create` requires resolving the `skills` group first.

    Asking only the root command to judge the whole token list lets nearly every form
    pass, because a group defers subcommand resolution until invoke — a guard wired
    that way is green while checking nothing, the exact failure this file exists to
    stop, so the nested rejection is asserted here on purpose.
    """
    assert (
        parse_error(["skills", "create", "<name>", "--description", "<what it holds>", "--public"])
        is None
    )
    assert parse_error(["skills", "frobnicate"]) is not None


def test_a_bare_word_span_is_not_an_invocation() -> None:
    assert _invocations(["stash"]) == []


def test_pipelines_and_escaped_quotes_survive_tokenising() -> None:
    """`vfs` takes one quoted payload; shlex must keep its inner quotes intact."""
    pipe = """stash vfs "cat '/me/README.md' | sed -n '1,80p'\""""
    assert _tokenise(pipe) == ["stash", "vfs", "cat '/me/README.md' | sed -n '1,80p'"]
    assert _tokenise('stash vfs "rg \\"query\\" /me"') == ["stash", "vfs", 'rg "query" /me']


def test_no_optional_bracket_syntax_hides_in_the_corpus() -> None:
    """`[--flag]` notation is absent from the corpus today. If it ever appears, this
    test must be taught to handle it on purpose — never a silent token filter."""
    bracketed = sorted({span for span in DOCUMENTED_FORMS if "[" in span and "]" in span})
    assert bracketed == [], f"documented forms now use bracket syntax: {bracketed}"
