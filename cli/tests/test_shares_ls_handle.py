"""STAS-346: the share endpoint's MCP handle must reach every CLI surface.

``GET /api/v1/share`` is the only place a folder's MCP handle lives
(``backend/routers/shares.py`` returns ``shares``, ``general_access`` and
``mcp_url`` together). The old client method funneled that response through the
generic ``_list()`` helper, which keeps the list under one key and discards its
siblings, so ``stash shares ls folder <id> --json`` printed ``No shares found.``
and ``[]`` for a folder whose endpoint response carried a working handle —
STAS-298 defect D-1, whose hand step H3 was "read the handle with a raw REST
call, because the CLI will not tell you".

The payloads below are the ones STAS-298 captured off the wire
(``.fusion/tasks/STAS-298/attachments/1791485893428-bundle-step2-handles.txt``:
``step2-share-listing-endpoint.json`` vs ``step2-cli-shares-ls.json`` and
``step2-negative-listing.txt``), so the recorded symptom is replayed through the
real code paths rather than reimagined. The handle must survive byte-identical:
the CLI renders the server's string or says nothing, and never assembles one.
"""

import json

import httpx
from typer.testing import CliRunner

from cli import main, mcp_server
from cli.client import StashClient

runner = CliRunner()

# The exact captured link-granted envelope: no named people, but a live handle.
LINK_GRANTED = {
    "shares": [],
    "general_access": "read",
    "mcp_url": (
        "http://127.0.0.1:4199/api/v1/mcp/folders/0VmWDNWgYHsIHKRJr6_EWAus3mt0I1CY6aBv5-stsWs"
    ),
}

# The captured private/revoked envelope: silence is the correct answer here.
PRIVATE = {"shares": [], "general_access": "none", "mcp_url": None}

# A released PyPI CLI can talk to a server that predates the share-MCP surface.
# Absent is the same truth as null: the CLI must stay silent, not crash.
LEGACY_SERVER = {"shares": [], "general_access": "read"}

# shares populated + handle set: the state the old list-only shape did render.
LINK_GRANTED_WITH_PEOPLE = {
    "shares": [{"display_name": "Sam", "permission": "read", "principal_id": "u1"}],
    "general_access": "read",
    "mcp_url": LINK_GRANTED["mcp_url"],
}


def _served(payload: dict, seen: list[httpx.Request]) -> StashClient:
    """A StashClient whose transport always answers with `payload`, logging requests."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    client = StashClient("https://example.test", api_key="k")
    client._http = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handle)
    )
    return client


# --- client: the whole envelope reaches the caller --------------------------


def test_link_granted_envelope_survives_whole() -> None:
    """The endpoint's sibling keys are the point of the call for a folder, so
    they must not be filtered out on the way back."""
    seen: list[httpx.Request] = []
    state = _served(LINK_GRANTED, seen).get_share_state("folder", "fold-1")
    assert state == LINK_GRANTED


def test_handle_is_byte_identical_to_the_wire() -> None:
    """The URL is minted server-side from PUBLIC_URL. Any CLI-side assembly,
    normalization, or re-base would break the paste-into-`tools add` chain, so
    the exact captured string has to come back out of the client."""
    seen: list[httpx.Request] = []
    wire = json.dumps(LINK_GRANTED)
    client = _served(LINK_GRANTED, seen)
    client._http = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=wire)),
    )
    assert client.get_share_state("folder", "fold-1")["mcp_url"] == (
        "http://127.0.0.1:4199/api/v1/mcp/folders/0VmWDNWgYHsIHKRJr6_EWAus3mt0I1CY6aBv5-stsWs"
    )


def test_endpoint_and_query_params_are_unchanged() -> None:
    """The share-state method reads the same endpoint with the same params the
    listing endpoint takes — a renamed param would 422 at runtime only."""
    seen: list[httpx.Request] = []
    _served(PRIVATE, seen).get_share_state("folder", "fold-1")
    assert seen[0].url.path == "/api/v1/share"
    assert dict(seen[0].url.params) == {"object_type": "folder", "object_id": "fold-1"}


def test_private_envelope_passes_through_as_is() -> None:
    """A private share answers with a null handle. The client reports that
    truth instead of inventing a substitute."""
    seen: list[httpx.Request] = []
    assert _served(PRIVATE, seen).get_share_state("folder", "fold-1") == PRIVATE


def test_legacy_server_without_the_key_does_not_crash() -> None:
    """The CLI is distributed separately from the server, so a released CLI
    meets a server that never sends the key at all."""
    seen: list[httpx.Request] = []
    state = _served(LEGACY_SERVER, seen).get_share_state("folder", "fold-1")
    assert "mcp_url" not in state
    assert state == LEGACY_SERVER


# --- `stash shares ls`: the handle reaches both output modes -----------------


class _ShareStateClient:
    """Stand-in for StashClient that answers with one recorded share-state payload."""

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_share_state(self, object_type, object_id):
        return dict(self._payload)


def _wire(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(main, "_require_auth", lambda: {"api_key": "k"})
    monkeypatch.setattr(main, "_client", lambda: _ShareStateClient(payload))


def test_link_granted_json_carries_the_handle(monkeypatch, capsys) -> None:
    """The STAS-298 symptom, closed: an empty `shares` list used to make the
    command print `No shares found.` and `[]` and hide a handle that was live
    on the wire. --json stdout is now the endpoint's envelope."""
    _wire(monkeypatch, LINK_GRANTED)
    main.shares_ls(object_type="folder", object_id="fold-1", as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == LINK_GRANTED
    assert "No shares found." not in captured.err


def test_link_granted_text_prints_the_handle(monkeypatch, capsys) -> None:
    """Human mode has to hand over something pasteable into
    `stash tools add --url`, which means the URL survives unwrapped: rich
    folds an 80-column line and a folded URL is not a handle."""
    _wire(monkeypatch, LINK_GRANTED)
    main.shares_ls(object_type="folder", object_id="fold-1", as_json=False)
    captured = capsys.readouterr()
    assert LINK_GRANTED["mcp_url"] in captured.out
    assert "No shares found." not in captured.err


def test_people_and_handle_both_render(monkeypatch, capsys) -> None:
    """Named people and a link grant are not alternatives; a folder can have
    both, and neither row may suppress the other."""
    _wire(monkeypatch, LINK_GRANTED_WITH_PEOPLE)
    main.shares_ls(object_type="folder", object_id="fold-1", as_json=False)
    captured = capsys.readouterr()
    assert "Sam" in captured.out
    assert LINK_GRANTED["mcp_url"] in captured.out


def test_private_share_stays_silent(monkeypatch, capsys) -> None:
    """This is the behaviour that was already right and must not be traded
    away: no link grant, so no handle, so the empty-state note and no URL."""
    _wire(monkeypatch, PRIVATE)
    main.shares_ls(object_type="folder", object_id="fold-1", as_json=False)
    captured = capsys.readouterr()
    assert "No shares found." in captured.err
    assert "No shares found." not in captured.out
    assert "mcp/folders" not in captured.out


def test_legacy_server_exits_0_and_invents_nothing(monkeypatch) -> None:
    """A released CLI meeting a server that never sends the key prints the
    empty-state note like any other empty listing — exit 0, no URL, no crash."""
    _wire(monkeypatch, LEGACY_SERVER)
    result = runner.invoke(main.app, ["shares", "ls", "folder", "fold-1"])
    assert result.exit_code == 0
    assert "No shares found." in result.stderr
    assert "mcp/folders" not in result.stdout


def test_mcp_tool_returns_the_envelope(monkeypatch) -> None:
    """The stdio bridge is the other half of the chain: an agent asking
    `stash_list_shares` gets the same envelope, handle byte-identical, and
    nothing wrapped around it."""
    calls: list = []

    class _Client:
        def get_share_state(self, object_type, object_id):
            calls.append((object_type, object_id))
            return dict(LINK_GRANTED)

    monkeypatch.setattr(mcp_server, "_client", lambda: _Client())
    assert json.loads(mcp_server.stash_list_shares("folder", "fold-1")) == LINK_GRANTED
    assert calls == [("folder", "fold-1")]
