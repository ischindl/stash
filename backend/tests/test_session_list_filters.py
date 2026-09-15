"""The Sessions list's server-side prefilters and page-2 marker.

Every filter runs in SQL rather than in the browser because the list pages: a
client-side filter could only ever see the page already loaded, so "Load more"
would append rows that contradict the filter. These tests pin that contract for
each prefilter plus `has_more`.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from .conftest import unique_name


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


async def _account(client: AsyncClient, prefix: str = "sesslist") -> tuple[str, str]:
    name = unique_name(prefix)
    resp = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@test.local"},
    )
    assert resp.status_code == 201
    body = resp.json()
    return body["api_key"], body["id"]


async def _register(client: AsyncClient) -> str:
    key, _user_id = await _account(client)
    return key


async def _push(
    client: AsyncClient,
    key: str,
    session_id: str,
    *,
    agent_name: str = "claude",
    content: str = "hello",
    session_folder_id: str | None = None,
) -> None:
    """One event is what makes a session row visible in the list at all — an
    event-less shell is deliberately hidden."""
    event = {
        "agent_name": agent_name,
        "event_type": "user_message",
        "content": content,
        "session_id": session_id,
    }
    if session_folder_id:
        event["session_folder_id"] = session_folder_id
    resp = await client.post(
        "/api/v1/me/sessions/events/batch",
        json={"events": [event]},
        headers=_auth(key),
    )
    assert resp.status_code == 201


async def _title(client: AsyncClient, key: str, session_id: str, title: str) -> None:
    resp = await client.patch(
        "/api/v1/me/sessions/title",
        params={"session_id": session_id},
        json={"title": title},
        headers=_auth(key),
    )
    assert resp.status_code == 200


async def _ids(client: AsyncClient, key: str, **params) -> tuple[list[str], bool]:
    resp = await client.get("/api/v1/me/sessions", params=params, headers=_auth(key))
    assert resp.status_code == 200
    body = resp.json()
    return [s["session_id"] for s in body["sessions"]], body["has_more"]


@pytest.mark.asyncio
async def test_has_more_marks_the_pages_ahead(client: AsyncClient, _db_pool):
    """`has_more` comes from fetching one row past the page, so the client can
    offer Load more without the server running a second count query."""
    key = await _register(client)
    for i in range(3):
        await _push(client, key, f"paged-{i}")

    first_page, has_more = await _ids(client, key, limit=2)
    assert len(first_page) == 2
    assert has_more is True

    second_page, more_after = await _ids(client, key, limit=2, offset=2)
    assert len(second_page) == 1
    assert more_after is False
    # Pages partition the list: no session appears twice, none goes unseen.
    assert set(first_page).isdisjoint(second_page)
    assert set(first_page) | set(second_page) == {"paged-0", "paged-1", "paged-2"}


@pytest.mark.asyncio
async def test_pages_partition_the_list_when_sessions_share_a_timestamp(
    client: AsyncClient, pool, _db_pool
):
    """Paging is only correct while row-selection order and returned order are one
    and the same total order. If they break `last_event_at` ties differently, the
    lookahead row can land mid-page: the sliced-off row is then skipped by every
    page while the following page repeats a row already shown."""
    viewer_key, viewer_id = await _account(client, "pager")
    _other_key, other_id = await _account(client, "authored")
    for sid in ("a-early", "b-mid", "c-late"):
        await _push(client, viewer_key, sid)

    # Deterministic display names, and an author whose name sorts the opposite
    # way to session_id, so the two orderings disagree on the first tie.
    await pool.execute("UPDATE users SET display_name = 'aaa viewer' WHERE id = $1", viewer_id)
    await pool.execute("UPDATE users SET display_name = 'zzz author' WHERE id = $1", other_id)
    same_moment = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    await pool.execute(
        "UPDATE sessions SET last_event_at = $1 WHERE owner_user_id = $2",
        same_moment,
        viewer_id,
    )
    await pool.execute(
        "UPDATE sessions SET created_by = $1 WHERE owner_user_id = $2 AND session_id = 'a-early'",
        other_id,
        viewer_id,
    )

    collected: list[str] = []
    offset = 0
    for _ in range(5):
        ids, has_more = await _ids(client, viewer_key, limit=1, offset=offset)
        collected += ids
        if not has_more:
            break
        offset += 1

    assert collected == ["a-early", "b-mid", "c-late"]


@pytest.mark.asyncio
async def test_hide_curator_drops_curator_run_transcripts(client: AsyncClient, _db_pool):
    """The curator files its own nightly runs as `agent-curate-…` sessions. Left
    in the list they bury a person's own sessions the first night it runs."""
    key = await _register(client)
    await _push(client, key, "my-work")
    await _push(client, key, "agent-curate-a1b2c3d4-2026-08-01")

    shown, _ = await _ids(client, key, hide_curator=True)
    assert shown == ["my-work"]

    unfiltered, _ = await _ids(client, key)
    assert "agent-curate-a1b2c3d4-2026-08-01" in unfiltered


@pytest.mark.asyncio
async def test_folder_filter_returns_only_that_folder(client: AsyncClient, _db_pool):
    key = await _register(client)
    folder = await client.post(
        "/api/v1/me/session-folders/get-or-create",
        json={"external_key": "org_900", "name": "Quarterly Review"},
        headers=_auth(key),
    )
    folder_id = folder.json()["id"]

    await _push(client, key, "filed-here", session_folder_id=folder_id)
    await _push(client, key, "filed-there")

    filed, _ = await _ids(client, key, folder_id=folder_id)
    assert filed == ["filed-here"]


@pytest.mark.asyncio
async def test_agent_filter_matches_agent_name(client: AsyncClient, _db_pool):
    key = await _register(client)
    await _push(client, key, "claude-run", agent_name="claude")
    await _push(client, key, "codex-run", agent_name="codex")

    shown, _ = await _ids(client, key, agent="codex")
    assert shown == ["codex-run"]


@pytest.mark.asyncio
async def test_title_query_matches_case_insensitively(client: AsyncClient, _db_pool):
    key = await _register(client)
    await _push(client, key, "titled", content="first prompt")
    await _push(client, key, "other")
    await _title(client, key, "titled", "Investigate Flaky Auth Test")

    matched, _ = await _ids(client, key, q="flaky auth")
    assert matched == ["titled"]


@pytest.mark.asyncio
async def test_title_query_treats_wildcards_as_literal_text(client: AsyncClient, _db_pool):
    """Searching goes through strpos, not LIKE, so a title containing a percent
    sign is found by typing it rather than matching every session."""
    key = await _register(client)
    await _push(client, key, "literal")
    await _push(client, key, "plain")
    await _title(client, key, "literal", "100% coverage ramp")

    matched, _ = await _ids(client, key, q="% coverage")
    assert matched == ["literal"]


@pytest.mark.asyncio
async def test_filters_and_together(client: AsyncClient, _db_pool):
    """Prefilters compose with AND: a combination nothing satisfies yields an
    empty page rather than quietly ignoring one of the filters."""
    key = await _register(client)
    await _push(client, key, "claude-work", agent_name="claude")
    await _push(client, key, "codex-work", agent_name="codex")
    await _title(client, key, "claude-work", "Deploy checklist")

    matched, has_more = await _ids(client, key, agent="codex", q="deploy")
    assert matched == []
    assert has_more is False

    satisfied, _ = await _ids(client, key, agent="claude", q="deploy")
    assert satisfied == ["claude-work"]
