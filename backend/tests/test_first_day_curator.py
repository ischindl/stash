"""The first-day curator: during a scope's first 24 hours the wiki updates
after every conversation, not just on the nightly tick — a user who just
signed up (or a developer who just activated the platform) watches the wiki
grow while they get set up. The behavior that matters: it only fires for
young scopes, it debounces so an event stream doesn't stack runs, and it
never fires with nothing new to curate.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import agent_auth
from backend.services.curation_service import Position
from backend.tasks.agent_schedules import _first_day_curator_tick, run_curator_now

from .conftest import unique_name
from .test_developer_platform import _developer, _event, _mint_workspace_key, _push
from .test_permissions import _register_with_email


@pytest.fixture
def dispatched(monkeypatch):
    """Capture dispatches for a scope whose curators can actually run.

    Two credentials are in play, and a dispatch needs whichever one its own run
    uses: a developer-platform workspace curates through the backend's key (no
    user credential), so ANTHROPIC_API_KEY must be set; every other curator
    — personal Memory, project folders — runs on the scope's credential, and
    STAS-131 removed the no-credential machine-login fallback, so it needs a
    resolvable endpoint (a connected local box, like the founder's own stack).
    Without the one that applies, the honest answer is no dispatch at all (see
    test_unconnected_scope_gets_no_doomed_dispatch).
    """
    from backend.config import settings
    from backend.tasks.session_titles import generate_session_title

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr(generate_session_title, "delay", lambda *a, **k: None)
    calls: list[tuple] = []
    monkeypatch.setattr(run_curator_now, "delay", lambda *a, **k: calls.append((a, k)))

    async def local_endpoint(user_id, provider=None):
        if provider == "local":
            return {
                "provider": "local",
                "kind": "endpoint",
                "secret": '{"base_url": "http://127.0.0.1:11434/v1", "model": "m", "thinking": "off"}',
            }
        return None

    monkeypatch.setattr(agent_auth, "_get_credential", local_endpoint)
    return calls


async def _workspace_with_conversation(
    client: AsyncClient, *, include_internal: bool = False, include_customer: bool = True
) -> UUID:
    api_key, _, workspace = await _developer(client)
    ws_key = await _mint_workspace_key(client, api_key, workspace)
    if include_customer:
        await _push(client, ws_key, [_event("s-1", user_id="cust-1", user_name="Cust")])
    if include_internal:
        await _push(client, ws_key, [_event("developer-own-conversation")])
    return UUID(workspace["scope_user_id"])


async def _dispatched_wikis(pool, dispatched) -> set[str]:
    wikis = set()
    for args, _ in dispatched:
        wikis.add(
            await pool.fetchval("SELECT curator_wiki FROM agents WHERE id = $1", UUID(args[0]))
        )
    return wikis


@pytest.mark.asyncio
@pytest.mark.parametrize("include_internal", [False, True])
async def test_first_day_conversation_dispatches_unmetered_runs(
    client: AsyncClient, pool, dispatched, include_internal
):
    scope_id = await _workspace_with_conversation(client, include_internal=include_internal)
    await _first_day_curator_tick(scope_id)
    # Only wikis with eligible input update. A customer-only conversation
    # cannot cause an empty run in the developer's own Memory wiki.
    expected = {"internal", "external"} if include_internal else {"external"}
    assert await _dispatched_wikis(pool, dispatched) == expected
    # The platform is the trigger, and it says so twice now: `metered=False`
    # because the platform pays for a trigger of its own, and
    # `platform_initiated=True` because only a curator the user left switched on
    # may be run by it. The two used to be one flag, which is why a user-triggered
    # run on a free-to-the-platform endpoint could have been silently dropped.
    assert all(kwargs == {"metered": False, "platform_initiated": True} for _, kwargs in dispatched)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversation", "opt_out", "expected"),
    [
        # The developer's own conversation is the Memory wiki's material alone:
        # an end-user event is not the developer's memory, and a developer event
        # is no customer's material.
        ("developer", False, {"internal"}),
        ("customer", False, {"external"}),
        # Withholding from the SHARED wiki does not withhold the customer's own
        # private wiki, which this same external curator maintains —
        # `scoped_curation_service.run` builds one private scope per end user,
        # whether or not that user shares. The gate therefore still fires; what
        # the withheld transcript can never reach is the shared wiki, and that
        # boundary is `CurationScope.check`, measured in
        # test_scoped_curation.py (revocation + protected-material cases).
        ("customer", True, {"external"}),
    ],
    ids=["developer-own", "sharing-customer", "opted-out-customer"],
)
async def test_each_curator_is_gated_by_its_own_feed(
    client: AsyncClient, pool, dispatched, conversation, opt_out, expected
):
    """Day-one dispatch asks each wiki separately because each one is gated by
    its own feed: neither curator may be roused for the other's material."""
    scope_id = await _workspace_with_conversation(
        client,
        include_internal=conversation == "developer",
        include_customer=conversation == "customer",
    )
    if opt_out:
        opted_out = await pool.fetchrow(
            "UPDATE end_users SET share_wiki = false WHERE external_id = 'cust-1'"
            "  AND workspace_id = (SELECT id FROM workspaces WHERE scope_user_id = $1)"
            "  AND share_wiki RETURNING id",
            scope_id,
        )
        # Proves the fixture did what it claims — the user existed and WAS
        # sharing, so the case cannot pass because the workspace never had an
        # end user to withhold.
        assert opted_out is not None, "cust-1 was not a sharing end user of this workspace"

    await _first_day_curator_tick(scope_id)
    assert await _dispatched_wikis(pool, dispatched) == expected


@pytest.mark.asyncio
async def test_paused_curator_stays_paused_on_first_day(client, pool, dispatched):
    """The first-day tick is a platform trigger, so it may never run a curator
    the user parked: `platform_initiated=True` is the trigger said in the open,
    not inferred from `metered=False` (which now also means "the platform pays
    nothing because this lane dials the customer's own endpoint")."""
    scope_id = await _workspace_with_conversation(client)
    await pool.execute(
        "UPDATE agents SET run_mode='chat' WHERE user_id=$1 AND is_curator", scope_id
    )
    await _first_day_curator_tick(scope_id)
    assert dispatched == []


@pytest.mark.asyncio
async def test_old_workspace_still_curates_its_own_memory(client: AsyncClient, pool, dispatched):
    scope_id = await _workspace_with_conversation(client, include_internal=True)
    await pool.execute(
        "UPDATE workspaces SET created_at = now() - interval '2 days' WHERE scope_user_id = $1",
        scope_id,
    )
    # The external wiki's first day is over; the scope user itself is still
    # fresh, so its own Memory wiki keeps updating eagerly.
    await _first_day_curator_tick(scope_id)
    assert await _dispatched_wikis(pool, dispatched) == {"internal"}


@pytest.mark.asyncio
async def test_old_scope_does_not_dispatch(client: AsyncClient, pool, dispatched):
    scope_id = await _workspace_with_conversation(client)
    await pool.execute(
        "UPDATE workspaces SET created_at = now() - interval '2 days' WHERE scope_user_id = $1",
        scope_id,
    )
    await pool.execute(
        "UPDATE users SET created_at = now() - interval '2 days' WHERE id = $1", scope_id
    )
    await _first_day_curator_tick(scope_id)
    assert dispatched == []


@pytest.mark.asyncio
async def test_recent_run_debounces(client: AsyncClient, pool, dispatched):
    scope_id = await _workspace_with_conversation(client, include_internal=True)
    await pool.execute(
        "UPDATE agents SET last_run_outcome = 'ran', last_run_at = now() "
        "WHERE user_id = $1 AND is_curator",
        scope_id,
    )
    await _first_day_curator_tick(scope_id)
    assert dispatched == []

    # Once the debounce window has passed, pending changes dispatch again.
    await pool.execute(
        "UPDATE agents SET last_run_at = $2 WHERE user_id = $1 AND is_curator",
        scope_id,
        datetime.now(UTC) - timedelta(minutes=11),
    )
    await _first_day_curator_tick(scope_id)
    assert await _dispatched_wikis(pool, dispatched) == {"internal", "external"}


async def _personal_user_with_conversation(client: AsyncClient) -> UUID:
    api_key, body = await _register_with_email(client, f"{unique_name('solo')}@example.com")
    resp = await client.post(
        "/api/v1/me/sessions/events",
        json={
            "agent_name": "claude-code",
            "event_type": "assistant_message",
            "content": "hello",
            "session_id": "s-personal-1",
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 201
    return UUID(body["id"])


@pytest.mark.asyncio
async def test_personal_first_day_conversation_dispatches_an_unmetered_run(
    client: AsyncClient, pool, dispatched
):
    user_id = await _personal_user_with_conversation(client)
    await _first_day_curator_tick(user_id)
    assert await _dispatched_wikis(pool, dispatched) == {"internal"}
    assert dispatched[0][1] == {"metered": False, "platform_initiated": True}


@pytest.mark.asyncio
async def test_old_personal_account_does_not_dispatch(client: AsyncClient, pool, dispatched):
    user_id = await _personal_user_with_conversation(client)
    await pool.execute(
        "UPDATE users SET created_at = now() - interval '2 days' WHERE id = $1", user_id
    )
    await _first_day_curator_tick(user_id)
    assert dispatched == []


@pytest.mark.asyncio
async def test_personal_scope_with_nothing_new_does_not_dispatch(client: AsyncClient, dispatched):
    _, body = await _register_with_email(client, f"{unique_name('solo')}@example.com")
    await _first_day_curator_tick(UUID(body["id"]))
    assert dispatched == []


async def _push_historical_event(client: AsyncClient, api_key: str, created_at: datetime) -> None:
    resp = await client.post(
        "/api/v1/me/sessions/events",
        json={
            "agent_name": "claude-code",
            "event_type": "assistant_message",
            "content": "an old conversation",
            "session_id": "s-imported-1",
            "created_at": created_at.isoformat(),
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_imported_pre_signup_history_dispatches(client: AsyncClient, pool, dispatched):
    """Imported sessions keep their original timestamps, which predate the
    curator's seeded curated_through. The import must pull curated_through
    back so the first-day run actually reads the history."""
    api_key, body = await _register_with_email(client, f"{unique_name('solo')}@example.com")
    user_id = UUID(body["id"])
    old = datetime.now(UTC) - timedelta(days=10)
    await _push_historical_event(client, api_key, old)
    curated_through = await pool.fetchval(
        "SELECT curated_through FROM agents WHERE user_id = $1 AND is_curator", user_id
    )
    assert curated_through < old
    await _first_day_curator_tick(user_id)
    assert await _dispatched_wikis(pool, dispatched) == {"internal"}


@pytest.mark.asyncio
async def test_unconnected_scope_gets_no_doomed_dispatch(client: AsyncClient, pool, monkeypatch):
    """STAS-131: with the machine-login fallback gone, a scope with no
    connected model has nothing to run — the tick must skip it instead of
    dispatching a run doomed to fail at exec time."""
    calls: list[tuple] = []
    monkeypatch.setattr(run_curator_now, "delay", lambda *a, **k: calls.append((a, k)))
    user_id = await _personal_user_with_conversation(client)
    await _first_day_curator_tick(user_id)
    assert calls == []


@pytest.mark.asyncio
async def test_late_import_reopens_curation(client: AsyncClient, pool, dispatched):
    """A curator that already ran has curated_through at its last run; an
    import of older sessions after that must still pull it back."""
    api_key, body = await _register_with_email(client, f"{unique_name('solo')}@example.com")
    user_id = UUID(body["id"])
    await pool.execute(
        "UPDATE users SET created_at = now() - interval '2 days' WHERE id = $1", user_id
    )
    await pool.execute(
        "UPDATE agents SET last_run_outcome = 'ran', last_run_at = now(), curated_through = now() "
        "WHERE user_id = $1 AND is_curator",
        user_id,
    )
    old = datetime.now(UTC) - timedelta(days=10)
    await _push_historical_event(client, api_key, old)
    curated_through = await pool.fetchval(
        "SELECT curated_through FROM agents WHERE user_id = $1 AND is_curator", user_id
    )
    assert curated_through < old
    # Past the first day nothing dispatches immediately — the nightly run
    # picks the import up, because the pending-changes gate now sees it.
    await _first_day_curator_tick(user_id)
    assert dispatched == []
    from backend.services import curation_service

    assert await curation_service.has_changes_since(
        user_id, user_id, Position(curated_through), wiki="internal"
    )
