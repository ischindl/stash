"""An opted-out customer's data must be unavailable, even to a disobedient curator."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from pydantic import ValidationError

from backend.services import (
    curation_service,
    end_user_service,
    llm,
    session_folder_service,
    source_service,
)
from backend.services import scoped_curation_service as curation
from backend.services.curation_service import Position

from .conftest import unique_name
from .test_developer_platform import _developer, _event, _mint_workspace_key, _push
from .test_permissions import _auth


@pytest_asyncio.fixture
async def dataset(client, pool):
    api_key, _, workspace = await _developer(client)
    key = await _mint_workspace_key(client, api_key, workspace)
    events = [
        _event("private-job", "private", "Protected customer"),
        _event("public-job", "public", "Participating customer"),
    ]
    events[0]["content"] = "SECRET_TRANSCRIPT: protected engine serial and service history"
    events[1]["content"] = "ALLOWED_TRANSCRIPT: reusable parts identification lesson"
    await _push(client, key, events)
    owner = UUID(workspace["scope_user_id"])
    users = {
        u["external_id"]: dict(u)
        for u in await pool.fetch(
            "SELECT * FROM end_users WHERE workspace_id=$1", UUID(workspace["id"])
        )
    }
    await end_user_service.update_end_user(users["private"]["id"], share_wiki=False)
    workspace = dict(
        await pool.fetchrow("SELECT * FROM workspaces WHERE id=$1", UUID(workspace["id"]))
    )
    pages = {}
    for name, user in users.items():
        pages[name] = await pool.fetchval(
            "INSERT INTO pages (owner_user_id,folder_id,name,content_markdown,created_by) "
            "VALUES ($1,$2,$3,$4,$1) RETURNING id",
            owner,
            user["wiki_folder_id"],
            f"{name} notes",
            f"{name.upper()}_WIKI_SECRET",
        )
    shared = await pool.fetchval(
        "INSERT INTO pages (owner_user_id,folder_id,name,content_markdown,created_by) "
        "VALUES ($1,$2,'Index','ALLOWED_SHARED_KNOWLEDGE',$1) RETURNING id",
        owner,
        workspace["external_wiki_folder_id"],
    )
    private_file = await pool.fetchval(
        "INSERT INTO files (owner_user_id,end_user_id,name,content_type,size_bytes,storage_key,"
        "uploaded_by,extracted_text,extraction_status) "
        "VALUES ($1,$2,'Protected upload','text/plain',12,'test-scoped-upload',$1,'SECRET_FILE','done') "
        "RETURNING id",
        owner,
        users["private"]["id"],
    )
    source = await source_service.create_source(
        owner_user_id=owner,
        source_type="google_drive_folder",
        external_ref=unique_name("drive"),
        display_name="Protected drive",
        end_user_id=users["private"]["id"],
    )
    source_doc = await pool.fetchval(
        "INSERT INTO drive_documents (owner_user_id,source_id,path,name,kind,content,extraction_status) "
        "VALUES ($1,$2,'secret','Protected source','file','SECRET_SOURCE','done') RETURNING id",
        owner,
        UUID(source["id"]),
    )
    return SimpleNamespace(
        workspace=workspace,
        owner=owner,
        users=users,
        pages=pages,
        shared=shared,
        private_file=private_file,
        source_doc=source_doc,
        key=key,
        api_key=api_key,
    )


async def scope(data, purpose, user="public"):
    if purpose == "shared":
        destination = data.workspace["external_wiki_folder_id"]
        users = [data.users["public"]["id"]]
    else:
        destination = data.users[user]["wiki_folder_id"]
        users = [data.users[user]["id"]]
    return await curation.load_scope(
        data.workspace,
        purpose,
        destination,
        users,
        "agent-curate-security-test",
        curation_service.NEVER,
        datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_shared_curator_cannot_discover_or_guess_protected_material(dataset):
    shared = await scope(dataset, "shared")
    corpus = json.dumps(shared.documents)
    for secret in ("SECRET_TRANSCRIPT", "PRIVATE_WIKI_SECRET", "SECRET_FILE", "SECRET_SOURCE"):
        assert secret not in corpus
    assert "ALLOWED_TRANSCRIPT" in corpus and "ALLOWED_SHARED_KNOWLEDGE" in corpus
    # Private wikis aren't shared inputs, including those of participating users.
    assert "PUBLIC_WIKI_SECRET" not in corpus
    for document_id in (
        str(dataset.pages["private"]),
        "session:private-job",
        f"file:{dataset.private_file}",
        f"source:{dataset.source_doc}",
    ):
        denied = await shared.tool("read_document", {"document_id": document_id})
        assert "unavailable" in denied["error"] and "content" not in denied
    with pytest.raises(PermissionError):
        await shared.tool("bash", {"command": "stash changes --json"})


@pytest.mark.asyncio
async def test_shared_completion_excludes_a_project_the_developer_has_not_cleared(dataset, pool):
    """Per-project clearance is the developer's veto over their own workspace: a
    project they have not cleared feeds nothing to the shared wiki, even when
    its sessions belong to an end user who shares their own history.

    It is asserted here, against the query that selects the shared completion's
    inputs, rather than against prompt prose: a rule the model is told can be
    disobeyed or quietly dropped by the next prompt rewrite, and the boundary
    this suite exists to prove is the one the backend enforces."""
    owner = dataset.owner

    async def file_the_participating_session(folder_id):
        await pool.execute(
            "UPDATE sessions SET session_folder_id=$2 WHERE session_id='public-job' "
            "AND owner_user_id=$1",
            owner,
            folder_id,
        )

    uncleared = (
        await session_folder_service.create_folder(owner, unique_name("uncleared-project"))
    )["id"]
    assert uncleared is not None
    await file_the_participating_session(uncleared)
    assert "ALLOWED_TRANSCRIPT" not in json.dumps((await scope(dataset, "shared")).documents)

    # Clearance is a toggle, not a verdict: clearing the project returns it to
    # the shared corpus with the same session in it.
    await pool.execute("UPDATE session_folders SET share_wiki=TRUE WHERE id=$1", uncleared)
    assert "ALLOWED_TRANSCRIPT" in json.dumps((await scope(dataset, "shared")).documents)

    # The Default folder is the unfiled catch-all, so it carries no routing
    # decision of its own: filing there must not withhold a sharing user's event.
    default = (await session_folder_service.ensure_default_folder(owner))["id"]
    await file_the_participating_session(default)
    assert "ALLOWED_TRANSCRIPT" in json.dumps((await scope(dataset, "shared")).documents)

    # An uncleared project governs the shared wiki only. The sharing user's
    # own private wiki keeps reading the same session: the veto is about what
    # crosses between users, never about what a user may curate for themselves.
    await pool.execute("UPDATE session_folders SET share_wiki=FALSE WHERE id=$1", uncleared)
    await file_the_participating_session(uncleared)
    assert "ALLOWED_TRANSCRIPT" in json.dumps((await scope(dataset, "private", "public")).documents)


@pytest.mark.asyncio
async def test_private_curator_reads_own_material_and_shared_knowledge_only(dataset):
    private = await scope(dataset, "private", "private")
    corpus = json.dumps(private.documents)
    assert all(
        s in corpus
        for s in ("SECRET_TRANSCRIPT", "PRIVATE_WIKI_SECRET", "SECRET_FILE", "SECRET_SOURCE")
    )
    assert "ALLOWED_SHARED_KNOWLEDGE" in corpus
    assert "ALLOWED_TRANSCRIPT" not in corpus and "PUBLIC_WIKI_SECRET" not in corpus
    for page in (dataset.shared, dataset.pages["public"]):
        with pytest.raises(PermissionError):
            await private.tool(
                "write_page", {"page_id": str(page), "title": "Leak", "content": "SECRET"}
            )
    with pytest.raises(ValidationError):
        await private.tool(
            "write_page",
            {
                "page_id": None,
                "title": "Leak",
                "content": "SECRET",
                "folder_id": str(dataset.workspace["external_wiki_folder_id"]),
            },
        )


@pytest.mark.asyncio
async def test_model_can_write_only_its_fixed_destination(dataset, pool):
    private = await scope(dataset, "private", "private")
    result = await private.tool(
        "write_page", {"page_id": None, "title": "Maintenance", "content": "SECRET_RECORD"}
    )
    page = await pool.fetchrow("SELECT * FROM pages WHERE id=$1", UUID(result["page_id"]))
    assert page["folder_id"] == dataset.users["private"]["wiki_folder_id"]
    assert page["end_user_id"] == dataset.users["private"]["id"]
    assert page["public_permission"] == "none"
    await private.tool(
        "write_page",
        {"page_id": str(page["id"]), "title": "Maintenance", "content": "Updated SECRET_RECORD"},
    )
    with pytest.raises(PermissionError):
        shared = await scope(dataset, "shared")
        await shared.tool(
            "write_page", {"page_id": str(page["id"]), "title": "Public", "content": "SECRET"}
        )


@pytest.mark.asyncio
async def test_revocation_invalidates_inflight_read_write_and_old_shared_corpus(
    dataset, client, pool
):
    shared = await scope(dataset, "shared")
    old_root = dataset.workspace["external_wiki_folder_id"]
    await end_user_service.update_end_user(dataset.users["public"]["id"], share_wiki=False)
    for tool, args in (
        ("read_document", {"document_id": str(dataset.shared)}),
        ("write_page", {"page_id": None, "title": "Leaked", "content": "ALLOWED_TRANSCRIPT"}),
    ):
        with pytest.raises(PermissionError, match="permissions changed"):
            await shared.tool(tool, args)
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "ALLOWED_SHARED_KNOWLEDGE"
    )
    assert (
        await pool.fetchval(
            "SELECT external_wiki_folder_id FROM workspaces WHERE id=$1", dataset.workspace["id"]
        )
        != old_root
    )
    for user_id in (None, "private", "public", "new-customer"):
        r = await client.post(
            "/api/v1/me/vfs",
            headers=_auth(dataset.key),
            json={"script": "grep -r ALLOWED_SHARED_KNOWLEDGE /memory /files", "user_id": user_id},
        )
        assert r.status_code == 200
        assert "ALLOWED_SHARED_KNOWLEDGE" not in r.json()["stdout"]


@pytest.mark.asyncio
async def test_shared_input_excludes_replayed_retrieval_results(dataset, client):
    event = _event("public-job", "public", "Participating customer")
    event.update(
        event_type="tool_result", tool_name="search_stash", content="OLD_COPIED_PRIVATE_DATA"
    )
    await _push(client, dataset.key, [event])
    assert "OLD_COPIED_PRIVATE_DATA" not in json.dumps((await scope(dataset, "shared")).documents)


@pytest.mark.asyncio
async def test_discovered_page_id_can_be_read_and_updated_without_translation(dataset):
    shared = await scope(dataset, "shared")
    listing = await shared.tool("search_documents", {"query": "Index"})
    page_id = listing["documents"][0]["id"]
    original = await shared.tool("read_document", {"document_id": page_id})
    assert original["content"] == "ALLOWED_SHARED_KNOWLEDGE"
    result = await shared.tool(
        "write_page",
        {"page_id": page_id, "title": "Index", "content": "Updated permitted knowledge."},
    )
    assert result["page_id"] == page_id
    updated = await shared.tool("read_document", {"document_id": page_id})
    assert updated["content"] == "Updated permitted knowledge."


@pytest.mark.asyncio
async def test_shared_reads_recheck_consent_even_without_a_generation_change(dataset, pool):
    shared = await scope(dataset, "shared")
    await pool.execute(
        "UPDATE end_users SET share_wiki=false WHERE id=$1", dataset.users["public"]["id"]
    )
    with pytest.raises(PermissionError, match="no longer shared"):
        await shared.tool("search_documents", {"query": ""})


@pytest.mark.asyncio
async def test_separate_model_contexts_and_no_sprite_execution(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, curation_service, sprite_agent_service

    calls = []
    all_started = asyncio.Event()

    # `route` arrives because run() hands every scope of a run the one provider it resolved
    async def fake_run(scope, instructions, route=None):
        calls.append((scope.purpose, json.dumps(scope.documents)))
        if len(calls) == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        return "Completed this isolated wiki."

    async def forbidden(*args, **kwargs):
        pytest.fail("Developer curator must never run in a credential-bearing sprite")

    monkeypatch.setattr(curation, "run_scope", fake_run)
    monkeypatch.setattr(sprite_agent_service, "run_chat", forbidden)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    # The whole corpus is this run's feed, so its position is nulled — but the
    # watermark fence anchors on the position the caller loaded, exactly as
    # `_run_curator_now` captures it before the same override.
    read = curation_service.position_of(agent)
    agent["curated_through"] = None
    await sprite_agent_service.run_scheduled(agent, "security-test", read)
    assert len([p for p, _ in calls if p == "private"]) == 2
    assert len([p for p, _ in calls if p == "shared"]) == 1
    assert all("SECRET_TRANSCRIPT" not in text for p, text in calls if p == "shared")
    assert any("SECRET_TRANSCRIPT" in text for p, text in calls if p == "private")
    assert sprite_exec.calls == [] and sprite_exec.writes == []


@pytest.mark.asyncio
async def test_internal_developer_curator_cannot_read_customer_inputs(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, curation_service, sprite_agent_service

    calls = []

    async def fake_run(scope, instructions, route=None):
        calls.append(scope)
        assert "SECRET_TRANSCRIPT" not in json.dumps(scope.documents)
        assert "ALLOWED_TRANSCRIPT" not in json.dumps(scope.documents)
        return "No private developer changes."

    monkeypatch.setattr(curation, "run_scope", fake_run)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="internal")
    read = curation_service.position_of(agent)
    agent["curated_through"] = None
    await sprite_agent_service.run_scheduled(agent, "security-test", read)
    assert len(calls) == 1 and calls[0].purpose == "internal"
    assert sprite_exec.calls == [] and sprite_exec.writes == []
    with pytest.raises(PermissionError):
        await sprite_agent_service.build_scheduled_turn(agent, "security-test")


@pytest.mark.asyncio
async def test_optout_during_model_request_blocks_publication(
    dataset, pool, monkeypatch, sprite_exec
):
    from anthropic.types import ToolUseBlock

    shared = await scope(dataset, "shared")

    async def create(**kwargs):
        await end_user_service.update_end_user(dataset.users["public"]["id"], share_wiki=False)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                ToolUseBlock(
                    type="tool_use",
                    id="write",
                    name="write_page",
                    input={
                        "page_id": None,
                        "title": "Leaked after revocation",
                        "content": "ALLOWED_TRANSCRIPT",
                    },
                )
            ],
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    with pytest.raises(PermissionError, match="permissions changed"):
        await curation.run_scope(shared, None)
    assert (
        await pool.fetchval("SELECT count(*) FROM pages WHERE name='Leaked after revocation'") == 0
    )


@pytest.mark.asyncio
async def test_native_model_loop_uses_fresh_messages_and_restricted_tools(
    dataset, monkeypatch, sprite_exec
):
    from anthropic.types import TextBlock, ToolUseBlock

    private = await scope(dataset, "private", "private")
    shared = await scope(dataset, "shared")
    requests = []

    async def create(**kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        assert {t["name"] for t in kwargs["tools"]} == {
            "search_documents",
            "read_document",
            "write_page",
        }
        if len(requests) == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="read",
                        name="read_document",
                        input={"document_id": "session:private-job"},
                    )
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="Done.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    await curation.run_scope(private, None)
    await curation.run_scope(shared, None)
    assert "SECRET_TRANSCRIPT" in json.dumps(requests[1]["messages"])
    assert "SECRET_TRANSCRIPT" not in json.dumps(requests[2])
    assert len(requests[2]["messages"]) == 1


@pytest.mark.asyncio
async def test_denied_lookup_is_an_explicit_tool_error_without_protected_content(
    dataset, monkeypatch, sprite_exec
):
    from anthropic.types import TextBlock, ToolUseBlock

    shared = await scope(dataset, "shared")
    requests = []

    async def create(**kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        if len(requests) == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="denied",
                        name="read_document",
                        input={"document_id": "session:private-job"},
                    )
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="No update needed.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    await curation.run_scope(shared, None)
    result = requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "unavailable" in result["content"]
    assert "SECRET_TRANSCRIPT" not in json.dumps(requests)


@pytest.mark.asyncio
async def test_truncated_response_cannot_execute_partial_page_writes(
    dataset, pool, monkeypatch, sprite_exec
):
    from anthropic.types import ToolUseBlock

    async def create(**kwargs):
        return SimpleNamespace(
            stop_reason="max_tokens",
            content=[
                ToolUseBlock(
                    type="tool_use",
                    id="truncated",
                    name="write_page",
                    input={"page_id": None, "title": "Partial page", "content": "Incomplete"},
                )
            ],
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    with pytest.raises(RuntimeError, match="max_tokens"):
        await curation.run_scope(await scope(dataset, "shared"), None)
    assert await pool.fetchval("SELECT count(*) FROM pages WHERE name='Partial page'") == 0


@pytest.mark.asyncio
async def test_missing_backend_key_fails_without_dispatch(dataset, client, monkeypatch):
    from backend.tasks import agent_schedules

    monkeypatch.setattr(curation.settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "delay", lambda *a, **k: pytest.fail("Not configured")
    )
    r = await client.post(
        "/api/v1/me/developer/curator/run",
        headers={**_auth(dataset.api_key), "X-Stash-Scope": str(dataset.owner)},
    )
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


@pytest.mark.asyncio
async def test_developer_prompt_preview_describes_scoped_runner(dataset):
    from backend.routers.agents import get_agent_prompt
    from backend.services import agent_service

    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    preview = await get_agent_prompt(UUID(agent["id"]), current_user={"id": dataset.owner})
    assert "separate" in preview["system_prompt"].lower()
    assert "stash changes" not in preview["system_prompt"]


@pytest.mark.asyncio
async def test_failed_shared_run_does_not_report_success_or_advance_watermark(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, curation_service, sprite_agent_service

    private_started = set()
    private_cancelled = set()
    all_private_started = asyncio.Event()

    async def fail_shared(scope, instructions, route=None):
        if scope.purpose == "shared":
            await asyncio.wait_for(all_private_started.wait(), timeout=1)
            raise PermissionError("Publication denied")
        private_started.add(scope.destination)
        if len(private_started) == 2:
            all_private_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            private_cancelled.add(scope.destination)
            raise

    monkeypatch.setattr(curation, "run_scope", fail_shared)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    before = await pool.fetchval(
        "SELECT curated_through FROM agents WHERE id=$1", UUID(agent["id"])
    )
    read = curation_service.position_of(agent)
    agent["curated_through"] = None
    with pytest.raises(ExceptionGroup) as failure:
        await sprite_agent_service.run_scheduled(agent, "failed-test", read)
    assert any(isinstance(exc, PermissionError) for exc in failure.value.exceptions)
    assert private_cancelled == private_started and len(private_cancelled) == 2
    assert (
        await pool.fetchval("SELECT curated_through FROM agents WHERE id=$1", UUID(agent["id"]))
        == before
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM history_events WHERE session_id LIKE 'agent-curate-%' AND event_type='assistant_message'"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_scoped_run_timeout_preserves_progress_and_releases_lock(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    async def stuck(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(curation, "run_scope", stuck)
    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", 0.05)
    # Compressed but not zero: this slice never returns, so the wall is the mechanism, and
    # a grace of 0 would collapse the wall onto the budget and prove nothing. Left at the
    # real window this test would wait out a whole completion.
    monkeypatch.setattr(llm, "LOCAL_COMPLETION_TIMEOUT_S", 0.05)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    before = await pool.fetchval(
        "SELECT curated_through FROM agents WHERE id=$1", UUID(agent["id"])
    )
    with pytest.raises(RuntimeError, match="run time limit"):
        await _run_curator_now(UUID(agent["id"]), metered=False)
    row = await pool.fetchrow(
        "SELECT curated_through,last_run_outcome,last_run_error FROM agents WHERE id=$1",
        UUID(agent["id"]),
    )
    assert row["curated_through"] == before
    assert row["last_run_outcome"] == "failed" and "run time limit" in row["last_run_error"]
    assert sprite_exec.redis.data == {}


# --- One run walks its backlog in slices under a budget of its own (STAS-266, measured
# on the founder stack 2026-09-23). A run used to bound every event still owed into one
# window and commit once, at the end, so a backlog larger than one budget died at the
# ceiling with the watermark exactly where it started: 600 s on a local model, 1.27M
# events ahead, `curated_through` unmoved since 2026-08-22, retried once a day forever.
# Each slice now commits through the same fenced compare-and-set every other forward
# writer obeys, the next slice reads from the pair that commit left behind, and a run
# that runs out of time stays a loud failure that keeps the slices it finished.

_SEEDED = 5  # events _seed_backlog pushes, one per session
_BUDGET = 2.0  # a slice's work is DB-only here, so one slice always lands inside this


async def _seed_backlog(
    client, dataset, monkeypatch, count: int = _SEEDED, at: datetime | None = None
):
    """Push `count` events into the feed of the customer who shares their transcript.

    One event per session, so each is a distinct event the feed has to show. `at` pins
    them all to one instant, which is what forces a slice boundary to fall inside a
    tie rather than between two instants.

    Ingest of a title-bearing event queues a title job, and these tests run with the
    fake provider key `sprite_exec` installs and with no Celery broker to take it.
    """
    from backend.tasks.session_titles import generate_session_title

    monkeypatch.setattr(generate_session_title, "delay", lambda *a, **k: None)
    events = []
    for i in range(count):
        event = _event(f"sess-slice-{i}", "public", "Participating customer")
        instant = at if at is not None else datetime.now(UTC) + timedelta(milliseconds=i)
        event["created_at"] = instant.isoformat()
        events.append(event)
    await _push(client, dataset.key, events)


def _curates_without_a_model():
    async def fake(scope, instructions, route=None):
        return "Curated this slice."

    return fake


def _stall_once_a_slice_is_committed(curator: UUID, read: Position, budget: float):
    """A `run_scope` that curates instantly until one slice is committed behind it, then
    outlives the run's whole budget.

    A slice only starts once the slice before it committed, so a stored watermark ahead
    of where the run began is proof the first slice landed. Stalling on that fact rather
    than on the clock makes the timeout deterministic, and it cannot fire before the run
    has something durable to keep — which is the property these tests then assert.
    """

    async def fake(scope, instructions, route=None):
        from backend.services import agent_service

        stored = curation_service.position_of(await agent_service.get_agent_by_id(curator))
        if stored.ahead(read):
            await asyncio.sleep(budget * 2)
        return "Curated this slice."

    return fake


def _record_windows(monkeypatch) -> list[tuple]:
    """Capture every scope load a run performs, as (purpose, since, until, corpus).

    A slice loads one scope per wiki it walks, so one window is recorded per scope, and
    the shared scope belongs to every slice: the `shared` series is therefore one entry
    per slice. `since` and `until` are the bounds the load was really given, so a slice
    that resumed at an instant instead of at the stored pair — or that read from past a
    position its previous slice never committed — is visible here rather than inferred.
    """
    windows: list[tuple] = []
    real = curation.load_scope

    async def spy(workspace, purpose, destination, user_ids, session_id, since, until):
        scope = await real(workspace, purpose, destination, user_ids, session_id, since, until)
        windows.append((purpose, since, until, json.dumps(scope.documents)))
        return scope

    monkeypatch.setattr(curation, "load_scope", spy)
    return windows


def _slices(windows: list[tuple]) -> list[tuple]:
    """The recorded slices — one per slice — as (since, until, corpus)."""
    return [w[1:] for w in windows if w[0] == "shared"]


def _record_commits(monkeypatch) -> list[Position]:
    """Every position a slice actually committed, in the order it committed them.

    The window a slice loads is not the position it earns: a slice that ran out of time
    loaded a window and never committed it. What a run may claim is what the
    compare-and-set accepted, so this spies on that one statement and records what it
    stored — which is also the fence the next slice has to honour.
    """
    from backend.services import agent_service

    committed: list[Position] = []
    real = agent_service.advance_watermark

    async def spy(conn, agent_id, read, through):
        stored = await real(conn, agent_id, read, through)
        committed.append(stored)
        return stored

    monkeypatch.setattr(agent_service, "advance_watermark", spy)
    return committed


def _seeded(corpus: str) -> set[int]:
    """Which seeded sessions a recorded window actually put in front of the model."""
    return {i for i in range(_SEEDED) if f"hello from sess-slice-{i}" in corpus}


@pytest.mark.asyncio
async def test_a_run_slices_a_backlog_larger_than_one_feed_window(
    dataset, client, pool, monkeypatch, sprite_exec
):
    """The founder's case in miniature: the backlog holds more than one feed window."""
    from backend.services import agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    monkeypatch.setattr(curation, "run_scope", _curates_without_a_model())
    await _seed_backlog(client, dataset, monkeypatch)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = curation_service.position_of(agent)
    assert read == curation_service.NEVER  # the lane never ran, so everything is owed
    windows = _record_windows(monkeypatch)
    commits = _record_commits(monkeypatch)

    await _run_curator_now(curator, metered=False)

    slices = _slices(windows)
    # Six events owed, two to a feed window: three slices. One run that reads all six
    # in one window is the shape that used to commit nothing until all six were curated.
    assert len(slices) == 3
    assert len(commits) == 3  # every slice committed, none of them waited on the others
    assert slices[0][0] == read  # the first slice reads where the dispatcher loaded
    for committed, next_slice in zip(commits, slices[1:]):
        # The next slice reads the exact pair this one committed, event id included.
        assert next_slice[0] == committed
    stored = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    assert commits[-1].event_id is None  # the run stopped on a whole instant, then stopped
    assert stored == commits[-1]  # and the marker says exactly that
    assert _seeded("".join(corpus for _, _, corpus in slices)) == set(range(_SEEDED))
    backlog = await curation_service.curator_event_backlog(
        dataset.owner, curation_service.WIKI_EXTERNAL, stored
    )
    assert backlog["distinct_events"] == 0  # the backlog drained, rather than being deferred


@pytest.mark.asyncio
async def test_a_run_that_runs_out_of_time_keeps_the_slices_it_committed(
    dataset, client, pool, monkeypatch, sprite_exec
):
    """The wall's exit: a slice that never returns is cancelled and lost whole, and the
    run keeps only what the slices before it committed.

    Both exits carry one message on purpose, so the cause and the lost slice are what
    tell them apart. This one closes on the caller's `wait_for` and carries its
    `TimeoutError`; the run's own stop is pinned by
    test_the_run_stops_itself_at_the_next_boundary_and_keeps_the_slice_that_arrived_late,
    which leaves the completion window alone so the wall sits 300 s away, and carries no
    cause at all while keeping the slice that arrived late."""
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", _BUDGET)
    # The wall sits one completion window past the budget: 2.0 + 1 = 3.0 s. That is past
    # the budget, so the run's own boundary check had its chance and the slice was
    # already in flight, and inside the stall's 2 × budget = 4.0 s, so the wall is what
    # cuts it. Zero would collapse the wall onto the budget — the shape this file exists
    # to rule out — and 2.0 or more would move the wall past the stall and hand the run
    # back to its own stop.
    monkeypatch.setattr(llm, "LOCAL_COMPLETION_TIMEOUT_S", 1)
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    await _seed_backlog(client, dataset, monkeypatch)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = curation_service.position_of(agent)
    monkeypatch.setattr(
        curation,
        "run_scope",
        _stall_once_a_slice_is_committed(curator, read, _BUDGET),
    )
    windows = _record_windows(monkeypatch)
    commits = _record_commits(monkeypatch)

    with pytest.raises(RuntimeError, match="run time limit") as stop:
        await _run_curator_now(curator, metered=False)

    assert isinstance(stop.value.__cause__, TimeoutError)  # the wall cancelled a slice
    slices = _slices(windows)
    stored = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    assert stored.ahead(read)  # the watermark moved at all
    assert stored == commits[-1]  # to exactly the position a slice actually committed
    # The slice in flight is gone: it had already loaded its window, so it counts as a
    # slice, and it committed nothing, so it is missing from the commits. That gap is
    # what separates this exit from the run's own stop, which keeps the late slice.
    assert len(commits) < len(slices) == 2
    row = await pool.fetchrow(
        "SELECT last_run_outcome,last_run_error FROM agents WHERE id=$1", curator
    )
    assert row["last_run_outcome"] == "failed"  # loud, never a quiet partial success
    assert "run time limit" in row["last_run_error"]
    backlog = await curation_service.curator_event_backlog(
        dataset.owner, curation_service.WIKI_EXTERNAL, stored
    )
    assert 0 < backlog["distinct_events"] < 6  # less owed than at the start, not all of it
    assert sprite_exec.redis.data == {}


@pytest.mark.asyncio
async def test_the_run_stops_itself_at_the_next_boundary_and_keeps_the_slice_that_arrived_late(
    dataset, client, pool, monkeypatch, sprite_exec
):
    """A slice that lands after the budget is spent is the run's own business, not the
    wall's: the run closes at the next boundary and keeps that slice.

    Both exits carry the same message on purpose — the founder reads one failure, "ran
    out of time" — so what tells them apart is the cause. The run's own between-slice
    check raises with nothing beneath it; the caller's wall raises *from* a cancelled
    slice, so its `__cause__` is the `TimeoutError` that killed it, and the slice that
    was in flight is gone. Only the first of these keeps work the model already finished.

    This patches the budget and pointedly not the completion window the wall sits past
    it, so the wall is ~300 s away and cannot be what stopped this run.
    """
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", _BUDGET)
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    await _seed_backlog(client, dataset, monkeypatch)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = curation_service.position_of(agent)
    monkeypatch.setattr(
        curation,
        "run_scope",
        _stall_once_a_slice_is_committed(curator, read, _BUDGET),
    )
    windows = _record_windows(monkeypatch)
    commits = _record_commits(monkeypatch)

    with pytest.raises(RuntimeError, match="run time limit") as stop:
        await _run_curator_now(curator, metered=False)

    assert stop.value.__cause__ is None  # the run raised; no slice was cancelled to do it
    slices = _slices(windows)
    # The late slice committed, and the run stopped rather than beginning a third.
    assert len(commits) == len(slices) == 2
    stored = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    assert stored == commits[-1]  # the marker claims exactly what a slice earned
    row = await pool.fetchrow(
        "SELECT last_run_outcome,last_run_error FROM agents WHERE id=$1", curator
    )
    assert row["last_run_outcome"] == "failed"  # a half-curated backlog still reads loud
    assert "run time limit" in row["last_run_error"]
    backlog = await curation_service.curator_event_backlog(
        dataset.owner, curation_service.WIKI_EXTERNAL, stored
    )
    assert 0 < backlog["distinct_events"] < 6  # less owed than at the start, not all of it
    assert sprite_exec.redis.data == {}  # both locks released on the run's own exit


@pytest.mark.asyncio
async def test_the_next_run_resumes_at_the_watermark_the_last_run_committed(
    dataset, client, pool, monkeypatch, sprite_exec
):
    """A timed-out run has to leave the next one less to do, or the backlog never drains:
    the second run reads from the position the first one proved, so it shows the model
    neither the events already curated nor the ones still ahead unread.

    This is also the scoped lane's only proof of what a recovery looks like to the
    founder: the first run left the row `failed` with a cause, and this one has to hand
    back `ran` with the cause gone. The outcome half is the half nobody asserted —
    `test_curator.py::test_failed_run_records_error_and_refunds_credit` covers the same
    recovery in the harness lane and reads only the error column."""
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", _BUDGET)
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    await _seed_backlog(client, dataset, monkeypatch)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = curation_service.position_of(agent)
    monkeypatch.setattr(
        curation,
        "run_scope",
        _stall_once_a_slice_is_committed(curator, read, _BUDGET),
    )
    windows = _record_windows(monkeypatch)
    with pytest.raises(RuntimeError, match="run time limit"):
        await _run_curator_now(curator, metered=False)
    first = _slices(windows)
    resumed_at = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    assert resumed_at.ahead(read)

    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", 3600.0)
    monkeypatch.setattr(curation, "run_scope", _curates_without_a_model())
    windows.clear()
    await _run_curator_now(curator, metered=False)

    second = _slices(windows)
    assert second[0][0] == resumed_at  # it read the new watermark, not 2026-08-22
    assert second[0][0] != read
    # What the first run EARNED is two slices: one that fitted inside the budget, and
    # one that came back after the budget was spent and was kept, because the run stops
    # itself at the next boundary instead of being cut mid-flight (STAS-364). Only the
    # events no slice committed are read again — the watermark still never claims
    # material that is not behind it.
    assert _seeded(first[0][2]) == {0}
    assert _seeded("".join(corpus for _, _, corpus in first)) == {0, 1, 2}
    assert _seeded("".join(corpus for _, _, corpus in second)) == {3, 4}
    assert len(second) == 1  # the debt the stop left: two events, one window
    stored = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    row = await pool.fetchrow(
        "SELECT last_run_outcome,last_run_error FROM agents WHERE id=$1", curator
    )
    # Both halves of a recovery, not one of them: `mark_run` wiped the cause as this run
    # started and `mark_run_succeeded` resolved the outcome, so nothing a founder reads
    # still says the lane timed out.
    assert row["last_run_outcome"] == "ran"
    assert row["last_run_error"] is None
    backlog = await curation_service.curator_event_backlog(
        dataset.owner, curation_service.WIKI_EXTERNAL, stored
    )
    assert backlog["distinct_events"] == 0  # the two runs together emptied it


@pytest.mark.asyncio
async def test_a_slice_cut_in_the_middle_of_a_tie_resumes_at_that_event(
    dataset, client, pool, monkeypatch, sprite_exec
):
    """A push lands hundreds of events on one `created_at`, so a slice boundary inside
    such an instant is the common case. The position it stores names the event it
    consumed, and a slice that resumed at the instant alone could not express that: the
    rest of the instant would be curated by nobody while the watermark — which does
    count those events — walked past them.
    """
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", _BUDGET)
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    tied = datetime.now(UTC)
    await _seed_backlog(client, dataset, monkeypatch, at=tied)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = curation_service.position_of(agent)
    monkeypatch.setattr(
        curation,
        "run_scope",
        _stall_once_a_slice_is_committed(curator, read, _BUDGET),
    )
    windows = _record_windows(monkeypatch)
    with pytest.raises(RuntimeError, match="run time limit"):
        await _run_curator_now(curator, metered=False)

    first = _slices(windows)
    stopped_at = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    assert stopped_at.at == tied  # the slice it committed ended inside the tied instant
    assert stopped_at.event_id is not None  # and it says which event of it it consumed

    monkeypatch.setattr(sprite_agent_service.settings, "CURATOR_RUN_TIMEOUT_SECONDS", 3600.0)
    monkeypatch.setattr(curation, "run_scope", _curates_without_a_model())
    windows.clear()
    await _run_curator_now(curator, metered=False)

    second = _slices(windows)
    assert second[0][0] == stopped_at  # resumed at that event, not at its instant
    assert _seeded("".join(corpus for _, _, corpus in first + second)) == set(range(_SEEDED))
    stored = curation_service.position_of(await agent_service.get_agent_by_id(curator))
    backlog = await curation_service.curator_event_backlog(
        dataset.owner, curation_service.WIKI_EXTERNAL, stored
    )
    assert backlog["distinct_events"] == 0


# --- The scoped lane commits its watermark under the same compare-and-set every
# other forward writer already uses (STAS-250). Before this, `run` stamped its own
# boundary with a bare UPDATE, so the fence `mark_curated` enforces for the Memory
# and folder curators stopped at the one lane that also moves the marker forward.


@pytest.mark.asyncio
async def test_a_scoped_run_cannot_discard_an_overlapping_run_watermark_advance(
    dataset, pool, monkeypatch, sprite_exec
):
    """An overlapping run advanced this lane mid-turn: its progress stands, this
    run's completion is refused loud, and the refused run claims no completion in
    the curator log. This is the founder's 2026-09-05 loss (one run discarding
    another's finished curation) reached through the second forward writer — the
    lane's unconditional stamp discarded the other run's advance without a word."""
    from backend.services import agent_service, curation_service
    from backend.tasks.agent_schedules import _run_curator_now

    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = datetime.now(UTC) - timedelta(minutes=5)
    await pool.execute("UPDATE agents SET curated_through=$2 WHERE id=$1", curator, read)
    advanced = curation_service.Position(datetime.now(UTC) + timedelta(minutes=10))

    overlapped = False

    async def overlapping_run_finished(scope, instructions, route=None):
        # The other run started from this same stored position, so its own
        # compare-and-set matches and its advance lands once, while this run is
        # mid-turn. The guard is the other run's single completion: this run's
        # fan-out visits several scopes, and one curation run advances the marker
        # once however many scopes it walks.
        nonlocal overlapped
        if not overlapped:
            overlapped = True
            await agent_service.mark_curated(curator, curation_service.Position(read), advanced)
        return "Completed this isolated wiki."

    monkeypatch.setattr(curation, "run_scope", overlapping_run_finished)
    with pytest.raises(agent_service.CuratorWatermarkConflict):
        await _run_curator_now(curator, metered=False)

    row = await pool.fetchrow(
        "SELECT curated_through,curated_through_event_id,last_run_outcome FROM agents WHERE id=$1",
        curator,
    )
    assert row["curated_through"] == advanced.at  # the other run's advance survives exactly
    assert row["curated_through_event_id"] == advanced.event_id
    assert row["last_run_outcome"] == "failed"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM history_events "
            "WHERE session_id LIKE 'agent-curate-%' AND event_type='assistant_message'"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_a_scoped_run_cannot_swallow_a_concurrent_reset_to_never(
    dataset, pool, monkeypatch, sprite_exec
):
    """The reset-to-NULL class, which only a compare-and-set can see: revoking a
    shared wiki clears its curator's pair — what `end_user_service`'s `_archive_shared_wiki`
    does — so re-sharing re-curates from the start. A run that read before the reset holds
    a position the stored value no longer is, so its advance is refused: stamped over the
    NULL it would have closed the corpus the reset deliberately re-opened."""
    from backend.services import agent_service, curation_service
    from backend.tasks.agent_schedules import _run_curator_now

    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = datetime.now(UTC) - timedelta(minutes=5)
    await pool.execute("UPDATE agents SET curated_through=$2 WHERE id=$1", curator, read)

    async def revoked_mid_turn(scope, instructions, route=None):
        # The revocation's own write, in its own shape: both halves cleared.
        await pool.execute(
            "UPDATE agents SET curated_through=NULL,curated_through_event_id=NULL WHERE id=$1",
            curator,
        )
        return "Completed this isolated wiki."

    monkeypatch.setattr(curation, "run_scope", revoked_mid_turn)
    with pytest.raises(agent_service.CuratorWatermarkConflict):
        await _run_curator_now(curator, metered=False)

    row = await pool.fetchrow(
        "SELECT curated_through,curated_through_event_id,last_run_outcome FROM agents WHERE id=$1",
        curator,
    )
    assert row["curated_through"] is None and row["curated_through_event_id"] is None
    assert row["last_run_outcome"] == "failed"
    # The window the reset re-opened is still open — nothing was skipped unread.
    assert await curation_service.has_changes_since(
        dataset.owner, dataset.owner, curation_service.NEVER, curation_service.WIKI_EXTERNAL
    )


@pytest.mark.asyncio
async def test_a_full_history_scoped_run_advances_the_position_it_read(
    dataset, pool, monkeypatch, sprite_exec
):
    """`full_history` re-reads the whole corpus from `never` and stays
    compare-and-set on the position the dispatcher loaded, so a backfill onto a lane
    that already curated is an ordinary advance rather than a conflict. The fence
    anchors on the loaded position, not on the position the override nulled for the
    feed — anchored on the nulled one, a backfill could never write at all."""
    from backend.services import agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    curator = UUID(agent["id"])
    read = datetime.now(UTC) - timedelta(minutes=5)
    await pool.execute("UPDATE agents SET curated_through=$2 WHERE id=$1", curator, read)

    async def curated(scope, instructions, route=None):
        return "Completed this isolated wiki."

    monkeypatch.setattr(curation, "run_scope", curated)
    await _run_curator_now(curator, metered=False, full_history=True)

    row = await pool.fetchrow(
        "SELECT curated_through,curated_through_event_id,last_run_outcome,last_run_error "
        "FROM agents WHERE id=$1",
        curator,
    )
    assert row["curated_through"] > read  # the backfill landed, it was not refused
    assert row["last_run_outcome"] == "ran", row["last_run_error"]
