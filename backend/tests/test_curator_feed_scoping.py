"""The external wiki's event feed is scoped to share_wiki sessions in SQL.

The rule "only share_wiki users feed the shared anonymized wiki" used to live
in the curator's prompt: the feed returned every owner event plus a
`user_share_wiki` column and asked the agent to route. That made a privacy
promise depend on an LLM following an instruction, and it made the external
curator read (and pay for) every event in the account — on the founder's
workspace ~265 559 to reach ~84 887 relevant ones.

These tests pin the three ways the scoping can silently break:

1. The feed leaks an opted-out session's events.
2. The cheap run gate disagrees with the feed — beat dispatches a curator
   whose feed is empty (wasted sprite wake + a metered run burned), or stays
   silent while the feed has work. Gate-true ⇔ feed-non-empty, both ways.
3. The watermark advance is scoped differently from the feed, so an
   overflow of irrelevant noise starves an opted-in session out of the run.

All three readers (`_feed_events`, `complete_through`, `has_changes_since`)
must consume ONE predicate, so they are exercised against the same fixtures.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import curation_service

from .test_curator import _register
from .test_developer_platform import _developer, _mint_workspace_key, _push

INTERNAL = "internal"
EXTERNAL = "external"

# Everything is pushed at a fixed instant; the watermark is far enough behind
# that ordering inside the run is decided by the feed, never by wall-clock skew.
BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
OLD = datetime(2020, 1, 1, tzinfo=UTC)

# For the gate tests: history that predates the watermark, and activity that
# follows it. Fixed instants, so no test here can flip on wall-clock skew.
WATERMARK = BASE + timedelta(hours=1)
LATER = BASE + timedelta(minutes=90)


async def _workspace(client: AsyncClient) -> tuple[UUID, str]:
    """An activated developer workspace: (scope owner id, machine key).

    Events are pushed with the machine key, so they land on the workspace's
    scope — the same scope both curators read."""
    api_key, _, workspace = await _developer(client)
    return UUID(workspace["scope_user_id"]), await _mint_workspace_key(client, api_key, workspace)


def _event(session_id: str, content: str, *, user_id: str | None = None, at: datetime = BASE):
    event = {
        "agent_name": "support-bot",
        "event_type": "user_message",
        "content": content,
        "session_id": session_id,
        "created_at": at.isoformat(),
    }
    if user_id is not None:
        event["user_id"] = user_id
        event["user_name"] = user_id.title()
    return event


async def _opt_out(pool, scope_user_id: UUID, external_id: str) -> None:
    """A user opts out — the contract path is the product UI; here it is the
    one column that flag writes."""
    row = await pool.fetchrow(
        "UPDATE end_users SET share_wiki = false WHERE external_id = $2"
        "  AND workspace_id = (SELECT id FROM workspaces WHERE scope_user_id = $1)"
        "  AND share_wiki RETURNING id",
        scope_user_id,
        external_id,
    )
    # Proves the fixture did what it claims: the row existed AND was sharing
    # before, so the test cannot pass because the user was never opted in.
    assert row is not None, f"{external_id} was not a sharing end user of this workspace"


# --- 1. the feed itself -----------------------------------------------------


@pytest.mark.asyncio
async def test_external_feed_excludes_opted_out_sessions_internal_keeps_them(
    client: AsyncClient, pool
):
    """SQL enforces what the prompt used to ask for.

    Four shapes of event, so a leak has one place to hide: an opted-in
    session, an opted-out session, a session with no end user, and an event
    whose session row no longer exists. The external wiki gets only the first;
    the internal wiki — the owner's own Memory wiki — must keep seeing every
    one of them, unchanged."""
    scope, key = await _workspace(client)
    await _push(
        client,
        key,
        [
            _event("sess-sharing", "part numbers for the Cascadia", user_id="acme"),
            _event("sess-opted-out", "the owner's private supplier list", user_id="secret-corp"),
            _event("sess-no-user", "the developer's own session"),
        ],
    )
    # An event whose session row is gone. `history_events.session_id` is a
    # VARCHAR, not a FK, and session rows do get hard-deleted
    # (backend/tasks/demo_janitor.py), so the feed can meet an event it cannot
    # attribute to any end user. (The spec asked for `session_id IS NULL`; that
    # column is NOT NULL, so this is the reachable shape of the same risk.)
    await pool.execute(
        "INSERT INTO history_events (owner_user_id, agent_name, event_type, session_id, content,"
        "  created_at) VALUES ($1, 'support-bot', 'user_message', 'sess-orphaned',"
        "  'a session that no longer exists', $2)",
        scope,
        BASE,
    )
    await _opt_out(pool, scope, "secret-corp")

    external = await curation_service.changes_since(scope, scope, OLD, wiki=EXTERNAL)
    assert [h["content"] for h in external["history"]] == ["part numbers for the Cascadia"]
    assert external["counts"]["history"] == 1

    internal = await curation_service.changes_since(scope, scope, OLD, wiki=INTERNAL)
    assert {h["content"] for h in internal["history"]} == {
        "part numbers for the Cascadia",
        "the owner's private supplier list",
        "the developer's own session",
        "a session that no longer exists",
    }


@pytest.mark.asyncio
async def test_end_user_share_wiki_defaults_to_true(pool):
    """The predicate is an opt-OUT filter, so it only starves what was
    deliberately opted out. If the column default ever flips, every end user
    silently vanishes from the shared wiki and no other test here notices."""
    default = await pool.fetchval(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_name = 'end_users' AND column_name = 'share_wiki'"
    )
    assert default is not None and "true" in default.lower()


# --- 2. the gate agrees with the feed, in both directions -------------------


async def _two_users(client: AsyncClient, pool, key: str, scope: UUID) -> None:
    """Both end users exist with history BEFORE the watermark, and one is
    opted out. So the only thing the gate or the feed can react to is the
    activity a test pushes after it — and no page, file, drive or save row is
    ever created here, which is what lets the gate's unscoped clauses stay
    silent instead of masking the assertion."""
    await _push(
        client,
        key,
        [
            _event("sess-sharing", "earlier shared work", user_id="acme"),
            _event("sess-opted-out", "earlier private work", user_id="secret-corp"),
        ],
    )
    await _opt_out(pool, scope, "secret-corp")


@pytest.mark.asyncio
async def test_gate_is_false_for_opted_out_activity_the_feed_cannot_see(client: AsyncClient, pool):
    """Gate-true ⇔ feed-non-empty, direction one.

    Only opted-out activity happened after the watermark. The unscoped gate
    says "run!" and the beat wakes a sprite, burns a metered run, and hands
    the curator an empty feed. The scoped gate must say no — and must say no
    for the external curator ONLY: the internal curator still has this work."""
    scope, key = await _workspace(client)
    await _two_users(client, pool, key, scope)

    await _push(
        client,
        key,
        [
            _event(
                "sess-opted-out", "private supplier list changed", user_id="secret-corp", at=LATER
            )
        ],
    )

    assert await curation_service.has_changes_since(scope, scope, WATERMARK, wiki=EXTERNAL) is False
    empty = await curation_service.changes_since(scope, scope, WATERMARK, wiki=EXTERNAL)
    assert empty["counts"]["history"] == 0

    # Same instant, same data, different wiki: the owner's curator must run.
    assert await curation_service.has_changes_since(scope, scope, WATERMARK, wiki=INTERNAL) is True


@pytest.mark.asyncio
async def test_gate_turns_true_when_an_opted_in_session_changes(client: AsyncClient, pool):
    """Direction two: real shareable work must not be gated away. A scoped
    gate that over-filters is worse than no gate — the shared wiki would
    quietly stop updating and nothing would report an error."""
    scope, key = await _workspace(client)
    await _two_users(client, pool, key, scope)

    await _push(
        client,
        key,
        [_event("sess-sharing", "new fault code on the Cascadia", user_id="acme", at=LATER)],
    )

    assert await curation_service.has_changes_since(scope, scope, WATERMARK, wiki=EXTERNAL) is True
    feed = await curation_service.changes_since(scope, scope, WATERMARK, wiki=EXTERNAL)
    assert [h["content"] for h in feed["history"]] == ["new fault code on the Cascadia"]


# --- 3. overflow can no longer starve an opted-in session -------------------


@pytest.mark.asyncio
async def test_noise_from_an_opted_out_user_cannot_starve_the_shared_wiki(
    client: AsyncClient, pool, monkeypatch
):
    """Why the fix matters, not just what it does.

    The feed is capped at _MAX_EVENTS and the watermark only advances through
    what fit, so with one shared query a flood from an opted-out user eats the
    slots and the watermark walks past... no: it STOPS before them, and the
    opted-in session waits behind every future flood. Scoping the feed means
    the external curator's budget holds only material it may use, so a run
    finishes the window it was given."""
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 5)
    scope, key = await _workspace(client)

    noise = [
        _event(
            "sess-opted-out",
            f"private note {i}",
            user_id="secret-corp",
            at=BASE + timedelta(minutes=i),
        )
        for i in range(6)
    ]
    needle = _event(
        "sess-sharing", "the shared lesson", user_id="acme", at=BASE + timedelta(minutes=3)
    )
    await _push(client, key, noise + [needle])
    await _opt_out(pool, scope, "secret-corp")
    until = BASE + timedelta(hours=1)

    external = await curation_service.changes_since(scope, scope, OLD, wiki=EXTERNAL)
    assert [h["content"] for h in external["history"]] == ["the shared lesson"]
    assert external["history_has_more"] is False
    # The window is complete for the external wiki, so the watermark reaches it.
    assert await curation_service.complete_through(scope, OLD, until, wiki=EXTERNAL) == until

    # The internal curator still sees the flood and still overflows — proof the
    # cap was live and that internal behaviour did not change.
    internal = await curation_service.changes_since(scope, scope, OLD, wiki=INTERNAL)
    assert internal["history_has_more"] is True
    assert len(internal["history"]) == 5
    assert await curation_service.complete_through(scope, OLD, until, wiki=INTERNAL) < until


# --- 4. the predicate is one definition, not a widening default -------------


@pytest.mark.asyncio
async def test_personal_scope_external_feed_is_empty_and_gate_is_false(client: AsyncClient, pool):
    """A non-developer account has sessions with no end user at all. Scoping
    the external feed must make that feed legitimately EMPTY — never 'fall
    back to everything'. An accidental widening (a missing EXISTS, an `OR`, a
    default of internal) shows up here as a non-empty external feed.

    A personal account has no external curator in the product, so this is the
    shape of the bug, not a shipped state: it is the predicate's floor."""
    key, uid = await _register(client)
    await _push_events_personal(client, key)
    assert await pool.fetchval("SELECT count(*) FROM end_users") == 0

    external = await curation_service.changes_since(uid, uid, OLD, wiki=EXTERNAL)
    assert external["counts"]["history"] == 0
    assert external["history"] == []
    assert await curation_service.has_changes_since(uid, uid, OLD, wiki=EXTERNAL) is False

    # Internal keeps every one of them.
    internal = await curation_service.changes_since(uid, uid, OLD, wiki=INTERNAL)
    assert internal["counts"]["history"] == 2
    assert await curation_service.has_changes_since(uid, uid, OLD, wiki=INTERNAL) is True


async def _push_events_personal(client: AsyncClient, key: str) -> None:
    await client.post(
        "/api/v1/me/sessions/events/batch",
        json={
            "events": [
                _event("conv-a", "personal note 1", at=BASE),
                _event("conv-b", "personal note 2", at=BASE + timedelta(minutes=1)),
            ]
        },
        headers={"Authorization": f"Bearer {key}"},
    )


@pytest.mark.asyncio
async def test_unknown_wiki_raises_naming_the_value(client: AsyncClient, pool):
    """`wiki` has exactly two values. A typo at a call site must not curate
    the wrong wiki or default to internal — it must fail where it is typed."""
    scope, _ = await _workspace(client)
    for call in (
        lambda: curation_service.changes_since(scope, scope, OLD, wiki="exteranl"),
        lambda: curation_service.has_changes_since(scope, scope, OLD, wiki="exteranl"),
        lambda: curation_service.complete_through(scope, OLD, BASE, wiki="exteranl"),
    ):
        with pytest.raises(ValueError, match="exteranl"):
            await call()
