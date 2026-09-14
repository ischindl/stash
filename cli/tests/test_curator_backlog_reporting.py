"""`stash changes` may only report a backlog it actually received.

The founder reads this one line to decide how much of their history the Memory
curator still has to read. The number is a distinct-event count computed
server-side (`curator_event_backlog`), so the CLI's only honest options are to
print what the server sent, or to fail. Before STAS-192 the line rendered
through `data.get("event_backlog", {})` and `.get(..., 0)`, so a response
missing the field printed "Backlog: 0 distinct events still unread (0 rows
across 0 sessions)" — the most misleading value it could produce, because a
fabricated zero looks exactly like a drained backlog. A curator that has not
read anything would report itself finished.

So the two tests below pin opposite halves of one rule: the populated response
keeps rendering verbatim (the number the founder already trusts must not drift),
and a response that lacks the field or any part of it raises instead of
inventing a count. AGENTS.md forbids the fallback chain outright; this file is
what keeps it from coming back.
"""

from __future__ import annotations

import pytest

from cli import main
from cli.tests.test_usage_hints import _run_cli

# Small enough that the rendered line stays inside rich's 80-column default, so
# the assertions below match the sentence rather than an accidental wrap.
BACKLOG = {"distinct_events": 1204, "raw_rows": 9631, "distinct_sessions": 118}
BACKLOG_LINE = "Backlog: 1204 distinct events still unread (9631 rows across 118 sessions)"


class _StubClient:
    """The `changes` command's fetch seam, returning one canned payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def get_changes(self, since=None, wiki=None, folder=None):
        return self._payload


def _payload(**overrides) -> dict:
    """A realistic changes payload; `event_backlog` present unless overridden."""
    payload = {
        "since": "2026-09-01T00:00:00+00:00",
        "counts": {"history": 12, "pages": 3, "files": 0, "saves": 1, "sources": 2},
        "event_backlog": dict(BACKLOG),
    }
    payload.update(overrides)
    return payload


def _stub(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(main, "_client", lambda *a, **k: _StubClient(payload))


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_populated_backlog_renders_distinct_with_raw_beside_it(monkeypatch, capsys):
    """The number the founder already trusts, pinned byte for byte.

    Distinct events lead; raw rows and session count travel with them so an
    eight-times-re-uploaded transcript explains itself instead of reading as a
    bug. This rendering is the shipped contract — the fail-loud repair below
    must not change a character of it.
    """
    _stub(monkeypatch, _payload())

    code, out, err = _run_cli(monkeypatch, capsys, ["changes"])

    assert code == 0, err
    flat = _flat(out)
    assert BACKLOG_LINE in flat, flat
    # The distinct count is the headline, not the row count it replaced.
    assert "278165" not in flat
    assert "9631 rows" in flat and "118 sessions" in flat


def test_missing_event_backlog_raises_instead_of_fabricating_zero(monkeypatch, capsys):
    """No field means no number — the fabricated zero is the bug.

    The server sets `event_backlog` on every successful response, so its absence
    is a contract break (an older server, a proxy that reshaped the body). Zeroing
    it silently tells a founder whose curator has read nothing that their backlog
    is empty, which is the same dishonest-complete illusion the distinct-event
    work exists to remove.
    """
    broken = _payload()
    del broken["event_backlog"]
    _stub(monkeypatch, broken)

    with pytest.raises(KeyError):
        _run_cli(monkeypatch, capsys, ["changes"])

    # And nothing was printed that could be read as a count.
    out = _flat(capsys.readouterr().out)
    assert "distinct events still unread" not in out
    assert "0 rows across 0 sessions" not in out


def test_backlog_missing_one_field_raises_instead_of_printing_a_partial_zero(monkeypatch, capsys):
    """A half-present payload is as dangerous as an absent one.

    `raw_rows` alone would render "1204 distinct events still unread (0 rows
    across 118 sessions)" — plausible prose built from a fabricated zero. Every
    field on this line is required, so each missing key has to surface.
    """
    partial = dict(BACKLOG)
    del partial["raw_rows"]
    _stub(monkeypatch, _payload(event_backlog=partial))

    with pytest.raises(KeyError):
        _run_cli(monkeypatch, capsys, ["changes"])

    out = _flat(capsys.readouterr().out)
    assert "0 rows" not in out
