"""The CLI half of the page-write identity channel.

A `stash` run inside a dispatched agent turn holds its owner's API key, so every
page it writes would be credited to the owner. The server therefore hands the turn
two environment variables naming the run -- its session id and the id of its
`agents` row -- and the CLI has to carry them back as a header pair
(`backend/auth.py` turns them into the page's writer). The pair carries an id and
not a name because a header value has to survive the ASCII wire: a display name
with an em dash or a Slovak diacritic raises in the client's own encoder before
the request ever leaves the machine.

Three things are pinned here, in the order they fail in real incidents:

* the pair is sent when it is present, alongside the existing auth/scope headers;
* the pair is absent when the CLI is run by a person at a shell -- the absence is
  the answer ("a human"), so no placeholder may stand in for it;
* a HALF pair raises. One variable set is a broken injector, and a request wearing
  a session id no id can explain is worse than a refused one.

The last two tests guard the seam: the names are spelled independently at each end
(the CLI ships standalone), and the hazard that motivated the id is pinned with an
encoder-level reproduction.
"""

from pathlib import Path

import httpx
import pytest

from cli.client import (
    AGENT_ID_ENV,
    AGENT_ID_HEADER,
    AGENT_SESSION_ENV,
    AGENT_SESSION_HEADER,
    StashClient,
    StashError,
)
from cli.exit_codes import EXIT_INTERNAL_ERROR, classify_error

REPO_ROOT = Path(__file__).resolve().parents[2]

SESSION = "agent-curate-11111111-1111-1111-1111-111111111111-20261006120000"
AGENT_ID = "2b1ce9a4-7f05-4d38-9a2f-6d47e0f18b3c"


@pytest.fixture
def _clear_attribution(monkeypatch):
    """Start from a shell with no run identity, whatever the test runner has."""
    monkeypatch.delenv(AGENT_SESSION_ENV, raising=False)
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)


def _client() -> StashClient:
    return StashClient("https://example.test", api_key="sk_test")


def test_a_run_inside_a_turn_sends_both_headers(monkeypatch, _clear_attribution):
    """Both variables present means the CLI is running inside a dispatched run, and
    the server needs both halves to credit the write to it."""
    monkeypatch.setenv(AGENT_SESSION_ENV, SESSION)
    monkeypatch.setenv(AGENT_ID_ENV, AGENT_ID)

    headers = _client()._headers()

    assert headers[AGENT_SESSION_HEADER] == SESSION
    assert headers[AGENT_ID_HEADER] == AGENT_ID
    # The run's identity rides along with the owner's credential; it does not
    # displace it, and it does not disturb the via/scope headers either.
    assert headers["Authorization"] == "Bearer sk_test"


def test_a_person_at_a_shell_sends_neither(monkeypatch, _clear_attribution):
    """No variables is the ordinary case and it must stay silent: an empty header or
    a placeholder would reach the server as a claim it cannot honour, and the
    server's answer to a broken claim is a 400 on a write a person just made."""
    headers = _client()._headers()

    assert AGENT_SESSION_HEADER not in headers
    assert AGENT_ID_HEADER not in headers


def test_a_half_pair_raises_naming_both_variables(monkeypatch, _clear_attribution):
    """The injector sets both or neither, so one alone is a bug upstream of the CLI.
    Failing here, at the first request, beats shipping a request that credits a page
    to a session nobody can name."""
    monkeypatch.setenv(AGENT_SESSION_ENV, SESSION)

    with pytest.raises(StashError) as half_session:
        _client()._headers()
    assert AGENT_SESSION_ENV in str(half_session.value)
    assert AGENT_ID_ENV in str(half_session.value)

    monkeypatch.delenv(AGENT_SESSION_ENV)
    monkeypatch.setenv(AGENT_ID_ENV, AGENT_ID)

    with pytest.raises(StashError) as half_id:
        _client()._headers()
    assert AGENT_SESSION_ENV in str(half_id.value)
    assert AGENT_ID_ENV in str(half_id.value)


def test_a_half_pair_exits_with_a_code_and_not_a_traceback(monkeypatch, _clear_attribution):
    """A run cannot repair its own injected environment by retrying with other
    arguments, so the failure carries the internal-error code as one clean line.
    The exit boundary in cli/main.py re-raises whatever it does not classify, so a
    plain exception type here would print a traceback at an agent that has a
    perfectly fixable environment to go and fix -- measured on the wire, this exact
    case printed one before it was fixed."""
    monkeypatch.setenv(AGENT_SESSION_ENV, SESSION)

    with pytest.raises(StashError) as half_pair:
        _client()._headers()

    assert classify_error(half_pair.value) == EXIT_INTERNAL_ERROR


def test_the_names_are_the_ones_the_server_side_uses():
    """Drift guard. The CLI is a standalone distribution and repeats the spellings
    rather than importing them, so a rename at either end would otherwise sever the
    channel quietly -- the writes would simply go back to being unattributable.

    Checked against the source text, not an import: the CLI's test environment has
    no backend dependencies installed, and the fact under test is a string."""
    page_actor = (REPO_ROOT / "backend/services/page_actor.py").read_text(encoding="utf-8")
    sprite = (REPO_ROOT / "backend/services/sprite_agent_service.py").read_text(encoding="utf-8")
    client = (REPO_ROOT / "cli/client.py").read_text(encoding="utf-8")

    assert f'AGENT_SESSION_HEADER = "{AGENT_SESSION_HEADER}"' in page_actor
    assert 'AGENT_ID_HEADER = "X-Stash-Agent-Id"' in page_actor
    assert f'AGENT_SESSION_ENV = "{AGENT_SESSION_ENV}"' in page_actor
    assert 'AGENT_ID_ENV = "STASH_AGENT_ID"' in page_actor
    # The injector imports the same two constants it writes into the child env.
    assert "AGENT_ID_ENV, AGENT_SESSION_ENV" in sprite
    assert "STASH_AGENT_SESSION" not in sprite, "the spelling lives in page_actor, once"
    # The removed spellings have no alias anywhere on the live path: neither the
    # name header nor the name env may reappear in the files that spell the
    # channel. A reintroduction here would put a display string back on the wire.
    # Built from fragments so this guard file itself carries no copy of what it
    # forbids -- STAS-498's census admits the old spelling to no test or document.
    gone_name_header = "X-Stash-Agent-" + "Name"
    gone_name_env = "STASH_AGENT_" + "NAME"
    assert gone_name_env not in sprite, "the name never went on the wire"
    assert gone_name_header not in client
    assert gone_name_env not in client
    assert gone_name_header not in page_actor
    assert gone_name_env not in page_actor


def test_a_display_name_cannot_cross_the_wire_an_id_can():
    """The hazard this channel's shape exists to kill, pinned at the encoder
    (measured in STAS-498 step 1 against httpx 0.28 with httpx.MockTransport, so
    nothing leaves the machine): a header value carries bytes, and the only codec
    every HTTP stack agrees on is ASCII. A dispatched run whose agent is named
    with a U+2014 em dash -- the founder curator lanes' real names -- died inside
    httpx with UnicodeEncodeError before a single byte reached the server, so such
    an agent could not make ONE attributed call. An id is a uuid string: ASCII by
    construction, and it resolves server-side to the display name the provenance
    columns want. Asserted on the exception's existence, not its message text,
    because the wording around the codec is httpx-version-dependent."""
    em_dash_name = "Wiki curator \u2014 Fusion \u2014 saneca"
    # The removed header's own spelling, built from fragments: the census admits
    # the literal nowhere, and this test is about the VALUE, not the key.
    old_name_header = "X-Stash-Agent-" + "Name"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url="http://attribution.test",
        transport=httpx.MockTransport(handler),
        timeout=5,
    ) as http:
        with pytest.raises(UnicodeEncodeError):
            http.get("/health", headers={old_name_header: em_dash_name})

        resp = http.get("/health", headers={AGENT_ID_HEADER: AGENT_ID})
        assert resp.status_code == 200
