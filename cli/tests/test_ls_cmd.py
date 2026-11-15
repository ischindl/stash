"""`stash ls` is the demo-facing answer to "what do you have access to": the
overview must show every source as a directory (integrations included), and
drilling into a path must collapse the backend's recursive prefix listing into
one directory level — otherwise the output reads as a dump, not a filesystem."""

import json

import pytest
import typer

from cli import main

SOURCES = [
    {"source": "files", "type": "native_files", "display_name": "Files", "tree": []},
    {
        "source": "sessions",
        "type": "native_sessions",
        "display_name": "Session transcripts",
        "tree": [{"name": "claude", "kind": "session", "ref": "s1"}],
    },
    {
        "source": "github",
        "type": "provider",
        "provider": "github",
        "display_name": "github",
        "members": [{"handle": "11111111-1111-1111-1111-111111111111", "display_name": "stash"}],
        "sync_status": "idle",
        "tree": [
            {
                "name": "docs",
                "kind": "folder",
                "children": [
                    {"name": "api.md", "kind": "file", "path": "docs/api.md"},
                    {"name": "", "kind": "truncated", "hidden": 7},
                ],
            }
        ],
    },
    {
        "source": "gong",
        "type": "provider",
        "provider": "gong",
        "display_name": "gong",
        "members": [{"handle": "22222222-2222-2222-2222-222222222222", "display_name": "Gong"}],
        "sync_status": "failed",
        "tree": [],
    },
]


class FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def sources_tree(self, depth=3):
        return SOURCES

    def list_source_entries(self, source, path=""):
        assert source == "11111111-1111-1111-1111-111111111111"
        return [
            {"path": "docs/api.md", "name": "api.md", "kind": "file"},
            {"path": "docs/guides/intro.md", "name": "intro.md", "kind": "file"},
            {"path": "docs2/other.md", "name": "other.md", "kind": "file"},
        ]


def _setup(monkeypatch):
    monkeypatch.setattr(main, "_require_auth", lambda: {"api_key": "k"})
    monkeypatch.setattr(main, "_client", lambda: FakeClient())
    monkeypatch.setattr(main.telemetry, "record", lambda *a, **k: None)


def test_ls_overview_shows_every_source_as_a_directory(monkeypatch, capsys):
    _setup(monkeypatch)

    main.ls_cmd(path="", depth=2, as_json=False)

    out = capsys.readouterr().out
    assert "files/" in out
    assert "sessions/" in out
    # The github repo nests under its provider folder, not at the top level.
    assert "github/" in out
    assert "gong/" in out
    assert "api.md" in out
    assert "+7 more" in out
    assert "sync failed" in out


def test_ls_path_collapses_prefix_listing_to_one_level(monkeypatch, capsys):
    _setup(monkeypatch)

    main.ls_cmd(path="github/docs", depth=2, as_json=False)

    out = capsys.readouterr().out
    assert "api.md" in out
    assert "guides/" in out
    # Recursive descendants and sibling prefix matches must not leak in.
    assert "intro.md" not in out
    assert "other.md" not in out


def test_ls_unknown_source_fails_loudly(monkeypatch, capsys):
    _setup(monkeypatch)

    try:
        main.ls_cmd(path="nope", depth=2, as_json=False)
    except Exception as e:
        assert type(e).__name__ == "Exit"
    captured = capsys.readouterr()
    assert "No source named 'nope'" in captured.err
    assert "No source named 'nope'" not in captured.out


# A provider with several connections: the first path segment selects the
# connection (repo), and reads must hit that connection's handle — never the
# provider key, which is not a readable source.
MULTI_SOURCES = [
    {
        "source": "github",
        "type": "provider",
        "provider": "github",
        "display_name": "github",
        "members": [
            {"handle": "aaaa", "display_name": "stash"},
            {"handle": "bbbb", "display_name": "plugin"},
        ],
        "tree": [
            {"name": "plugin", "kind": "folder", "source": "bbbb", "children": []},
            {"name": "stash", "kind": "folder", "source": "aaaa", "children": []},
        ],
    },
]


class MultiClient(FakeClient):
    def sources_tree(self, depth=3):
        return MULTI_SOURCES

    def list_source_entries(self, source, path=""):
        assert source == "aaaa"
        assert path == "docs"
        return [{"path": "docs/api.md", "name": "api.md", "kind": "file"}]


def test_ls_provider_lists_its_connections(monkeypatch, capsys):
    _setup(monkeypatch)
    monkeypatch.setattr(main, "_client", lambda: MultiClient())

    main.ls_cmd(path="github", depth=2, as_json=False)

    out = capsys.readouterr().out
    assert "stash/" in out
    assert "plugin/" in out


def test_ls_drills_into_a_named_connection(monkeypatch, capsys):
    _setup(monkeypatch)
    monkeypatch.setattr(main, "_client", lambda: MultiClient())

    main.ls_cmd(path="github/stash/docs", depth=2, as_json=False)

    out = capsys.readouterr().out
    assert "api.md" in out


# --- A provider that ran past the server's budget is named, not hidden -------
#
# The server bounds its own work and returns a tree whose straggler carries a
# `listing_error` sentence. Two things are promised on this side of that wire:
# the sentence reaches the human (stderr, beside a tree that says it is short),
# and a short answer stays a successful answer — an agent that asked twice must
# not pay two failed turns for one slow integration.

STRAGGLER = "github: acme/plugin did not answer within the 10 s listing budget"


def _gapped(**by_provider: str) -> list[dict]:
    """The standard fixtures with named providers reporting what they missed."""
    return [
        {**s, "listing_error": by_provider[s["source"]]} if s["source"] in by_provider else s
        for s in SOURCES
    ]


class GapClient(FakeClient):
    def __init__(self, sources):
        self.sources = sources

    def sources_tree(self, depth=3):
        return self.sources


def _gap_setup(monkeypatch, sources):
    _setup(monkeypatch)
    monkeypatch.setattr(main, "_client", lambda: GapClient(sources))


def test_ls_names_the_provider_that_did_not_answer(monkeypatch, capsys):
    _gap_setup(monkeypatch, _gapped(github=STRAGGLER))

    main.ls_cmd(path="", depth=2, as_json=False)

    captured = capsys.readouterr()
    assert STRAGGLER in captured.err
    assert "listing incomplete" in captured.out
    # The providers that answered are still rendered in full.
    assert "api.md" in captured.out


def test_an_incomplete_listing_is_still_a_successful_command(monkeypatch, capsys):
    """Exit 2 twice is what ate the curator's wall. A listing that says what it
    is missing is an answer, and it leaves 0 behind."""
    _gap_setup(monkeypatch, _gapped(github=STRAGGLER))

    try:
        main.ls_cmd(path="", depth=2, as_json=False)
    except typer.Exit as e:  # pragma: no cover - the failing path of this test
        pytest.fail(f"a short listing must exit 0, got exit {e.exit_code}")


def test_json_ls_carries_the_gap_on_stdout_and_the_warning_on_stderr(monkeypatch, capsys):
    """An agent parsing `--json` gets the gap in the payload it parses, and the
    same sentence on stderr where a human watching the turn also sees it."""
    _gap_setup(monkeypatch, _gapped(github=STRAGGLER))

    main.ls_cmd(path="", depth=2, as_json=True)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    github = next(s for s in payload["sources"] if s["source"] == "github")
    assert github["listing_error"] == STRAGGLER
    assert STRAGGLER in captured.err


def test_drilling_into_a_source_names_only_that_source(monkeypatch, capsys):
    _gap_setup(monkeypatch, _gapped(github=STRAGGLER, gong="gong: Gong missed the budget"))

    main.ls_cmd(path="github", depth=2, as_json=False)

    captured = capsys.readouterr()
    assert STRAGGLER in captured.err
    assert "gong" not in captured.err
    # The provider still answers for the part it can.
    assert "docs/" in captured.out


def test_a_source_that_answered_says_nothing_about_a_gap(monkeypatch, capsys):
    _gap_setup(monkeypatch, SOURCES)

    main.ls_cmd(path="", depth=2, as_json=False)

    captured = capsys.readouterr()
    assert "listing incomplete" not in captured.out
    assert "did not answer" not in captured.err


def test_a_connection_folder_that_missed_is_marked_where_it_sits(monkeypatch, capsys):
    """In a provider with several connections the straggler is one folder, and
    the marker belongs on that folder — `ls github` must not read as though the
    whole integration failed."""

    class MissedConnectionClient(GapClient):
        def sources_tree(self, depth=3):
            sources = super().sources_tree(depth)
            # What the server actually sends for one missed connection: the
            # sentence on the folder that missed and on its provider, so the
            # tree localizes it and the warning names it once.
            github = {**sources[0], "listing_error": STRAGGLER}
            github["tree"] = [
                {
                    "name": "plugin",
                    "kind": "folder",
                    "source": "bbbb",
                    "listing_error": STRAGGLER,
                    "children": [],
                },
                {"name": "stash", "kind": "folder", "source": "aaaa", "children": []},
            ]
            return [github]

    _setup(monkeypatch)
    monkeypatch.setattr(main, "_client", lambda: MissedConnectionClient(_gapped()))

    main.ls_cmd(path="", depth=2, as_json=False)

    captured = capsys.readouterr()
    assert "listing incomplete" in captured.out
    assert STRAGGLER in captured.err


def test_a_gap_sentence_is_printed_as_written(monkeypatch, capsys):
    """Rich styles the tree, so server text that happens to contain brackets must
    arrive as text and not as markup that silently eats characters."""
    spiky = "github: acme/[bold]plugin[/bold] did not answer within the 10 s listing budget"

    _gap_setup(monkeypatch, _gapped(github=spiky))

    main.ls_cmd(path="", depth=2, as_json=False)

    captured = capsys.readouterr()
    assert "acme/[bold]plugin[/bold] did not answer" in captured.err
