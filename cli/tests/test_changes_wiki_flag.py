"""`stash changes --wiki` must put the scope on the wire, or the fix is invisible.

The external curator's feed is scoped server-side in SQL, and the only way that
agent reads its feed is this command. So the flag is the whole difference between
"the sharing promise is enforced" and "the promise is prose": if the CLI dropped
the value, or sent an empty one for the server to guess about, the external
curator would silently be back on the unscoped stream.

An omitted flag must send no `wiki` param at all — not `wiki=` — so every caller
that has never heard of the flag keeps the exact request it sends today.
"""

from __future__ import annotations

import httpx
from typer.testing import CliRunner

from cli import main
from cli.client import StashClient

runner = CliRunner()


def _recording_handler(requests: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    return handle


def _capturing_client(requests: list[httpx.Request]) -> StashClient:
    """A client whose transport records the request instead of sending it."""
    client = StashClient("https://example.test", api_key="k")
    client._http = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(_recording_handler(requests)),
    )
    return client


def test_wiki_external_reaches_the_request():
    requests: list[httpx.Request] = []
    client = _capturing_client(requests)

    client.get_changes("2026-01-01T00:00:00", "external")

    assert requests[0].url.params["since"] == "2026-01-01T00:00:00"
    assert requests[0].url.params["wiki"] == "external"


def test_unscoped_read_sends_no_wiki_param():
    requests: list[httpx.Request] = []
    client = _capturing_client(requests)

    client.get_changes("2026-01-01T00:00:00")

    assert "wiki" not in requests[0].url.params
    assert "since" in requests[0].url.params


def test_flagless_invocation_sends_no_wiki_param(monkeypatch):
    """`stash changes` as it was typed yesterday sends the request it made
    yesterday — the server's own default, not a value the CLI invented."""
    requests: list[httpx.Request] = []
    monkeypatch.setattr(main, "_client", lambda: _capturing_client(requests))

    result = runner.invoke(main.app, ["changes"])

    assert result.exit_code == 0, result.output
    assert "wiki" not in requests[0].url.params


def test_wiki_flag_reaches_the_request_end_to_end(monkeypatch):
    requests: list[httpx.Request] = []
    monkeypatch.setattr(main, "_client", lambda: _capturing_client(requests))

    result = runner.invoke(main.app, ["changes", "--wiki", "external"])

    assert result.exit_code == 0, result.output
    assert requests[0].url.params["wiki"] == "external"


def test_invalid_wiki_exits_2_naming_the_two_values():
    result = runner.invoke(main.app, ["changes", "--wiki", "exteranl"])

    assert result.exit_code == 2
    assert "--wiki" in result.output
    assert "internal" in result.output and "external" in result.output
