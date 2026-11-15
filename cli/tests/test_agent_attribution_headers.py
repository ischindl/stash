"""The CLI half of the page-write identity channel.

A `stash` run inside a dispatched agent turn holds its owner's API key, so every
page it writes would be credited to the owner. The server therefore hands the turn
two environment variables naming the run, and the CLI has to carry them back as a
header pair (`backend/auth.py` turns them into the page's writer).

Three things are pinned here, in the order they fail in real incidents:

* the pair is sent when it is present, alongside the existing auth/scope headers;
* the pair is absent when the CLI is run by a person at a shell — the absence is
  the answer ("a human"), so no placeholder may stand in for it;
* a HALF pair raises. One variable set is a broken injector, and a request wearing
  a session id no name can explain is worse than a refused one.

The last test is a drift guard: the names are spelled independently at each end
(the CLI ships standalone), and a rename on one side would otherwise degrade into
silently unattributed writes.
"""

from pathlib import Path

import pytest

from cli.client import (
    AGENT_NAME_ENV,
    AGENT_NAME_HEADER,
    AGENT_SESSION_ENV,
    AGENT_SESSION_HEADER,
    StashClient,
    StashError,
)
from cli.exit_codes import EXIT_INTERNAL_ERROR, classify_error

REPO_ROOT = Path(__file__).resolve().parents[2]

SESSION = "agent-curate-11111111-1111-1111-1111-111111111111-20261006120000"
AGENT = "Folder Curator"


@pytest.fixture
def _clear_attribution(monkeypatch):
    """Start from a shell with no run identity, whatever the test runner has."""
    monkeypatch.delenv(AGENT_SESSION_ENV, raising=False)
    monkeypatch.delenv(AGENT_NAME_ENV, raising=False)


def _client() -> StashClient:
    return StashClient("https://example.test", api_key="sk_test")


def test_a_run_inside_a_turn_sends_both_headers(monkeypatch, _clear_attribution):
    """Both variables present means the CLI is running inside a dispatched run, and
    the server needs both halves to credit the write to it."""
    monkeypatch.setenv(AGENT_SESSION_ENV, SESSION)
    monkeypatch.setenv(AGENT_NAME_ENV, AGENT)

    headers = _client()._headers()

    assert headers[AGENT_SESSION_HEADER] == SESSION
    assert headers[AGENT_NAME_HEADER] == AGENT
    # The run's identity rides along with the owner's credential; it does not
    # displace it, and it does not disturb the via/scope headers either.
    assert headers["Authorization"] == "Bearer sk_test"


def test_a_person_at_a_shell_sends_neither(monkeypatch, _clear_attribution):
    """No variables is the ordinary case and it must stay silent: an empty header or
    a placeholder would reach the server as a claim it cannot honour, and the
    server's answer to a broken claim is a 400 on a write a person just made."""
    headers = _client()._headers()

    assert AGENT_SESSION_HEADER not in headers
    assert AGENT_NAME_HEADER not in headers


def test_a_half_pair_raises_naming_both_variables(monkeypatch, _clear_attribution):
    """The injector sets both or neither, so one alone is a bug upstream of the CLI.
    Failing here, at the first request, beats shipping a request that credits a page
    to a session nobody can name."""
    monkeypatch.setenv(AGENT_SESSION_ENV, SESSION)

    with pytest.raises(StashError) as half_session:
        _client()._headers()
    assert AGENT_SESSION_ENV in str(half_session.value)
    assert AGENT_NAME_ENV in str(half_session.value)

    monkeypatch.delenv(AGENT_SESSION_ENV)
    monkeypatch.setenv(AGENT_NAME_ENV, AGENT)

    with pytest.raises(StashError) as half_name:
        _client()._headers()
    assert AGENT_SESSION_ENV in str(half_name.value)
    assert AGENT_NAME_ENV in str(half_name.value)


def test_a_half_pair_exits_with_a_code_and_not_a_traceback(monkeypatch, _clear_attribution):
    """A run cannot repair its own injected environment by retrying with other
    arguments, so the failure carries the internal-error code as one clean line.
    The exit boundary in cli/main.py re-raises whatever it does not classify, so a
    plain exception type here would print a traceback at an agent that has a
    perfectly fixable environment to go and fix — measured on the wire, this exact
    case printed one before it was fixed."""
    monkeypatch.setenv(AGENT_SESSION_ENV, SESSION)

    with pytest.raises(StashError) as half_pair:
        _client()._headers()

    assert classify_error(half_pair.value) == EXIT_INTERNAL_ERROR


def test_the_names_are_the_ones_the_server_side_uses():
    """Drift guard. The CLI is a standalone distribution and repeats the spellings
    rather than importing them, so a rename at either end would otherwise sever the
    channel quietly — the writes would simply go back to being unattributable.

    Checked against the source text, not an import: the CLI's test environment has
    no backend dependencies installed, and the fact under test is a string."""
    page_actor = (REPO_ROOT / "backend/services/page_actor.py").read_text(encoding="utf-8")
    sprite = (REPO_ROOT / "backend/services/sprite_agent_service.py").read_text(encoding="utf-8")

    assert f'AGENT_SESSION_HEADER = "{AGENT_SESSION_HEADER}"' in page_actor
    assert f'AGENT_NAME_HEADER = "{AGENT_NAME_HEADER}"' in page_actor
    assert f'AGENT_SESSION_ENV = "{AGENT_SESSION_ENV}"' in page_actor
    assert f'AGENT_NAME_ENV = "{AGENT_NAME_ENV}"' in page_actor
    # The injector imports the same two constants it writes into the child env.
    assert "AGENT_NAME_ENV, AGENT_SESSION_ENV" in sprite
    assert "STASH_AGENT_SESSION" not in sprite, "the spelling lives in page_actor, once"
