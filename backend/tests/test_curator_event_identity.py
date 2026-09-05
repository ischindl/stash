"""Re-importing a transcript must not inflate what the curator reads or owes.

`history_events` has no identity: a row is unique only by its surrogate id, so
the same conversation re-uploaded eight times is eight rows. Every reader
counted inserts — the backlog a founder reads, and the feed budget the curator
spends per run. These tests pin the one definition of an event and every
surface that must then agree with it.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import curation_service

from .test_curator import _auth, _file_session_into_folder, _push_events, _register
from .test_curator_feed_scoping import (
    BASE,
    EXTERNAL,
    INTERNAL,
    LATER,
    OLD,
    WATERMARK,
    _event,
    _opt_out,
    _workspace,
)
from .test_developer_platform import _developer, _mint_workspace_key, _push


def _turn(session_id: str, content: str, at: datetime) -> dict:
    return {
        "agent_name": "heavi-chat",
        "event_type": "user_message",
        "content": content,
        "session_id": session_id,
        "created_at": at.isoformat(),
    }


async def _row_count(pool, owner_user_id: UUID) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM history_events WHERE owner_user_id = $1", owner_user_id
    )


@pytest.mark.asyncio
async def test_a_reimported_transcript_is_one_set_of_events(client: AsyncClient, pool):
    """The symptom, end to end: three turn events pushed twice are three
    events everywhere the curator looks, while six rows stay in storage."""
    key, uid = await _register(client)
    turns = [_turn("conv-replay", f"turn {i}", BASE + timedelta(minutes=i)) for i in range(3)]

    await _push_events(client, key, turns)
    await _push_events(client, key, turns)

    feed = await curation_service.changes_since(uid, uid, OLD, wiki=INTERNAL)
    assert feed["counts"]["history"] == 3
    assert [h["content"] for h in feed["history"]] == ["turn 0", "turn 1", "turn 2"]
    assert await curation_service.has_changes_since(uid, uid, OLD, wiki=INTERNAL)

    assert await curation_service.curator_event_backlog(uid, INTERNAL, OLD) == {
        "distinct_events": 3,
        "raw_rows": 6,
        "distinct_sessions": 1,
    }

    # The watermark advances through the distinct events it was handed, and
    # then nothing is ahead — with the six rows still sitting there.
    position = await curation_service.complete_through(uid, OLD, LATER, INTERNAL)
    assert position == LATER
    assert await _row_count(pool, uid) == 6
    assert await curation_service.curator_event_backlog(uid, INTERNAL, position) == {
        "distinct_events": 0,
        "raw_rows": 0,
        "distinct_sessions": 0,
    }
    assert not await curation_service.has_changes_since(uid, uid, position, wiki=INTERNAL)


@pytest.mark.asyncio
async def test_events_sharing_one_batch_timestamp_stay_distinct(client: AsyncClient, pool):
    """The timestamp in the identity must not over-collapse. A batch that
    omits `created_at` gives every event the same server `now()`, so three
    different contents arriving together are still three events."""
    key, uid = await _register(client)
    await _push_events(
        client,
        key,
        [
            {
                "agent_name": "heavi-chat",
                "event_type": "user_message",
                "content": content,
                "session_id": "conv-batch",
            }
            for content in ("asked about pricing", "asked about quotas", "said thanks")
        ],
    )

    assert await curation_service.curator_event_backlog(uid, INTERNAL, OLD) == {
        "distinct_events": 3,
        "raw_rows": 3,
        "distinct_sessions": 1,
    }


@pytest.mark.asyncio
async def test_the_same_content_at_two_times_is_two_events(client: AsyncClient, pool):
    """A re-asked question is new work; a re-imported copy is not. The
    discriminator is the event's own timestamp, not the row's insert time."""
    key, uid = await _register(client)
    await _push_events(client, key, [_turn("conv-repeat", "same words", BASE)])
    await _push_events(client, key, [_turn("conv-repeat", "same words", BASE + timedelta(hours=2))])

    assert await curation_service.curator_event_backlog(uid, INTERNAL, OLD) == {
        "distinct_events": 2,
        "raw_rows": 2,
        "distinct_sessions": 1,
    }


@pytest.mark.asyncio
async def test_the_gate_and_the_feed_agree_when_only_copies_remain(client: AsyncClient, pool):
    """Copies of an event can straddle the watermark — and then the feed must
    be non-empty exactly when the daily gate says it is. An identity group
    shares one `created_at`, so a copy is either wholly ahead or wholly behind;
    a genuinely newer timestamp is a different event, not a copy."""
    key, uid = await _register(client)
    turns = [_turn("conv-bound", f"turn {i}", BASE + timedelta(minutes=i)) for i in range(2)]
    await _push_events(client, key, turns)
    await _push_events(client, key, turns)

    assert not await curation_service.has_changes_since(uid, uid, WATERMARK, wiki=INTERNAL)
    assert await curation_service.curator_event_backlog(uid, INTERNAL, WATERMARK) == {
        "distinct_events": 0,
        "raw_rows": 0,
        "distinct_sessions": 0,
    }

    await _push_events(client, key, [_turn("conv-bound", "turn 1", LATER)])

    assert await curation_service.has_changes_since(uid, uid, WATERMARK, wiki=INTERNAL)
    assert await curation_service.curator_event_backlog(uid, INTERNAL, WATERMARK) == {
        "distinct_events": 1,
        "raw_rows": 1,
        "distinct_sessions": 1,
    }
    feed = await curation_service.changes_since(uid, uid, WATERMARK, wiki=INTERNAL)
    assert [h["content"] for h in feed["history"]] == ["turn 1"]


@pytest.mark.asyncio
async def test_the_overflow_budget_buys_distinct_events(client: AsyncClient, pool, monkeypatch):
    """With fifteen rows for five distinct events and a cap of three, one run
    must spend its budget on three different things — and the boundary it
    hands the next run must leave exactly the distinct events that didn't fit."""
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 3)
    key, uid = await _register(client)
    for _ in range(3):
        await _push_events(
            client,
            key,
            [_turn("conv-cap", f"c{i}", BASE + timedelta(minutes=i)) for i in range(5)],
        )

    assert await curation_service.curator_event_backlog(uid, INTERNAL, OLD) == {
        "distinct_events": 5,
        "raw_rows": 15,
        "distinct_sessions": 1,
    }

    events, has_more = await curation_service._feed_events(uid, OLD, None, 3, wiki=INTERNAL)
    assert has_more is True
    assert [e["content"] for e in events] == ["c0", "c1", "c2"]

    boundary = await curation_service.complete_through(
        uid, OLD, BASE + timedelta(hours=1), INTERNAL
    )
    assert boundary == BASE + timedelta(minutes=2) - timedelta(microseconds=1)

    assert await curation_service.curator_event_backlog(uid, INTERNAL, boundary) == {
        "distinct_events": 3,
        "raw_rows": 9,
        "distinct_sessions": 1,
    }


@pytest.mark.asyncio
async def test_duplicates_collapse_and_the_shared_wiki_stays_scoped(client: AsyncClient, pool):
    """Collapsing and scoping are orthogonal: the shared wiki still excludes
    an opted-out user's session even though its copies collapsed away too."""
    scope, key = await _workspace(client)
    shared = _event("sess-shared", "shared turn", user_id="org_acme", at=BASE)
    private = _event("sess-private", "private turn", user_id="org_choate", at=BASE)
    await _push(client, key, [shared, private])
    await _push(client, key, [shared, private])

    assert await curation_service.curator_event_backlog(scope, INTERNAL, OLD) == {
        "distinct_events": 2,
        "raw_rows": 4,
        "distinct_sessions": 2,
    }

    await _opt_out(pool, scope, "org_choate")

    assert await curation_service.curator_event_backlog(scope, EXTERNAL, OLD) == {
        "distinct_events": 1,
        "raw_rows": 2,
        "distinct_sessions": 1,
    }
    feed = await curation_service.changes_since(scope, scope, OLD, wiki=EXTERNAL)
    assert [h["content"] for h in feed["history"]] == ["shared turn"]


@pytest.mark.asyncio
async def test_the_curator_transcript_is_never_backlog(client: AsyncClient, pool):
    """The curator's own run transcripts are excluded before dedupe, so they
    cannot become a canonical row and are not even counted as raw rows."""
    key, uid = await _register(client)
    await _push_events(client, key, [_turn("conv-real", "real turn", BASE)])
    await _push_events(
        client,
        key,
        [_turn("agent-curate-abc-202601011200", f"thinking {i}", LATER) for i in range(4)],
    )

    assert await _row_count(pool, uid) == 5
    assert await curation_service.curator_event_backlog(uid, INTERNAL, OLD) == {
        "distinct_events": 1,
        "raw_rows": 1,
        "distinct_sessions": 1,
    }


@pytest.mark.asyncio
async def test_a_backlog_of_only_curator_transcripts_reports_zero(client: AsyncClient, pool):
    """The honest zero. A backlog can only reach 0 by the feed being empty —
    here the sole activity after the position is the curator's own transcript,
    which is outside the feed, so all three surfaces must say none."""
    key, uid = await _register(client)
    await _push_events(client, key, [_turn("conv-real", "real turn", BASE)])
    await _push_events(
        client, key, [_turn("agent-curate-abc-202601011200", "curator thinking", LATER)]
    )

    assert await _row_count(pool, uid) == 2
    assert await curation_service.curator_event_backlog(uid, INTERNAL, WATERMARK) == {
        "distinct_events": 0,
        "raw_rows": 0,
        "distinct_sessions": 0,
    }
    assert not await curation_service.has_changes_since(uid, uid, WATERMARK, wiki=INTERNAL)
    events, has_more = await curation_service._feed_events(uid, WATERMARK, None, 100, wiki=INTERNAL)
    assert (events, has_more) == ([], False)


@pytest.mark.asyncio
async def test_draining_the_feed_consumes_exactly_the_backlog(
    client: AsyncClient, pool, monkeypatch
):
    """The backlog is a drain estimate only if consuming the feed lands it at
    zero. Fourteen rows for seven events, drained three at a time, must read
    nine rows and consume each of the seven identities — no more, no less."""
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 3)
    key, uid = await _register(client)
    for _ in range(2):
        await _push_events(
            client,
            key,
            [_turn("conv-drain", f"c{i}", BASE + timedelta(minutes=i)) for i in range(7)],
        )

    start = await curation_service.curator_event_backlog(uid, INTERNAL, OLD)
    assert start["distinct_events"] == 7 and start["raw_rows"] == 14

    until = BASE + timedelta(hours=2)
    position, seen, rows_fed, runs = OLD, set(), 0, 0
    while True:
        events, _ = await curation_service._feed_events(
            uid, position, None, curation_service._MAX_EVENTS, wiki=INTERNAL
        )
        if not events:
            break
        rows_fed += len(events)
        seen.update((e["session_id"], e["content"]) for e in events)
        position = await curation_service.complete_through(uid, position, until, INTERNAL)
        runs += 1

    assert seen == {("conv-drain", f"c{i}") for i in range(7)}
    assert rows_fed == 9 and runs == 3
    assert position == until
    assert await curation_service.curator_event_backlog(uid, INTERNAL, position) == {
        "distinct_events": 0,
        "raw_rows": 0,
        "distinct_sessions": 0,
    }


@pytest.mark.asyncio
async def test_a_filed_session_yields_one_row_per_identity(client: AsyncClient, pool):
    """The feed joins sessions, end users, and folders after collapsing; all
    three are 1:1, so a filed session pushed twice is still one row."""
    key, uid = await _register(client)
    folder_id = await _file_session_into_folder(client, key, uid, pool, "conv-folder")
    await _push_events(client, key, [_turn("conv-folder", "filing the session", BASE)])

    events, has_more = await curation_service._feed_events(uid, OLD, None, 100, wiki=INTERNAL)
    assert (has_more, len(events)) == (False, 1)
    assert events[0]["session_folder"] == "Acme Corp"
    assert events[0]["session_folder_id"] == UUID(folder_id)


@pytest.mark.asyncio
async def test_the_changes_endpoint_publishes_distinct_and_raw(client: AsyncClient, pool):
    """`stash changes --json` reads this response, so the distinction has to
    survive the wire, and the counts it already used had to become distinct."""
    key, uid = await _register(client)
    turns = [_turn("conv-api", f"turn {i}", BASE + timedelta(minutes=i)) for i in range(3)]
    await _push_events(client, key, turns)
    await _push_events(client, key, turns)

    r = await client.get(
        "/api/v1/me/changes", params={"since": OLD.isoformat()}, headers=_auth(key)
    )
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["history"] == 3
    assert r.json()["event_backlog"] == {
        "distinct_events": 3,
        "raw_rows": 6,
        "distinct_sessions": 1,
    }


@pytest.mark.asyncio
async def test_the_curator_status_endpoint_publishes_the_shared_backlog(client: AsyncClient, pool):
    """The founder's console reads the shared wiki's backlog from the
    curator's own watermark — collapsed, and scoped the same way its feed is."""
    ahead = datetime.now(UTC) + timedelta(days=1)
    api_key, _, workspace = await _developer(client)
    machine_key = await _mint_workspace_key(client, api_key, workspace)
    shared = _event("sess-shared", "shared turn", user_id="org_acme", at=ahead)
    private = _event("sess-private", "private turn", user_id="org_choate", at=ahead)
    await _push(client, machine_key, [shared, private])
    await _push(client, machine_key, [shared, private])
    await _opt_out(pool, UUID(workspace["scope_user_id"]), "org_choate")

    r = await client.get(
        "/api/v1/me/developer/curator",
        headers={**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]},
    )
    assert r.status_code == 200, r.text

    backlog = r.json()["event_backlog"]
    assert backlog["distinct_events"] == 1
    assert backlog["raw_rows"] == 2
    assert backlog["distinct_sessions"] == 1
