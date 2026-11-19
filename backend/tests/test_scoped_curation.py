"""An opted-out customer's data must be unavailable, even to a disobedient curator."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from pydantic import ValidationError

from backend.services import curation_service, end_user_service, source_service
from backend.services import scoped_curation_service as curation

from .conftest import unique_name
from .shared_wiki_helpers import approve_fixture_pages
from .test_developer_platform import _developer, _event, _mint_workspace_key, _push
from .test_permissions import _auth


def hosted_route() -> curation.llm.Route:
    """The provider the faked `_get_client` in this file answers for.

    A scoped run resolves its provider once — up in `run()`, before it fans out —
    and hands that route to each of its scopes, so a test driving one scope
    directly has to say which provider it is speaking to. Every `_get_client`
    fake here is the Anthropic client, and this is the route that client serves.
    """
    return curation.llm.anthropic_route()


@pytest_asyncio.fixture
async def dataset(client, pool, monkeypatch):
    async def approve(**kwargs):
        return curation.shared_wiki_privacy.PrivacyReview(approved=True, issues=[])

    monkeypatch.setattr(curation.shared_wiki_privacy.llm, "complete_structured", approve)
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
    from backend.services import scoped_curation_inputs

    async with curation.get_pool().acquire() as conn:
        await approve_fixture_pages(conn, data.workspace)
    if purpose == "shared":
        destination = data.workspace["external_wiki_folder_id"]
        users = [data.users["public"]["id"]]
    else:
        destination = data.users[user]["wiki_folder_id"]
        users = [data.users[user]["id"]]
    batch = await scoped_curation_inputs.load_batch(
        data.owner,
        None,
        datetime.now(UTC),
        internal=purpose == "internal",
        generation=data.workspace["curation_generation"],
        max_chars=2048,
    )
    return await curation.load_scope(
        data.workspace,
        purpose,
        destination,
        users,
        "agent-curate-security-test",
        batch=batch,
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
        denied = await private.tool(
            "write_page", {"page_id": str(page), "title": "Leak", "content": "SECRET"}
        )
        assert "outside" in denied["error"]
    denied = await private.tool(
        "write_page",
        {
            "page_id": None,
            "title": "Leak",
            "content": "SECRET",
            "folder_id": str(dataset.workspace["external_wiki_folder_id"]),
        },
    )
    assert "Invalid tool arguments" in denied["error"]
    assert denied["fields"][0]["field"] == "folder_id"


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
    shared = await scope(dataset, "shared")
    denied = await shared.tool(
        "write_page", {"page_id": str(page["id"]), "title": "Public", "content": "SECRET"}
    )
    assert "outside" in denied["error"]


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


async def _from_never(pool, agent: dict) -> dict:
    """Stand a curator's row at 'never' and hand back the row as the database holds it.

    A created curator seeds its delta watermark to a bounded backfill point, so a
    test that means the bootstrap-from-real-history run has to move the row, not
    only the dict it was handed: the completion fences its watermark advance on the
    position the run read, and that position is the row's, never a local edit's.
    """
    agent_id = UUID(str(agent["id"]))
    await pool.execute(
        "UPDATE agents SET curated_through = NULL, curated_through_event_id = NULL WHERE id = $1",
        agent_id,
    )
    return dict(await pool.fetchrow("SELECT * FROM agents WHERE id = $1", agent_id))


def _reserve_nothing_for_the_commit(monkeypatch) -> None:
    """Make a test's tiny ceiling BE its wall, by taking the commit reserve out.

    `run_wall_seconds` subtracts what a checkpoint commit, a `push_event` publish and
    `mark_run_succeeded` need, so a 1 s ceiling is refused as a wall that leaves no
    room to commit — correct for a deployment, unusable for a test that has to wait
    its wall out. What those tests are about is which wall gives out, what a cut
    leaves durable, and what fence the lane is held with; the size of the reserve is
    pinned where it lives, in `test_celery_visibility_timeout.py`. Since STAS-468
    zeroing it moves `input_budget` too — the gate and the grant follow the wall, on
    purpose, because a loop test's wall IS its ceiling — while what it never moves is
    any assertion about the reserve's SIZE, which lives in the unpatched ladder
    `test_every_granted_wall_affords_the_slice_it_grants`.
    """
    from backend.services import agent_service

    monkeypatch.setattr(agent_service, "RUN_COMMIT_RESERVE_SECONDS", 0)


@pytest.mark.asyncio
async def test_separate_model_contexts_and_no_sprite_execution(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, sprite_agent_service

    calls = []
    all_started = asyncio.Event()

    async def fake_run(scope, instructions, route):
        calls.append((scope.purpose, json.dumps(scope.documents)))
        if len(calls) == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)
        return "Completed this isolated wiki."

    async def forbidden(*args, **kwargs):
        pytest.fail("Developer curator must never run in a credential-bearing sprite")

    monkeypatch.setattr(curation, "run_scope", fake_run)
    monkeypatch.setattr(sprite_agent_service, "run_chat", forbidden)
    agent = await _from_never(
        pool, await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    )
    # This test asks which model context each scope is handed, and its `all_started`
    # rendezvous only works while all three scopes belong to ONE pass - so the ceiling is
    # dialed to the wall that holds this fixture's whole feed in a single pass. STAS-468
    # prices the commit inside the slice, which moves the 570 s default wall to a
    # 1,455-character grant - one pass short of this feed; the 630 s ceiling below
    # resolves to a 600 s wall and the 1,746-character grant these counts were written
    # against.
    await pool.execute(
        "UPDATE agents SET turn_timeout_seconds=$2 WHERE id=$1",
        UUID(str(agent["id"])),
        630,
    )
    agent = await agent_service.get_agent_by_id(UUID(str(agent["id"])))
    await sprite_agent_service.run_scheduled(
        agent, "security-test", curation_service.position_of(agent)
    )
    assert len([p for p, _ in calls if p == "private"]) == 2
    assert len([p for p, _ in calls if p == "shared"]) == 1
    assert all("SECRET_TRANSCRIPT" not in text for p, text in calls if p == "shared")
    assert any("SECRET_TRANSCRIPT" in text for p, text in calls if p == "private")
    assert sprite_exec.calls == [] and sprite_exec.writes == []


@pytest.mark.asyncio
async def test_internal_developer_curator_cannot_read_customer_inputs(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, sprite_agent_service

    calls = []

    async def fake_run(scope, instructions, route):
        calls.append(scope)
        assert "SECRET_TRANSCRIPT" not in json.dumps(scope.documents)
        assert "ALLOWED_TRANSCRIPT" not in json.dumps(scope.documents)
        return "No private developer changes."

    monkeypatch.setattr(curation, "run_scope", fake_run)
    agent = await _from_never(
        pool, await agent_service.get_or_create_curator(dataset.owner, wiki="internal")
    )
    await sprite_agent_service.run_scheduled(
        agent, "security-test", curation_service.position_of(agent)
    )
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
        await curation.run_scope(shared, None, hosted_route())
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
            "edit_page",
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
    await curation.run_scope(private, None, hosted_route())
    await curation.run_scope(shared, None, hosted_route())
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
    await curation.run_scope(shared, None, hosted_route())
    result = requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "unavailable" in result["content"]
    assert "SECRET_TRANSCRIPT" not in json.dumps(requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["edit_page", "write_page"])
async def test_model_corrects_malformed_page_id_without_aborting_scope(
    dataset, pool, monkeypatch, sprite_exec, tool_name
):
    from anthropic.types import TextBlock, ToolUseBlock

    shared = await scope(dataset, "shared")
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            result = kwargs["messages"][-1]["content"][0]
            assert result["is_error"]
            assert "page_id" in result["content"] and "UUID" in result["content"]
            assert await pool.fetchval("SELECT count(*) FROM page_edits") == 0
        if calls <= 2:
            arguments = (
                {"old_text": "ALLOWED_SHARED_KNOWLEDGE", "new_text": "Corrected knowledge"}
                if tool_name == "edit_page"
                else {"title": "Index", "content": "Corrected knowledge"}
            )
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id=f"edit-{calls}",
                        name=tool_name,
                        input={
                            "page_id": "d792f10c-d6ac40fe-a73d-f5a2e493a3df"
                            if calls == 1
                            else str(dataset.shared),
                            **arguments,
                        },
                    )
                ],
            )
        assert not kwargs["messages"][-1]["content"][0]["is_error"]
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="Done.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    assert await curation.run_scope(shared, None, hosted_route()) == "Done."
    assert calls == 3
    assert await pool.fetchval("SELECT count(*) FROM page_edits") == 1
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "Corrected knowledge"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("search_documents", {"query": "", "offset": -1}, "offset"),
        ("read_document", {}, "document_id"),
        ("edit_page", {"page_id": "bad", "old_text": "old", "new_text": "new"}, "page_id"),
        ("write_page", {"page_id": None, "title": "", "content": "SECRET"}, "title"),
    ],
)
async def test_invalid_tool_arguments_return_errors_without_writes(
    dataset, pool, name, arguments, field
):
    shared = await scope(dataset, "shared")
    result = await shared.tool(name, arguments)
    assert "Invalid tool arguments" in result["error"]
    assert any(error["field"] == field for error in result["fields"])
    assert "SECRET" not in json.dumps(result)
    assert await pool.fetchval("SELECT count(*) FROM page_edits") == 0
    assert "documents" in await shared.tool("search_documents", {"query": ""})


@pytest.mark.asyncio
async def test_invalid_tool_arguments_do_not_mask_revoked_permissions(dataset):
    shared = await scope(dataset, "shared")
    await end_user_service.update_end_user(dataset.users["public"]["id"], share_wiki=False)
    with pytest.raises(PermissionError):
        await shared.tool("edit_page", {"page_id": "bad"})


@pytest.mark.asyncio
async def test_tool_argument_recovery_does_not_swallow_internal_validation_failure(
    dataset, monkeypatch
):
    shared = await scope(dataset, "shared")

    async def broken_write(*args, **kwargs):
        curation.Search.model_validate({"query": []})

    monkeypatch.setattr(shared, "write", broken_write)
    with pytest.raises(ValidationError):
        await shared.tool("write_page", {"page_id": None, "title": "Valid", "content": "Valid"})


@pytest.mark.asyncio
async def test_repeated_invalid_arguments_still_exhaust_the_turn_limit(
    dataset, monkeypatch, sprite_exec
):
    from anthropic.types import ToolUseBlock

    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                ToolUseBlock(type="tool_use", id=f"bad-{calls}", name="read_document", input={})
            ],
        )

    monkeypatch.setattr(curation, "_MAX_TURNS", 2)
    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    with pytest.raises(RuntimeError, match="exhausted its tool turns"):
        await curation.run_scope(await scope(dataset, "shared"), None, hosted_route())
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_text", [None, " \n\t"])
async def test_empty_completion_continues_without_replaying_saved_write(
    dataset, pool, monkeypatch, sprite_exec, empty_text
):
    from anthropic.types import TextBlock, ToolUseBlock

    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="saved-edit",
                        name="edit_page",
                        input={
                            "page_id": str(dataset.shared),
                            "old_text": "ALLOWED_SHARED_KNOWLEDGE",
                            "new_text": "Saved knowledge",
                        },
                    )
                ],
            )
        if calls == 2:
            assert not kwargs["messages"][-1]["content"][0]["is_error"]
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[] if empty_text is None else [TextBlock(type="text", text=empty_text)],
            )
        assert calls == 3
        messages = kwargs["messages"]
        assert messages[-1]["role"] == "user"
        assert "summary" in messages[-1]["content"]
        assert messages[-2]["content"][0]["tool_use_id"] == "saved-edit"
        assert all(message["content"] for message in messages)
        assert await pool.fetchval("SELECT count(*) FROM page_edits") == 1
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="Updated wiki.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    assert (
        await curation.run_scope(await scope(dataset, "shared"), None, hosted_route())
        == "Updated wiki."
    )
    assert calls == 3
    assert await pool.fetchval("SELECT count(*) FROM page_edits") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("turn_limit", [1, 40])
async def test_empty_completion_retry_is_bounded(dataset, monkeypatch, sprite_exec, turn_limit):
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(stop_reason="end_turn", content=[])

    monkeypatch.setattr(curation, "_MAX_TURNS", turn_limit)
    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    error = RuntimeError if turn_limit == 1 else ValueError
    with pytest.raises(error, match="exhausted its tool turns|no completion summary"):
        await curation.run_scope(await scope(dataset, "shared"), None, hosted_route())
    assert calls == min(turn_limit, 2)


@pytest.mark.asyncio
async def test_empty_completion_does_not_retry_after_optout(dataset, monkeypatch, sprite_exec):
    shared = await scope(dataset, "shared")
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        await end_user_service.update_end_user(dataset.users["public"]["id"], share_wiki=False)
        return SimpleNamespace(stop_reason="end_turn", content=[])

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    with pytest.raises(PermissionError, match="permissions changed"):
        await curation.run_scope(shared, None, hosted_route())
    assert calls == 1


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
        await curation.run_scope(await scope(dataset, "shared"), None, hosted_route())
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
    from backend.services import agent_service, sprite_agent_service

    private_started = set()
    private_cancelled = set()
    all_private_started = asyncio.Event()

    async def fail_shared(scope, instructions, route):
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
    agent = await _from_never(
        pool, await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    )
    before = await pool.fetchval("SELECT curated_through FROM agents WHERE id=$1", agent["id"])
    with pytest.raises(ExceptionGroup) as failure:
        await sprite_agent_service.run_scheduled(
            agent, "failed-test", curation_service.position_of(agent)
        )
    assert any(isinstance(exc, PermissionError) for exc in failure.value.exceptions)
    assert private_cancelled == private_started and len(private_cancelled) == 2
    assert (
        await pool.fetchval("SELECT curated_through FROM agents WHERE id=$1", agent["id"]) == before
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
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    async def stuck(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(curation, "run_scope", stuck)
    monkeypatch.setattr(sprite_agent_service.settings, "AGENT_TURN_TIMEOUT_SECONDS", 0.05)
    # The wall is 0.05 s, two orders below the shipped turn price, so this run's
    # calibration moves with it: 2^-6-second turns at 625 characters each put the
    # guard's smallest-pass bound at 0.0256 s - under this wall - and grant
    # int(0.05 // 0.015625) x 625 = 1,875 characters, a fillable row. The wall, the
    # outcome and every assertion below stay exactly as they were.
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    monkeypatch.setattr(curation, "_INPUT_CHARS_PER_MODEL_TURN", 625)
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


# --- The run ceiling belongs to the curator, and it is one number per run ---


def _fence_ttl(seam, agent_id) -> object:
    """The TTL the `scoped-curator-{id}` single-flight fence was taken with."""
    return seam.redis.ttls[f"scoped-curator-{agent_id}"]


@pytest.mark.asyncio
async def test_each_curator_ceiling_sets_its_own_wall_and_its_own_fence(
    dataset, pool, monkeypatch, sprite_exec
):
    """Two scoped curators, two different walls, in one observable number each.

    The number a curator is given is both its `wait_for` wall and the TTL of the
    `scoped-curator-{id}` lock that fences that lane, so setting one ceiling cannot
    leave the fence on another value: a lock keyed shorter than the wall expires
    while its run is still going and hands the next dispatch a half-finished run.
    The shorter wall is the one that gives out first, which is what makes 'its own
    ceiling' a behaviour and not just a stored number."""
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.config import settings
    from backend.services import agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    async def stuck(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(curation, "run_scope", stuck)
    # The 1 s and 2 s walls are two orders below the shipped turn price, so the
    # calibration moves with them: 2^-6-second turns at 625 characters each put the
    # guard's bound at 0.0256 s under both walls and grant 40,000 and 80,000
    # characters, so both lanes reach run_scope; which wall
    # gives out first is still the assertion.
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    monkeypatch.setattr(curation, "_INPUT_CHARS_PER_MODEL_TURN", 625)
    # 1 s and 2 s rather than a realistic 900 s and 1200 s: this test waits both
    # walls out, and which one gives out first is the assertion.
    pair = {}
    for wiki, ceiling in (("internal", 2), ("external", 1)):
        curator = await agent_service.get_or_create_curator(dataset.owner, wiki=wiki)
        await pool.execute(
            "UPDATE agents SET turn_timeout_seconds=$2 WHERE id=$1",
            UUID(curator["id"]),
            ceiling,
        )
        pair[wiki] = curator

    gave_out: list[str] = []

    async def run_until_cut_off(wiki: str) -> None:
        with pytest.raises(RuntimeError, match="run time limit"):
            await _run_curator_now(UUID(pair[wiki]["id"]), metered=False)
        gave_out.append(wiki)

    await asyncio.gather(run_until_cut_off("internal"), run_until_cut_off("external"))

    assert gave_out == ["external", "internal"]
    assert _fence_ttl(sprite_exec, pair["external"]["id"]) == 1
    assert _fence_ttl(sprite_exec, pair["internal"]["id"]) == 2
    assert settings.AGENT_TURN_TIMEOUT_SECONDS not in (1, 2)
    assert sprite_exec.redis.data == {}


@pytest.mark.asyncio
async def test_a_run_that_takes_its_own_fence_uses_the_same_ceiling(
    dataset, pool, monkeypatch, sprite_exec
):
    """The second construction site of that fence, not the beat's.

    The beat holds `scoped-curator-{id}` itself and re-enters with
    `scoped_lock_held=True`; a lane arriving without that flag builds the lock here
    instead. Both sites have to read one source, or the two drift apart precisely
    the way a shared constant lets them."""
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, sprite_agent_service

    async def stuck(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(curation, "run_scope", stuck)
    # A 1 s wall is two orders below the shipped turn price: patch the calibration
    # (2^-6 s turns at 625 characters each) so the granted 40,000-character slice
    # reaches run_scope, where the wall is what this test waits for.
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    monkeypatch.setattr(curation, "_INPUT_CHARS_PER_MODEL_TURN", 625)
    curator = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET turn_timeout_seconds=$2 WHERE id=$1",
        UUID(curator["id"]),
        1,
    )
    agent = await agent_service.get_agent_by_id(UUID(curator["id"]))
    with pytest.raises(RuntimeError, match="run time limit"):
        await sprite_agent_service.run_scheduled(
            agent, "own-fence-test", curation_service.position_of(agent)
        )
    assert _fence_ttl(sprite_exec, curator["id"]) == 1


@pytest.mark.asyncio
async def test_an_unset_ceiling_fences_the_run_with_the_setting_it_read(
    dataset, pool, monkeypatch, sprite_exec
):
    """A NULL ceiling is 'the deployment default applies', read through the resolver.

    This test and `test_scoped_run_timeout_preserves_progress_and_releases_lock`,
    which this card leaves untouched, are the pair that says there is no second
    codepath: the row with no ceiling still drives its wall through the patched
    setting, and the fence it took carries that same patched number."""
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    async def stuck(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(curation, "run_scope", stuck)
    monkeypatch.setattr(sprite_agent_service.settings, "AGENT_TURN_TIMEOUT_SECONDS", 0.05)
    # As in `test_scoped_run_timeout_preserves_progress_and_releases_lock`: the patched
    # wall is far too short to buy an input row at the shipped turn price, so this
    # run's calibration moves with it (bound 0.0256 s, granted 1,875 characters).
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    monkeypatch.setattr(curation, "_INPUT_CHARS_PER_MODEL_TURN", 625)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    with pytest.raises(RuntimeError, match="run time limit"):
        await _run_curator_now(UUID(agent["id"]), metered=False)
    assert _fence_ttl(sprite_exec, agent["id"]) == 0.05
    assert sprite_exec.redis.data == {}


@pytest.mark.asyncio
async def test_a_scoped_run_never_writes_a_stop_flag_only_a_chat_lane_reads(
    dataset, pool, monkeypatch, sprite_exec
):
    """The ceiling stays out of the chat lanes, measured rather than asserted.

    `request_stop` and the two session turn locks still read the process-wide
    constant, and that is only right because a scoped run returns before it reaches
    `run_chat`, `_pump_turn` or `_stoppable` — the only readers of
    `agent-turn-stop:{session}`. Both endings of a scoped run are checked: one that
    finishes and one its own ceiling cuts off."""
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.agent_schedules import _run_curator_now

    def stop_keys() -> list[str]:
        return [k for k in sprite_exec.redis.data if k.startswith("agent-turn-stop:")]

    async def completes(scope, instructions, route):
        return "Wiki updated."

    monkeypatch.setattr(curation, "run_scope", completes)
    agent = await _from_never(
        pool, await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    )
    await sprite_agent_service.run_scheduled(
        agent, "stop-flag-test", curation_service.position_of(agent)
    )
    assert stop_keys() == []

    async def stuck(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(curation, "run_scope", stuck)
    # The second half sets a 1 s ceiling, two orders below the shipped turn price:
    # patch the calibration so its granted 40,000-character slice still holds a row
    # and the wall is what ends the run.
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    monkeypatch.setattr(curation, "_INPUT_CHARS_PER_MODEL_TURN", 625)
    await pool.execute(
        "UPDATE agents SET turn_timeout_seconds=$2 WHERE id=$1", UUID(str(agent["id"])), 1
    )
    with pytest.raises(RuntimeError, match="run time limit"):
        await _run_curator_now(UUID(str(agent["id"])), metered=False)
    assert stop_keys() == []


# --- The guard and the seeded loop agree by inclusion, not by luck ---


def test_a_wall_at_the_cheapest_pass_is_refused_naming_the_bound():
    """The refuse side of the relation, at its exact edge: a wall EQUAL to the
    cheapest grantable slice refuses, and says so by number.

    `<=` is load-bearing, not a typo for `<`: the slice a wall of exactly the bound
    would be granted already costs that whole wall, which is the zero-drain symptom
    inside the fixed code. STAS-468 widened what a wall has to pay for: the pass price
    is not the whole bill, because the pass must also leave time to commit itself, so
    the threshold is `_minimum_wall_seconds()` - read here, never restated - and the
    message names the commit's reserve and the wall that buys both next to the numbers
    an operator already got, because this refusal reaches them as a failed curator run.
    """
    from backend.services import agent_service

    bound = curation._smallest_pass_seconds()
    gate = curation._minimum_wall_seconds()
    reserve = agent_service.RUN_COMMIT_RESERVE_SECONDS
    with pytest.raises(ValueError, match="cannot buy the cheapest pass") as refused:
        curation.input_budget(bound)
    message = str(refused.value)
    assert f"{bound:.1f} wall-seconds" in message
    assert f"{curation._MODEL_TURN_SECONDS}s" in message
    assert f"{curation._INPUT_CHARS_PER_MODEL_TURN}-character" in message
    assert "1024 characters" in message
    assert f"{reserve}s" in message
    assert f"{gate:.1f}s" in message


def test_every_granted_wall_affords_the_slice_it_grants():
    """The ladder half of the relation: a wall is granted only what it can pay for.

    Two asserts judge a granted wall, and the second is what STAS-468 adds. The slice's
    calibrated cost must be STRICTLY inside the wall - `granted x T / C < wall` - because
    a slice priced exactly AT the wall leaves the pass no time to checkpoint itself, and
    `<=` certified that symptom as correct for every wall whose price landed on it,
    including the 570 s wall the deployment default resolves to. The leftover must then
    be at least `RUN_COMMIT_RESERVE_SECONDS`, read from the shipped module and
    deliberately NOT monkeypatched here: that is what makes this the ladder that judges
    the reserve rather than one that restates it.

    The headroom law this pins is `((wall - R) mod T) + R`, whose minimum over every
    grantable wall is exactly R, reached at wall 600 - a 1,746-character slice costing
    570.0 of a 600 s wall, all three values exactly representable. So the headroom
    assert stays NON-STRICT: written as `>` it reddens for the right answer. Before the
    commit was priced the law was `wall mod T`, whose minimum is 0.0 - which is the
    defect. Threshold and grant are both read from `_minimum_wall_seconds()`, so neither
    can drift from the other, and the exact gate is deliberately NOT a sample: gate is
    `F + R`, and `(F + R) - R` need not return `F` under IEEE rounding, so the exact
    boundary is a knife-edge of representation rather than a semantic case; `gate +/-
    0.1` covers the decision. The nine witness walls below are the walls Step 0 measured
    - 335/340/355/364 decided by the loader's floor (the band this card newly refuses,
    335 having been the tightest wall admitted at 0.7045 s of headroom), and
    475/570/571/950/1140 decided by turns. Wall 600 is in the range below. The turn-only
    guard this design replaced granted wall 120 a 1,024-character slice costing more than
    the wall; that row is still in this ladder.
    """
    from backend.services import agent_service

    turn = curation._MODEL_TURN_SECONDS
    per_turn = curation._INPUT_CHARS_PER_MODEL_TURN
    minimum = curation.scoped_curation_inputs.MINIMUM_BATCH_CHARS
    reserve = agent_service.RUN_COMMIT_RESERVE_SECONDS
    gate = curation._minimum_wall_seconds()
    witnesses = (335, 340, 355, 364, 475, 570, 571, 950, 1140)
    walls = (
        [turn * half for half in (0.5, 1.0, 2.0)]
        + [gate - 0.1, gate + 0.1]
        + list(witnesses)
        + list(range(350, 1201, 50))
    )
    for wall in walls:
        try:
            granted = curation.input_budget(wall)
        except ValueError:
            assert wall < gate, f"wall {wall}s refused although the gate is {gate:.1f}s"
            continue
        assert wall >= gate, f"wall {wall}s granted although it does not reach {gate:.1f}s"
        assert wall > turn
        assert granted >= minimum
        cost = granted * turn / per_turn
        assert cost < wall, (
            f"wall {wall}s was granted {granted} characters, whose calibrated cost "
            f"{cost:.1f}s is not inside the wall that granted it"
        )
        assert wall - cost >= reserve, (
            f"wall {wall}s was granted {granted} characters costing {cost:.1f}s, leaving "
            f"{wall - cost:.1f}s - under the {reserve}s the pass needs to commit itself"
        )
    # The proof is a printed table, not a match: what each witness wall is granted, what
    # that costs at the priced measure, and what headroom is left for its commit.
    print()
    print(f"witness walls at T={turn}s / C={per_turn} / commit reserve R={reserve}s")
    for wall in witnesses:
        try:
            granted = curation.input_budget(wall)
        except ValueError:
            print(f"  wall {wall:4d}  REFUSED - buys neither the cheapest pass nor a commit")
            continue
        cost = granted * turn / per_turn
        headroom = wall - cost
        deciding = "floor" if granted == minimum else "turns"
        print(
            f"  wall {wall:4d}  grant={granted:5d} ({deciding:5s})  cost={cost:7.1f}  "
            f"headroom={headroom:6.1f}"
        )


@pytest.mark.asyncio
async def test_a_wall_just_above_the_refusal_starts_a_pass_at_the_loader(
    dataset, pool, monkeypatch, sprite_exec
):
    """The grant side of the relation: every wall `input_budget` answers reaches
    `load_batch` at least once, so a granted run never declines its own first pass.

    The CEILING is dialed here, and the assertion is about the wall it resolves to -
    one second over `_minimum_wall_seconds()`, the number the guard refuses at. Ceiling
    and wall are different numbers because `run_wall_seconds` subtracts the commit's own
    reserve from the ceiling before `input_budget` ever sees it, and this test runs with
    that reserve in force - a wall the shipped gate refuses is exactly what a real row
    cannot start a pass on. The fake scope answers instantly - so neither the wall nor
    the feed ends this run by design, and the only question left is whether the loader
    was reached at all. The bound and the grant are read from the shipped pair, not
    restated here.
    """
    from backend.services import agent_service, scoped_curation_inputs, sprite_agent_service

    async def completes(scope, instructions, route):
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", completes)
    calls: list[int] = []
    real_load = scoped_curation_inputs.load_batch

    async def spy(owner, since, until, **kwargs):
        calls.append(kwargs["max_chars"])
        return await real_load(owner, since, until, **kwargs)

    monkeypatch.setattr(scoped_curation_inputs, "load_batch", spy)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET turn_timeout_seconds=$2 WHERE id=$1",
        UUID(agent["id"]),
        int(curation._minimum_wall_seconds()) + 1 + agent_service.RUN_COMMIT_RESERVE_SECONDS,
    )
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    summary = await sprite_agent_service.run_scheduled(
        agent, "just-above-bound", curation_service.position_of(agent)
    )
    assert calls, "the guard granted this wall and the loop still never reached the loader"
    assert all(chars >= scoped_curation_inputs.MINIMUM_BATCH_CHARS for chars in calls)
    assert "Scoped curation" in summary


MEASURED_SECONDS_PER_CHARACTER = 1277 / 10211


def test_the_granted_budget_fits_the_wall_measured_in_the_sample_its_own_rate():
    """AC2: the budget, judged against a literal written from the sample.

    `MEASURED_SECONDS_PER_CHARACTER` is the founder lane's own completed pass -
    10,211 characters carried in 1,277 wall seconds on 2026-11-15 - copied into
    this test from the measurement, derived from nothing in the code it judges.
    The assertion: what `input_budget` grants a 570 s wall must cost no more than
    570 s at the price the lane was actually observed to run at. Before this card
    the same expression was red by two orders of magnitude: at the shipped 333 the
    570 s wall was granted 189,810 characters (the code sized 199,800 from the 600 s
    ceiling), which at 0.12506 s/char is 23,737 s - forty-one walls, and the reason
    the drain never reached a checkpoint. If a future model moves the real rate
    above 4,558 characters per 570 s wall, this test reddens while the constants
    sit comfortably unread, which is the whole point of keeping the literal here.

    What this literal is NOT: a headroom gate. Its rate - 0.12506 s per character - is
    2.61 times more generous than the measure that prices a grant (95 / 291 = 0.32646),
    so a slice can satisfy the assert above and still leave the pass no time to commit -
    which is precisely what it did at the 570 s wall. The headroom judge is the second
    assert below, written on the priced measure; this literal stays an empirical
    tripwire against a real rate outrunning the calibration.
    """
    from backend.services import agent_service

    granted = curation.input_budget(570)
    assert granted * MEASURED_SECONDS_PER_CHARACTER <= 570
    priced = granted * curation._MODEL_TURN_SECONDS / curation._INPUT_CHARS_PER_MODEL_TURN
    assert priced <= 570 - agent_service.RUN_COMMIT_RESERVE_SECONDS, (
        f"the 570 s wall was granted {granted} characters costing {priced:.1f}s, leaving "
        f"{570 - priced:.1f}s of a {agent_service.RUN_COMMIT_RESERVE_SECONDS}s commit"
    )


@pytest.mark.asyncio
async def test_the_run_hands_the_loader_its_wall_sized_slice_not_its_ceiling(
    dataset, pool, monkeypatch, sprite_exec
):
    """AC1 in one line: the `max_chars` reaching the loader is the wall's number.

    The row's ceiling is dialed to 10,000 s - far above the worker's cap, so the
    wall is 1,470 s and a ceiling-sized budget (333 x 10,000 = 3,330,000
    characters) and a wall-sized one cannot be mistaken for each other. Pre-fix
    this spy recorded the ceiling-derived number - red by a factor of 763 at this
    dial - and the comparison is written THROUGH `input_budget`, so it stays
    meaningful at any future calibration rather than pinning today's digits.
    """
    from backend.services import agent_service, scoped_curation_inputs, sprite_agent_service

    async def completes(scope, instructions, route):
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", completes)
    calls: list[int] = []
    real_load = scoped_curation_inputs.load_batch

    async def spy(owner, since, until, **kwargs):
        calls.append(kwargs["max_chars"])
        return await real_load(owner, since, until, **kwargs)

    monkeypatch.setattr(scoped_curation_inputs, "load_batch", spy)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET turn_timeout_seconds=$2 WHERE id=$1", UUID(agent["id"]), 10_000
    )
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    # The dial is real: ceiling and wall are far apart, and the worker cap is what
    # puts them there.
    assert agent_service.turn_ceiling_seconds(agent) == 10_000
    assert agent_service.run_wall_seconds(agent) == 1_470
    await sprite_agent_service.run_scheduled(
        agent, "wall-not-ceiling", curation_service.position_of(agent)
    )
    assert calls, "the scoped run never reached the loader"
    expected = curation.input_budget(agent_service.run_wall_seconds(agent))
    assert all(chars == expected for chars in calls), (
        f"the loader was handed {sorted(set(calls))}; the wall sizes {expected}"
    )


@pytest.mark.asyncio
async def test_duplicate_create_returns_existing_page_without_overwriting_it(dataset, pool):
    shared = await scope(dataset, "shared")
    result = await shared.tool(
        "write_page", {"page_id": None, "title": "Index", "content": "Accidental replacement"}
    )
    assert "already exists" in result["error"]
    assert result["page_id"] == str(dataset.shared)
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "ALLOWED_SHARED_KNOWLEDGE"
    )
    assert (
        await pool.fetchval("SELECT count(*) FROM page_edits WHERE page_id=$1", dataset.shared) == 0
    )
    # The conflict rolls back to a savepoint; the same scope can read and
    # deliberately update the existing page instead of aborting the run.
    doc = await shared.tool("read_document", {"document_id": result["page_id"]})
    assert doc["content"] == "ALLOWED_SHARED_KNOWLEDGE"
    await shared.tool(
        "write_page", {"page_id": result["page_id"], "title": "Index", "content": "Reviewed update"}
    )
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "Reviewed update"
    )


@pytest.mark.asyncio
async def test_conflicting_rename_preserves_both_pages(dataset, pool):
    shared = await scope(dataset, "shared")
    other = await shared.tool(
        "write_page", {"page_id": None, "title": "Other", "content": "Original other page"}
    )
    conflict = await shared.tool(
        "write_page", {"page_id": other["page_id"], "title": "Index", "content": "Bad rename"}
    )
    assert conflict["page_id"] == str(dataset.shared) and "error" in conflict
    assert (
        await pool.fetchval(
            "SELECT content_markdown FROM pages WHERE id=$1", UUID(other["page_id"])
        )
        == "Original other page"
    )
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "ALLOWED_SHARED_KNOWLEDGE"
    )


@pytest.mark.asyncio
async def test_duplicate_title_lookup_never_returns_another_wikis_page(dataset, pool):
    shared = await scope(dataset, "shared")
    result = await shared.tool(
        "write_page", {"page_id": None, "title": "private notes", "content": "Public knowledge"}
    )
    assert "error" not in result
    assert result["page_id"] != str(dataset.pages["private"])
    assert "PRIVATE_WIKI_SECRET" not in json.dumps(shared.documents)


@pytest.mark.asyncio
async def test_successful_scoped_batches_queue_and_drain_without_skipping_private_input(
    dataset, pool, monkeypatch, sprite_exec
):
    from backend.services import agent_service, billing_service, curation_service
    from backend.tasks import agent_schedules

    async def entitled(_):
        return True

    monkeypatch.setattr(billing_service, "is_pro", entitled)
    await pool.execute(
        "INSERT INTO history_events (owner_user_id,created_by,agent_name,event_type,session_id,content) "
        "VALUES ($1,$1,'heavi-chat','user_message','public-job','Another permitted lesson')",
        dataset.owner,
    )
    # DB and runner clocks may differ; make this a fixed historical backlog.
    inputs = await pool.fetch(
        "SELECT id FROM history_events WHERE owner_user_id=$1 ORDER BY created_at,id", dataset.owner
    )
    base = datetime.now(UTC) - timedelta(minutes=1)
    for index, event in enumerate(inputs):
        await pool.execute(
            "UPDATE history_events SET created_at=$2 WHERE id=$1",
            event["id"],
            base + timedelta(milliseconds=index),
        )
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    seen = []

    async def fake_run(scope, instructions, route):
        text = json.dumps(scope.documents)
        if scope.purpose == "shared":
            assert "SECRET_TRANSCRIPT" not in text
        seen.append(text)
        return "Reviewed this batch."

    monkeypatch.setattr(curation, "run_scope", fake_run)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute("UPDATE agents SET curated_through=NULL WHERE id=$1", UUID(agent["id"]))
    # A small event limit must split the real input rather than moving the
    # watermark over unseen data. One dispatch now drains every slice it can.
    await agent_schedules._run_curator_now(UUID(agent["id"]), metered=False)
    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    latest_input = await pool.fetchval(
        "SELECT max(created_at) FROM history_events WHERE owner_user_id=$1 "
        "AND session_id NOT LIKE 'agent-curate-%'",
        dataset.owner,
    )
    # Each committed pass publishes exactly one summary event, so this counts the
    # checkpoints the run made durable - the number that says no slice was skipped.
    checkpoints = await pool.fetchval(
        "SELECT count(*) FROM history_events WHERE owner_user_id=$1 "
        "AND event_type='assistant_message' AND session_id LIKE 'agent-curate-%'",
        dataset.owner,
    )
    assert checkpoints >= 2
    # Nothing had to be re-queued: the whole drain fitted inside the run's own ceiling,
    # so the catch-up chain stays what it is for - a cut run's unfinished backlog.
    assert queued == []
    assert current["curated_through"] >= latest_input
    assert current["last_run_outcome"] == "ran"
    assert current["month_run_count"] == 0
    assert any("SECRET_TRANSCRIPT" in text for text in seen)


@pytest.mark.asyncio
async def test_truncated_write_is_discarded_before_one_complete_retry(
    dataset, pool, monkeypatch, sprite_exec
):
    from anthropic.types import TextBlock, ToolUseBlock

    requests = []

    async def create(**kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        if len(requests) <= 2:
            truncated = len(requests) == 1
            return SimpleNamespace(
                stop_reason="max_tokens" if truncated else "tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id=f"write-{len(requests)}",
                        name="write_page",
                        input={
                            "page_id": None,
                            "title": "Partial page" if truncated else "Concise page",
                            "content": "Discard this partial output"
                            if truncated
                            else "Complete permitted lesson.",
                        },
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
    assert await curation.run_scope(await scope(dataset, "shared"), None, hosted_route()) == "Done."
    assert await pool.fetchval("SELECT count(*) FROM pages WHERE name='Partial page'") == 0
    assert await pool.fetchval("SELECT count(*) FROM pages WHERE name='Concise page'") == 1
    assert len(requests) == 3
    assert "Discard this partial output" not in json.dumps(requests[1:])
    assert "Partial page" in requests[1]["messages"][-1]["content"]
    retry_tools = {tool["name"]: tool for tool in requests[1]["tools"]}
    assert retry_tools["write_page"]["input_schema"]["properties"]["content"]["maxLength"] == 4000
    assert retry_tools["edit_page"]["input_schema"]["properties"]["new_text"]["maxLength"] == 2000


@pytest.mark.asyncio
async def test_rename_collision_resolves_in_the_pages_subfolder_not_the_wiki_root(dataset, pool):
    folder = await pool.fetchval(
        "INSERT INTO folders (owner_user_id,created_by,name,parent_folder_id) "
        "VALUES ($1,$1,'Nested',$2) RETURNING id",
        dataset.owner,
        dataset.workspace["external_wiki_folder_id"],
    )
    pages = {}
    for name in ("Index", "Other"):
        pages[name] = await pool.fetchval(
            "INSERT INTO pages (owner_user_id,folder_id,name,content_markdown,created_by) "
            "VALUES ($1,$2,$3,$4,$1) RETURNING id",
            dataset.owner,
            folder,
            name,
            "Nested " + name,
        )
    shared = await scope(dataset, "shared")
    result = await shared.tool(
        "write_page", {"page_id": str(pages["Other"]), "title": "Index", "content": "Bad rename"}
    )
    assert result["page_id"] == str(pages["Index"]) and "error" in result
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "ALLOWED_SHARED_KNOWLEDGE"
    )
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", pages["Other"])
        == "Nested Other"
    )


@pytest.mark.asyncio
async def test_duplicate_title_does_not_refresh_a_revision_changed_by_a_human(dataset, pool):
    shared = await scope(dataset, "shared")
    original = dict(shared.writable[dataset.shared])
    await pool.execute(
        "UPDATE pages SET content_markdown='Human edit',content_hash='human-revision' WHERE id=$1",
        dataset.shared,
    )
    with pytest.raises(ValueError, match="changed during curation"):
        await shared.tool(
            "write_page", {"page_id": None, "title": "Index", "content": "Stale replacement"}
        )
    assert shared.writable[dataset.shared] == original
    # Nor can a subsequent ordinary update reuse a silently refreshed hash.
    with pytest.raises(ValueError, match="changed during curation"):
        await shared.tool(
            "write_page",
            {"page_id": str(dataset.shared), "title": "Index", "content": "Stale replacement"},
        )
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "Human edit"
    )


@pytest.mark.asyncio
async def test_small_edit_preserves_large_page_and_tracks_revision(dataset, pool):
    text = "Existing detail.\n" * 3000 + "Unique ending."
    await pool.execute("UPDATE pages SET content_markdown=$2 WHERE id=$1", dataset.shared, text)
    shared = await scope(dataset, "shared")
    for old, new in [
        ("Unique ending.", "Unique ending.\nNew lesson."),
        ("New lesson.", "Verified new lesson."),
    ]:
        await shared.tool(
            "edit_page",
            {
                "page_id": str(dataset.shared),
                "old_text": old,
                "new_text": new,
            },
        )
    assert await pool.fetchval(
        "SELECT content_markdown FROM pages WHERE id=$1", dataset.shared
    ) == (text + "\nVerified new lesson.")
    assert (
        await pool.fetchval("SELECT count(*) FROM page_edits WHERE page_id=$1", dataset.shared) == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("old,new", [("absent", "new"), ("repeat", "new"), ("repeat repeat", "")])
async def test_ambiguous_missing_or_destructive_edit_does_not_write(dataset, pool, old, new):
    await pool.execute(
        "UPDATE pages SET content_markdown='repeat repeat' WHERE id=$1", dataset.shared
    )
    shared = await scope(dataset, "shared")
    result = await shared.tool(
        "edit_page",
        {
            "page_id": str(dataset.shared),
            "old_text": old,
            "new_text": new,
        },
    )
    assert "error" in result
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "repeat repeat"
    )


@pytest.mark.asyncio
async def test_edit_cannot_overwrite_a_concurrent_human_edit(dataset, pool):
    shared = await scope(dataset, "shared")
    original = shared.documents[str(dataset.shared)]["content"]
    await pool.execute(
        "UPDATE pages SET content_markdown='Human edit',content_hash='new-revision' WHERE id=$1",
        dataset.shared,
    )
    with pytest.raises(ValueError, match="changed during curation"):
        await shared.tool(
            "edit_page",
            {
                "page_id": str(dataset.shared),
                "old_text": original,
                "new_text": "Agent edit",
            },
        )
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "Human edit"
    )


@pytest.mark.asyncio
async def test_edit_enforces_writable_scope_and_permission_revocation(dataset):
    private = await scope(dataset, "private")
    denied = await private.tool(
        "edit_page",
        {
            "page_id": str(dataset.shared),
            "old_text": "ALLOWED_SHARED_KNOWLEDGE",
            "new_text": "Bad edit",
        },
    )
    assert "outside" in denied["error"]
    shared = await scope(dataset, "shared")
    await end_user_service.update_end_user(dataset.users["public"]["id"], share_wiki=False)
    with pytest.raises(PermissionError):
        await shared.tool(
            "edit_page",
            {
                "page_id": str(dataset.shared),
                "old_text": "ALLOWED_SHARED_KNOWLEDGE",
                "new_text": "Bad edit",
            },
        )


@pytest.mark.asyncio
async def test_initial_prompt_identifies_delta_inputs_not_existing_wiki(
    dataset, monkeypatch, sprite_exec
):
    from anthropic.types import TextBlock

    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="No update needed.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    shared = await scope(dataset, "shared")
    await curation.run_scope(shared, None, hosted_route())
    manifest = requests[0]["messages"][0]["content"]
    assert "session:public-job" in manifest
    assert "session:private-job" not in manifest
    assert "ALLOWED_SHARED_KNOWLEDGE" not in manifest
    assert requests[0]["max_tokens"] == 4096
    shared.documents = {
        key: doc
        for key, doc in shared.documents.items()
        if not key.startswith(("session:", "file:", "source:"))
    }
    assert "No new input" in await curation.run_scope(shared, None, hosted_route())
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_text_edit_preserves_a_concurrent_title_only_change(dataset, pool):
    shared = await scope(dataset, "shared")
    await pool.execute("UPDATE pages SET name='Human title' WHERE id=$1", dataset.shared)
    await shared.tool(
        "edit_page",
        {
            "page_id": str(dataset.shared),
            "old_text": "ALLOWED_SHARED_KNOWLEDGE",
            "new_text": "Updated knowledge",
        },
    )
    row = await pool.fetchrow("SELECT name,content_markdown FROM pages WHERE id=$1", dataset.shared)
    assert row["name"] == "Human title"
    assert row["content_markdown"] == "Updated knowledge"
    assert shared.documents[str(dataset.shared)]["title"] == "Human title"


@pytest.mark.asyncio
async def test_small_edit_supports_existing_large_page_and_long_human_title(dataset, pool):
    title = "H" * 250
    content = "Existing detail.\n" * 10000 + "Unique ending."
    await pool.execute(
        "UPDATE pages SET name=$2,content_markdown=$3 WHERE id=$1", dataset.shared, title, content
    )
    shared = await scope(dataset, "shared")
    await shared.tool(
        "edit_page",
        {
            "page_id": str(dataset.shared),
            "old_text": "Unique ending.",
            "new_text": "Unique ending. New lesson.",
        },
    )
    row = await pool.fetchrow("SELECT name,content_markdown FROM pages WHERE id=$1", dataset.shared)
    assert row["name"] == title
    assert row["content_markdown"] == content + " New lesson."


@pytest.mark.asyncio
async def test_editing_reserved_log_returns_recoverable_error(dataset, pool):
    await pool.execute("UPDATE pages SET name='Changelog' WHERE id=$1", dataset.shared)
    shared = await scope(dataset, "shared")
    result = await shared.tool(
        "edit_page",
        {
            "page_id": str(dataset.shared),
            "old_text": "ALLOWED_SHARED_KNOWLEDGE",
            "new_text": "Audit output",
        },
    )
    assert "error" in result
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "ALLOWED_SHARED_KNOWLEDGE"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["edit_page", "write_page"])
async def test_model_recovers_from_readonly_write_without_changing_shared_page(
    dataset, pool, monkeypatch, sprite_exec, tool_name
):
    from anthropic.types import TextBlock, ToolUseBlock

    private = await scope(dataset, "private", "private")
    read = await private.tool("read_document", {"document_id": str(dataset.shared)})
    assert read["writable"] is False
    calls = 0

    async def create(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            args = (
                {"old_text": "ALLOWED_SHARED_KNOWLEDGE", "new_text": "PRIVATE detail"}
                if tool_name == "edit_page"
                else {"title": "Index", "content": "PRIVATE detail"}
            )
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="denied",
                        name=tool_name,
                        input={"page_id": str(dataset.shared), **args},
                    )
                ],
            )
        if calls == 2:
            result = kwargs["messages"][-1]["content"][0]
            assert result["is_error"] and "outside" in result["content"]
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="allowed",
                        name="edit_page",
                        input={
                            "page_id": str(dataset.pages["private"]),
                            "old_text": "PRIVATE_WIKI_SECRET",
                            "new_text": "PRIVATE_WIKI_SECRET with new detail",
                        },
                    )
                ],
            )
        assert not kwargs["messages"][-1]["content"][0]["is_error"]
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="Updated private wiki.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    assert await curation.run_scope(private, None, hosted_route()) == "Updated private wiki."
    assert calls == 3
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == "ALLOWED_SHARED_KNOWLEDGE"
    )
    assert (
        await pool.fetchval("SELECT count(*) FROM page_edits WHERE page_id=$1", dataset.shared) == 0
    )
    assert (
        await pool.fetchval(
            "SELECT content_markdown FROM pages WHERE id=$1", dataset.pages["private"]
        )
        == "PRIVATE_WIKI_SECRET with new detail"
    )


@pytest.mark.asyncio
async def test_collision_cannot_adopt_a_page_created_after_snapshot(dataset, pool):
    shared = await scope(dataset, "shared")
    human_page = await pool.fetchval(
        "INSERT INTO pages (owner_user_id,folder_id,name,content_markdown,created_by) "
        "VALUES ($1,$2,'New human page','Human work',$1) RETURNING id",
        dataset.owner,
        shared.destination,
    )
    with pytest.raises(ValueError, match="created during curation"):
        await shared.tool(
            "write_page",
            {
                "page_id": None,
                "title": "New human page",
                "content": "Stale agent replacement",
            },
        )
    assert human_page not in shared.writable
    denied = await shared.tool(
        "write_page",
        {
            "page_id": str(human_page),
            "title": "New human page",
            "content": "Still stale",
        },
    )
    assert "error" in denied
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", human_page)
        == "Human work"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resumed", [False, True], ids=["partial-last-event", "partial-first-event"]
)
async def test_session_manifest_does_not_promote_event_offsets_to_document_bounds(
    dataset, pool, monkeypatch, sprite_exec, resumed
):
    from anthropic.types import TextBlock, ToolUseBlock

    from backend.services import scoped_curation_inputs

    stamp = datetime.now(UTC) - timedelta(minutes=5)
    until = stamp + timedelta(seconds=2)
    texts = ["First complete event", 'Tool result: "bolt" \\ café\n' * 80, "Last complete event"]
    await pool.execute("DELETE FROM history_events WHERE owner_user_id=$1", dataset.owner)
    for index, text in enumerate(texts):
        await pool.execute(
            "INSERT INTO history_events (owner_user_id,created_by,agent_name,event_type,session_id,content,created_at) "
            "VALUES ($1,$1,'test','user_message','private-job',$2,$3)",
            dataset.owner,
            text,
            stamp + timedelta(seconds=index),
        )
    batch = await scoped_curation_inputs.load_batch(
        dataset.owner,
        stamp - timedelta(microseconds=1),
        until,
        internal=False,
        generation=dataset.workspace["curation_generation"],
        max_chars=2048,
    )
    assert len(batch.documents) == 2 and batch.cursor
    if resumed:
        batch = await scoped_curation_inputs.load_batch(
            dataset.owner,
            batch.through,
            until,
            internal=False,
            generation=dataset.workspace["curation_generation"],
            cursor=batch.cursor,
            max_chars=2048,
        )
        assert len(batch.documents) == 2 and not batch.cursor
    private = await curation.load_scope(
        dataset.workspace,
        "private",
        dataset.users["private"]["wiki_folder_id"],
        [dataset.users["private"]["id"]],
        "agent-curate-session-chunks",
        batch=batch,
    )
    requests = []

    async def create(**kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        if len(requests) == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="read-session",
                        name="read_document",
                        input={"document_id": "session:private-job"},
                    )
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn", content=[TextBlock(type="text", text="Read supplied events.")]
        )

    monkeypatch.setattr(
        curation.llm,
        "_get_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )
    await curation.run_scope(private, None, hosted_route())
    manifest = json.loads(
        requests[0]["messages"][0]["content"].split("New input documents: ", 1)[1]
    )
    read = json.loads(requests[1]["messages"][-1]["content"][0]["content"])
    events = [json.loads(line) for line in read["content"].splitlines()]
    assert len(manifest) == 1 and len(events) == 2
    assert manifest[0]["characters"] == read["total_characters"] == len(read["content"])
    assert "chunk" not in manifest[0]  # The merged document has no single event's bounds.
    assert read["next_offset"] is None
    expected_texts = texts[1:] if resumed else texts[:2]
    for event, original in zip(events, expected_texts, strict=True):
        offset = event["offset"]
        assert event["total_characters"] == len(original)
        assert event["content"] == original[offset : offset + len(event["content"])]
    if resumed:
        assert events[0]["offset"] > 0 and events[1]["content"] == texts[2]
    else:
        assert events[0]["content"] == texts[0]
        assert len(events[1]["content"]) < events[1]["total_characters"]


@pytest.mark.asyncio
async def test_large_files_and_sources_resume_without_skipping_or_unbounded_input(dataset, pool):
    from backend.services import scoped_curation_inputs

    stamp = datetime.now(UTC) - timedelta(minutes=5)
    file_text, source_text = "A" * 6000 + "FILE_END", "B" * 9000 + "SOURCE_END"
    await pool.execute(
        "UPDATE files SET extracted_text=$2,created_at=$3 WHERE id=$1",
        dataset.private_file,
        file_text,
        stamp,
    )
    await pool.execute(
        "UPDATE drive_documents SET content=$2,updated_at=$3 WHERE id=$1",
        dataset.source_doc,
        source_text,
        stamp,
    )
    await pool.execute("DELETE FROM history_events WHERE owner_user_id=$1", dataset.owner)
    since, cursor = stamp - timedelta(microseconds=1), {}
    collected = {}
    for _ in range(12):
        batch = await scoped_curation_inputs.load_batch(
            dataset.owner,
            since,
            datetime.now(UTC),
            internal=False,
            generation=dataset.workspace["curation_generation"],
            cursor=cursor,
            max_chars=2048,
        )
        assert sum(len(d["content"]) for d in batch.documents) <= 2048
        private = await curation.load_scope(
            dataset.workspace,
            "private",
            dataset.users["private"]["wiki_folder_id"],
            [dataset.users["private"]["id"]],
            "agent-curate-file-source-chunks",
            batch=batch,
        )
        for doc in batch.documents:
            collected[doc["document_id"]] = collected.get(doc["document_id"], "") + doc["content"]
            read = await private.tool("read_document", {"document_id": doc["document_id"]})
            assert read["content"] == doc["content"]
            assert read["total_characters"] == len(doc["content"])
            assert read["next_offset"] is None
            assert private.documents[doc["document_id"]]["chunk"] == {
                "offset": doc["offset"],
                "end": doc["next_offset"],
                "total_characters": doc["characters"],
            }
        if batch.cursor:
            assert batch.through < stamp
            assert batch.cursor != cursor
        since, cursor = batch.through, batch.cursor
        if not cursor:
            break
    assert not cursor and since >= stamp
    assert collected == {
        f"file:{dataset.private_file}": file_text,
        f"source:{dataset.source_doc}": source_text,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "empty_completion"])
async def test_cursor_is_committed_only_after_all_scopes_succeed(
    dataset, client, pool, monkeypatch, sprite_exec, failure
):
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks.session_titles import generate_session_title

    # `sprite_exec` hands the app a test Anthropic key, which turns the per-push title
    # dispatch on, and a test run has no broker to receive it.
    monkeypatch.setattr(generate_session_title, "delay", lambda *a, **k: None)

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "X" * 10000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute("UPDATE agents SET curated_through=NULL WHERE id=$1", UUID(agent["id"]))
    seen = []

    async def completed(scope, instructions, route):
        seen.append(scope)
        return "Curated supplied chunk."

    real_run_scope = curation.run_scope
    monkeypatch.setattr(curation, "run_scope", completed)
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    await sprite_agent_service.run_scheduled(
        agent, "chunk-one", curation_service.position_of(agent)
    )
    before = await agent_service.get_agent_by_id(UUID(agent["id"]))
    # A run is a loop of passes now, so one successful run walks the chunked file to its
    # end instead of stopping after the first 2048 characters: the cursor it lands on is
    # empty because the file was read, not skipped. The mid-file commit this assertion
    # used to show is pinned directly by
    # `test_a_cursor_only_pass_is_checkpointed_and_its_next_run_resumes_the_rest`.
    assert before["curation_cursor"] == {}
    assert "X" * 100 not in json.dumps([s.documents for s in seen if s.purpose == "shared"])

    async def failed(*args):
        raise RuntimeError("Model unavailable")

    # The first run read everything that was pending, so the failing run has to be
    # given input of its own: the invariant is that a run which fails over material it
    # loaded leaves the pair exactly where the last completed pass left it.
    await _push(client, dataset.key, [_event("private-job", "private", "A lesson after the drain")])
    if failure == "empty_completion":

        async def create(**kwargs):
            return SimpleNamespace(stop_reason="end_turn", content=[])

        monkeypatch.setattr(curation, "run_scope", real_run_scope)
        monkeypatch.setattr(
            curation.llm,
            "_get_client",
            lambda: SimpleNamespace(messages=SimpleNamespace(create=create)),
        )
    else:
        monkeypatch.setattr(curation, "run_scope", failed)
    with pytest.raises(ExceptionGroup) as errors:
        await sprite_agent_service.run_scheduled(
            before, "chunk-two", curation_service.position_of(before)
        )
    if failure == "empty_completion":
        assert all(
            isinstance(error, ValueError) and "no completion summary" in str(error)
            for error in errors.value.exceptions
        )
    after = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert (after["curated_through"], after["curation_cursor"]) == (
        before["curated_through"],
        before["curation_cursor"],
    )


@pytest.mark.asyncio
async def test_a_ceiling_cut_keeps_its_completed_passes_and_the_next_run_resumes_them(
    dataset, pool, monkeypatch, sprite_exec
):
    """The defect this card exists for, in one run and the run after it.

    The pass that finishes is durable even though the run its ceiling cut off was
    still reading, and the next run starts where that checkpoint says rather than at
    the beginning of the backlog again. The cut is the run's own one-second ceiling;
    the pass it stops is held open by the fake below.
    """
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, sprite_agent_service

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "X" * 10000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    # The loop seeds its pre-pass check with the shipped turn price; a 1 s wall needs
    # that seed moved with it (the budget itself never runs - the lambda answers).
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET curated_through=NULL, turn_timeout_seconds=$2 WHERE id=$1",
        UUID(agent["id"]),
        1,
    )
    file_key = f"file:{dataset.private_file}"
    first_slice = asyncio.Event()
    hold = asyncio.Event()
    read_offsets: list[int] = []

    async def read_one_slice_then_hold(scope, instructions, route):
        document = scope.documents.get(file_key)
        if document is None:
            return "Nothing of mine in this slice."
        read_offsets.append(document["chunk"]["offset"] if "chunk" in document else 0)
        if not first_slice.is_set():
            first_slice.set()
            return "First slice read."
        await hold.wait()
        return "Never reached: the ceiling cuts this pass off."

    monkeypatch.setattr(curation, "run_scope", read_one_slice_then_hold)
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    with pytest.raises(RuntimeError, match="run time limit"):
        await sprite_agent_service.run_scheduled(
            agent, "cut-run", curation_service.position_of(agent)
        )

    cut = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert read_offsets[0] == 0 and read_offsets[1] > 0
    # The checkpoint stands where the completed pass stopped, not where the pass the
    # wall stopped had got to, and not at the beginning of the document.
    assert cut["curated_through"] is not None
    partial = cut["curation_cursor"]["partial"]
    assert list(partial) == [key for key in partial if key.startswith(file_key)]
    assert list(partial.values()) == [read_offsets[1]]
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM history_events WHERE session_id LIKE 'agent-curate-%' "
            "AND event_type='assistant_message'"
        )
        == 1
    )

    async def report_what_is_read(scope, instructions, route):
        document = scope.documents.get(file_key)
        if document is not None:
            read_offsets.append(document["chunk"]["offset"] if "chunk" in document else 0)
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", report_what_is_read)
    resumes_at = len(read_offsets)
    await sprite_agent_service.run_scheduled(cut, "resumed-run", curation_service.position_of(cut))
    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    # The next run reads the rest of the document rather than its first slice again.
    assert read_offsets[resumes_at] == read_offsets[1]
    assert read_offsets[resumes_at:] == sorted(read_offsets[resumes_at:])
    assert current["curation_cursor"] == {}


@pytest.mark.asyncio
async def test_a_run_stops_before_a_pass_it_cannot_finish_inside_its_wall(
    dataset, pool, monkeypatch, sprite_exec, caplog
):
    """A healthy drain ends at a committed pass boundary, not at the wall that cuts one.

    The sibling of the ceiling-cut test above, and the two are read together: that one
    pins what survives a run the wall stops, this one pins that a run carrying the same
    backlog through the same wall is never stopped at all. The loop declines the pass it
    cannot finish, so nothing is in flight to lose and the next dispatch resumes from a
    committed position rather than from a discarded slice. Three facts prove it: the call
    RETURNS (no `RunCeilingExceeded`, no `TimeoutError`), the number of slices the model
    was handed equals the number of passes that committed — the last thing that ran is the
    last thing that is durable — and the summary names the position the drain stands at.

    The wall is the row's own 1 s and one pass costs a quarter of it, because the margin a
    decline leaves is exactly one pass: at a shorter pass the pre-pass check and the rescue
    wall would race each other on a loaded machine, and which of them answered would be a
    fact about the test runner rather than about this code.
    """
    import logging

    from backend.services import agent_service, sprite_agent_service

    _reserve_nothing_for_the_commit(monkeypatch)
    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "V" * 20000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    # The loop's seed must stay far below the 0.25 s pass this test measures, so the
    # declined pass stays a measured one and never the seed.
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    # A drain in flight, standing two days behind the document it is about to read: the
    # backlog is several slices wide, so the feed cannot empty inside this wall.
    await pool.execute(
        "UPDATE agents SET curated_through = now() - interval '2 days',"
        " drain_through = now() - interval '2 days', turn_timeout_seconds = 1"
        " WHERE id = $1",
        UUID(agent["id"]),
    )
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    slices_read: list[int] = []

    async def read_a_slice_slowly(scope, instructions, route):
        await asyncio.sleep(0.25)
        document = scope.documents.get(f"file:{dataset.private_file}")
        if document is not None:
            slices_read.append(document["chunk"]["offset"] if "chunk" in document else 0)
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", read_a_slice_slowly)
    with caplog.at_level(logging.INFO, logger="backend.services.scoped_curation_service"):
        summary = await sprite_agent_service.run_scheduled(
            agent, "wall-stop", curation_service.position_of(agent)
        )

    committed = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("curator pass=")
    ]
    assert len(slices_read) >= 2, f"a 1 s wall fit no passes at all: {slices_read}"
    assert len(slices_read) == len(committed), (
        f"the model was handed {len(slices_read)} slices but {len(committed)} passes are"
        " durable: the run was cut mid-pass instead of declining one"
    )
    assert "undrained" in summary, f"the run reported a drained feed: {summary}"
    standing = committed[-1].split("committed_through=")[1].split()[0]
    assert standing in summary, f"the summary did not name {standing}: {summary}"
    row = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert row["drain_through"].isoformat() == standing, (
        "the drain does not stand where the last committed pass left it"
    )
    assert row["last_run_outcome"] != "failed"


@pytest.mark.asyncio
async def test_one_run_drains_a_document_larger_than_its_budget_in_committed_passes(
    dataset, pool, monkeypatch, sprite_exec, caplog
):
    """One run walks a document its budget cannot hold, and commits as it goes.

    The checkpoints between the first slice and the last one move only the cursor half
    of the pair - the instant stays one microsecond behind the document still being
    read - so this is also the proof that a cursor-only advance is a checkpoint. The
    spy watches each durable write; the row at the end says those writes are the row,
    and the drain-rate meter says one line per row-moving pass and never for one that
    committed nothing.
    """
    from backend.services import agent_service, sprite_agent_service

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "Z" * 6000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    checkpoints: list[tuple[datetime, dict]] = []
    real_checkpoint = curation._checkpoint_pass

    async def record_checkpoint(agent_, batch, fence, cursor, scope, drain):
        moved = await real_checkpoint(agent_, batch, fence, cursor, scope, drain)
        if moved:
            checkpoints.append((batch.through, dict(batch.cursor)))
        return moved

    async def read_slice(scope, instructions, route):
        return "Slice read."

    monkeypatch.setattr(curation, "_checkpoint_pass", record_checkpoint)
    monkeypatch.setattr(curation, "run_scope", read_slice)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute("UPDATE agents SET curated_through=NULL WHERE id=$1", UUID(agent["id"]))
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    with caplog.at_level(logging.INFO, logger="backend.services.scoped_curation_service"):
        await sprite_agent_service.run_scheduled(
            agent, "drain-run", curation_service.position_of(agent)
        )

    meter = [
        record.getMessage()
        for record in caplog.records
        if record.name == "backend.services.scoped_curation_service"
        and record.getMessage().startswith("curator pass=")
    ]
    assert [line.split()[1] for line in meter] == [
        f"pass={number}" for number in range(1, len(checkpoints) + 1)
    ]
    first_pass = dict(field.split("=", 1) for field in meter[0].split()[1:])
    assert set(first_pass) == {
        "pass",
        "agent",
        "committed_through",
        "characters",
        "scopes",
        "wall",
        "budget",
        "turn_seconds",
        "seconds_per_character",
        "calibrated_seconds_per_character",
    }
    assert int(first_pass["characters"]) > 0
    # A fake test pass is near-instant, so its measured price may legitimately round
    # to zero at four decimals; what must hold is that the field parses.
    assert float(first_pass["seconds_per_character"]) >= 0
    # The line prints both prices at four decimals, so the promise is that the printed
    # calibration is the shipped pair at the precision the line carries - a stricter
    # tolerance would assert digits the meter never prints. It still reddens on a
    # different pair: 95/512 prints 0.1855, not 0.3265.
    assert float(first_pass["turn_seconds"]) == curation._MODEL_TURN_SECONDS
    assert float(first_pass["calibrated_seconds_per_character"]) == round(
        curation._MODEL_TURN_SECONDS / curation._INPUT_CHARS_PER_MODEL_TURN, 4
    )

    mid_pairs = checkpoints[:-1]
    assert len(mid_pairs) >= 2
    assert len({through for through, _ in mid_pairs}) == 1
    assert len({json.dumps(cursor, sort_keys=True) for _, cursor in mid_pairs}) == len(mid_pairs)
    assert all(cursor["partial"] for _, cursor in mid_pairs)
    assert checkpoints[-1][1] == {}
    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert current["curation_cursor"] == {}
    assert current["curated_through"] == checkpoints[-1][0]


@pytest.mark.asyncio
async def test_a_scoped_run_whose_position_moved_underneath_it_refuses_the_pass(
    dataset, pool, monkeypatch, sprite_exec, caplog
):
    """An overlapping run that moved the pair this pass fenced on is not stamped over.

    The pass boundary is where this lane writes, so it is where a mid-run rewind or an
    overlapping advance has to be caught: the UPDATE matches the pair the pass started
    from, matches no row once that pair is gone, and the run ends loud with the rival's
    position standing, nothing published, and no meter line for a pass that committed
    nothing.
    """
    from backend.services import agent_service, sprite_agent_service

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "Z" * 6000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute("UPDATE agents SET curated_through=NULL WHERE id=$1", UUID(agent["id"]))
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))

    rival_stamp = datetime.now(UTC)
    moved_by_rival = False
    published: list = []

    async def rival(scope, instructions, route):
        nonlocal moved_by_rival
        if not moved_by_rival:
            moved_by_rival = True
            await pool.execute(
                "UPDATE agents SET curated_through=$2, curated_through_event_id=NULL WHERE id=$1",
                UUID(agent["id"]),
                rival_stamp,
            )
        return "Curated by the run that got there first."

    async def record_push(*args, **kwargs):
        published.append(args)

    monkeypatch.setattr(curation, "run_scope", rival)
    monkeypatch.setattr(curation.memory_service, "push_event", record_push)

    with caplog.at_level(logging.INFO, logger="backend.services.scoped_curation_service"):
        with pytest.raises(agent_service.CuratorWatermarkConflict):
            await sprite_agent_service.run_scheduled(
                agent, "lost-the-race", curation_service.position_of(agent)
            )

    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert current["curated_through"] == rival_stamp
    assert published == []
    assert [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("curator pass=")
    ] == []


@pytest.mark.asyncio
async def test_a_pass_that_owed_input_and_moved_nothing_fails_instead_of_re_reading_it(
    dataset, pool, monkeypatch, sprite_exec
):
    """The loop's termination rule, forced.

    The loader's own floor is what keeps a pass from handing back the slice it was given,
    so this is unreachable through the real loader today; forcing it is how the rule the
    loop relies on stays pinned. A pass whose pair moved nowhere while its batch still
    owed input is a loader that cannot progress: the run says so after that pass, keeps
    the position the pass before it committed, and does not go back for the same
    characters until its wall arrives.
    """
    from backend.services import agent_service, scoped_curation_inputs, sprite_agent_service

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "Z" * 6000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    real_load = scoped_curation_inputs.load_batch
    handed_back: list = []
    loads = 0

    async def loader_that_cannot_advance(*args, **kwargs):
        nonlocal loads
        loads += 1
        if not handed_back:
            handed_back.append(await real_load(*args, **kwargs))
        return handed_back[0]

    async def read_slice(scope, instructions, route):
        return "Slice read."

    monkeypatch.setattr(scoped_curation_inputs, "load_batch", loader_that_cannot_advance)
    monkeypatch.setattr(curation, "run_scope", read_slice)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute("UPDATE agents SET curated_through=NULL WHERE id=$1", UUID(agent["id"]))
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))

    with pytest.raises(RuntimeError, match="cannot advance the curation cursor"):
        await sprite_agent_service.run_scheduled(
            agent, "stuck", curation_service.position_of(agent)
        )

    assert loads == 2, "the run asked for the same slice again instead of failing"
    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert current["curated_through"] == handed_back[0].through
    assert current["curation_cursor"]["partial"] == handed_back[0].cursor["partial"]


@pytest.mark.asyncio
async def test_a_ceiling_cut_hands_its_unfinished_backlog_to_the_next_catchup_pass(
    dataset, pool, monkeypatch, sprite_exec
):
    """The chain used to start only after a run that succeeded, so the failure shape
    that this card makes normal - a run its own ceiling cut off after completing some
    passes - stopped dead and waited a day for the next tick.

    The hand-off must not launder the failure either: the row still reports it.
    """
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, billing_service
    from backend.tasks import agent_schedules

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "X" * 10000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    # The loop seeds its pre-pass check with the shipped turn price; a 1 s wall needs
    # that seed moved with it (the budget itself never runs - the lambda answers).
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)

    async def entitled(_):
        return True

    monkeypatch.setattr(billing_service, "is_pro", entitled)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET curated_through=NULL, turn_timeout_seconds=$2 WHERE id=$1",
        UUID(agent["id"]),
        1,
    )
    first_slice = asyncio.Event()
    hold = asyncio.Event()

    async def read_one_slice_then_hold(scope, instructions, route):
        if f"file:{dataset.private_file}" not in scope.documents:
            return "Nothing of mine in this slice."
        if not first_slice.is_set():
            first_slice.set()
            return "First slice read."
        await hold.wait()
        return "Never reached: the ceiling cuts this pass off."

    monkeypatch.setattr(curation, "run_scope", read_one_slice_then_hold)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )
    with pytest.raises(RuntimeError, match="run time limit"):
        await agent_schedules._run_curator_now(UUID(agent["id"]), metered=False)

    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert current["curated_through"] is not None
    assert current["last_run_outcome"] == "failed"
    (dispatch,) = queued
    assert dispatch["args"] == [str(agent["id"])]
    assert dispatch["countdown"] == 15
    assert dispatch["kwargs"] == {
        "metered": False,
        "platform_initiated": True,
        # Two passes of the chain are already spent: the one that just ran, and the
        # hand-off this dispatch is.
        "catchup_remaining": agent_schedules.curator_catchup_pass_ceiling() - 2,
        "catchup": True,
    }


@pytest.mark.asyncio
async def test_a_ceiling_cut_that_completed_no_pass_starts_no_chain(
    dataset, pool, monkeypatch, sprite_exec
):
    """The other half of the hand-off, and the reason it cannot become a retry loop:
    a cut that checkpointed nothing has nothing to hand over, so the progress gate
    refuses and the lane waits for its next scheduled run instead of burning a chain.
    """
    _reserve_nothing_for_the_commit(monkeypatch)
    from backend.services import agent_service, billing_service
    from backend.tasks import agent_schedules

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "X" * 10000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    # The loop seeds its pre-pass check with the shipped turn price; a 1 s wall needs
    # that seed moved with it (the budget itself never runs - the lambda answers).
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)

    async def entitled(_):
        return True

    monkeypatch.setattr(billing_service, "is_pro", entitled)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET curated_through=NULL, turn_timeout_seconds=$2 WHERE id=$1",
        UUID(agent["id"]),
        1,
    )
    hold = asyncio.Event()

    async def hold_the_first_slice(scope, instructions, route):
        if f"file:{dataset.private_file}" in scope.documents:
            await hold.wait()
        return "Never reached: the ceiling cuts this pass off."

    monkeypatch.setattr(curation, "run_scope", hold_the_first_slice)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )
    with pytest.raises(RuntimeError, match="run time limit"):
        await agent_schedules._run_curator_now(UUID(agent["id"]), metered=False)

    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert current["curated_through"] is None
    assert current["last_run_outcome"] == "failed"
    assert queued == []


@pytest.mark.asyncio
async def test_a_full_history_scoped_backfill_reads_from_before_its_watermark(
    dataset, pool, monkeypatch, sprite_exec
):
    """The backfill promise is that the run reads with no watermark, and a per-pass
    checkpoint must not spend that promise on its own write anchor.

    The lane runs once and drains, so its stored pair stands past every character it was
    handed. The control is that ordinary run repeated: it hands the model nothing, which
    is what the watermark is for. The backfill is the same row dispatched by the caller
    that zeroes the position inside its own copy of the agent, so the read has to go back
    to the start of the feed while the fenced write still matches on the pair that caller
    loaded - the one thing a backfill must not throw away.
    """
    from backend.services import agent_service, sprite_agent_service
    from backend.tasks import agent_schedules

    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute("UPDATE agents SET curated_through=NULL WHERE id=$1", UUID(agent["id"]))
    read: list[str] = []

    async def record_read(scope, instructions, route):
        read.extend(scope.documents)
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", record_read)
    monkeypatch.setattr(agent_schedules.run_curator_now, "apply_async", lambda **kw: None)
    emptied = await agent_service.get_agent_by_id(UUID(agent["id"]))
    await sprite_agent_service.run_scheduled(
        emptied, "backfill-drain", curation_service.position_of(emptied)
    )
    drained = sorted(set(read))
    assert drained
    read.clear()

    stored = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert stored["curated_through"] is not None
    await sprite_agent_service.run_scheduled(
        stored, "backfill-control", curation_service.position_of(stored)
    )
    assert read == [], "an incremental run re-read input its watermark already covers"

    await agent_schedules._run_curator_now(UUID(agent["id"]), full_history=True, metered=False)
    assert set(read) == set(drained), "the backfill read from its watermark and so read nothing"


@pytest.mark.asyncio
async def test_an_exhausted_catchup_bound_names_the_backlog_it_left_behind(
    dataset, pool, monkeypatch, caplog
):
    """The bound that stops a chain while input is still pending used to be silent."""
    from backend.services import agent_service, billing_service, scoped_curation_service
    from backend.tasks import agent_schedules

    async def entitled(_):
        return True

    monkeypatch.setattr(billing_service, "is_pro", entitled)
    # A scoped lane spends the backend's own key, and the chain asks whether that key exists
    # before it answers what the next pass costs.
    monkeypatch.setattr(curation.settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET curated_through=now() - interval '1 day' WHERE id=$1",
        UUID(agent["id"]),
    )
    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    # The pair this run started from, which is the half the progress gate compares.
    started_from = {**current, "curated_through": None, "curation_cursor": {}}
    workspace = await scoped_curation_service.workspace_for_agent(current)
    queued = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )

    with caplog.at_level(logging.WARNING):
        await agent_schedules._queue_curator_catchup(
            started_from, datetime.now(UTC), 0, workspace=workspace, after_ceiling=True
        )

    assert queued == []
    warned = [
        record.getMessage()
        for record in caplog.records
        if record.name == "backend.tasks.agent_schedules" and record.levelno == logging.WARNING
    ]
    assert any(
        str(agent["id"]) in message and "input still pending" in message for message in warned
    )


@pytest.mark.asyncio
async def test_same_timestamp_group_larger_than_event_limit_drains(dataset, pool, monkeypatch):
    from backend.services import curation_service, scoped_curation_inputs

    stamp = datetime.now(UTC) - timedelta(minutes=5)
    await pool.execute("DELETE FROM history_events WHERE owner_user_id=$1", dataset.owner)
    for index in range(7):
        await pool.execute(
            "INSERT INTO history_events (owner_user_id,created_by,agent_name,event_type,session_id,content,created_at) "
            "VALUES ($1,$1,'test','user_message','private-job',$2,$3)",
            dataset.owner,
            f"message-{index}",
            stamp,
        )
    monkeypatch.setattr(curation_service, "_MAX_EVENTS", 2)
    since, cursor, seen = stamp - timedelta(microseconds=1), {}, []
    for _ in range(4):
        batch = await scoped_curation_inputs.load_batch(
            dataset.owner,
            since,
            stamp,
            internal=False,
            generation=dataset.workspace["curation_generation"],
            cursor=cursor,
            max_chars=2048,
        )
        seen.extend(d["key"] for d in batch.documents)
        since, cursor = batch.through, batch.cursor
    assert len(seen) == len(set(seen)) == 7
    assert since == stamp and cursor == {}


@pytest.mark.asyncio
async def test_revised_large_document_restarts_its_partial_cursor(dataset, pool):
    from backend.services import scoped_curation_inputs

    stamp = datetime.now(UTC) - timedelta(minutes=5)
    await pool.execute("DELETE FROM history_events WHERE owner_user_id=$1", dataset.owner)
    await pool.execute(
        "UPDATE files SET extracted_text=$2,created_at=$3 WHERE id=$1",
        dataset.private_file,
        "old" * 3000,
        stamp,
    )
    batch = await scoped_curation_inputs.load_batch(
        dataset.owner,
        stamp - timedelta(microseconds=1),
        stamp,
        internal=False,
        generation=dataset.workspace["curation_generation"],
        max_chars=2048,
    )
    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "new" * 3000
    )
    revised = await scoped_curation_inputs.load_batch(
        dataset.owner,
        batch.through,
        stamp,
        internal=False,
        generation=dataset.workspace["curation_generation"],
        cursor=batch.cursor,
        max_chars=2048,
    )
    assert revised.documents[0]["offset"] == 0
    assert revised.documents[0]["content"].startswith("newnew")


@pytest.mark.asyncio
async def test_scoped_wiki_output_does_not_trigger_staleness_or_catchup(dataset, pool, monkeypatch):
    from backend.services import agent_service, alert_service, scoped_curation_inputs
    from backend.tasks import agent_schedules

    old = datetime.now(UTC) - timedelta(days=4)
    await pool.execute(
        "UPDATE history_events SET created_at=$2 WHERE owner_user_id=$1", dataset.owner, old
    )
    await pool.execute("UPDATE files SET created_at=$2 WHERE owner_user_id=$1", dataset.owner, old)
    await pool.execute(
        "UPDATE drive_documents SET updated_at=$2 WHERE owner_user_id=$1", dataset.owner, old
    )
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    await pool.execute(
        "UPDATE agents SET curated_through=$2,last_run_outcome='ran' WHERE id=$1",
        UUID(agent["id"]),
        datetime.now(UTC) - timedelta(days=3),
    )
    await pool.execute("UPDATE pages SET updated_at=now() WHERE owner_user_id=$1", dataset.owner)
    current = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert not await scoped_curation_inputs.has_pending(current, dataset.workspace)

    async def forbidden(*args):
        pytest.fail("Curator's own wiki output must not page an operator")

    monkeypatch.setattr(alert_service, "send_alert", forbidden)
    assert await agent_schedules._alert_stale_curators() == 0


@pytest.mark.asyncio
async def test_truncated_full_replacement_retries_as_small_edit(
    dataset, pool, monkeypatch, sprite_exec
):
    from anthropic.types import TextBlock, ToolUseBlock

    text = "Keep all existing knowledge.\n" * 2000 + "Unique ending."
    await pool.execute("UPDATE pages SET content_markdown=$2 WHERE id=$1", dataset.shared, text)
    requests = []

    async def create(**kwargs):
        requests.append(json.loads(json.dumps(kwargs)))
        turn = len(requests)
        if turn <= 2:
            return SimpleNamespace(
                stop_reason="max_tokens" if turn == 1 else "tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id=f"write-{turn}",
                        name="write_page",
                        input={
                            "page_id": str(dataset.shared),
                            "title": "Index",
                            "content": "X" * 5000,
                        },
                    )
                ],
            )
        if turn == 3:
            result = kwargs["messages"][-1]["content"][0]
            assert result["is_error"] and "4000 characters" in result["content"]
            return SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    ToolUseBlock(
                        type="tool_use",
                        id="edit",
                        name="edit_page",
                        input={
                            "page_id": str(dataset.shared),
                            "old_text": "Unique ending.",
                            "new_text": "Unique ending. New lesson.",
                        },
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
    await curation.run_scope(await scope(dataset, "shared"), None, hosted_route())
    assert str(dataset.shared) in requests[1]["messages"][-1]["content"]
    assert "X" * 5000 not in json.dumps(requests[1])
    assert (
        await pool.fetchval("SELECT content_markdown FROM pages WHERE id=$1", dataset.shared)
        == text + " New lesson."
    )
    assert (
        await pool.fetchval("SELECT count(*) FROM page_edits WHERE page_id=$1", dataset.shared) == 1
    )


@pytest.mark.asyncio
async def test_two_history_drain_dispatches_resume_one_position_rather_than_rereading_it(
    dataset, pool, monkeypatch, sprite_exec, caplog
):
    """A backlog two slices wide is drained by two dispatches that never meet twice.

    The drain exists because a curator whose backlog outruns its wall used to restart from
    its first unread character on every dispatch, so the same slice was curated until the
    worker's soft time limit killed the task. What makes that impossible is one fact: a
    dispatch reads the position the dispatch before it committed. This drives the two
    dispatches through the dispatcher that seeds the drain — the only caller that decides
    a run is a backfill — and watches three things a re-zeroing bug would break at once:
    the pair moves and survives the cut run, the second dispatch reads only characters the
    first never committed, and the incremental watermark is not one byte different, because
    a drain that wrote it would have been clamped away and reported as a discarded run.
    """
    from backend.services import agent_service
    from backend.tasks import agent_schedules

    _reserve_nothing_for_the_commit(monkeypatch)
    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "Q" * 6000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    # The loop seeds its pre-pass check with the shipped turn price; a 1 s wall needs
    # that seed moved with it (the budget itself never runs - the lambda answers).
    monkeypatch.setattr(curation, "_MODEL_TURN_SECONDS", 0.015625)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    # A curator whose incremental position is AHEAD of the backlog it is about to drain:
    # the whole corpus sits behind the watermark, which is the shape that made the old
    # full-history run unable to checkpoint at all.
    await pool.execute(
        "UPDATE agents SET curated_through=now(), turn_timeout_seconds=1 WHERE id=$1",
        UUID(agent["id"]),
    )
    file_key = f"file:{dataset.private_file}"
    reads: list[int] = []
    second_read = asyncio.Event()
    hold = asyncio.Event()

    async def read_one_slice_then_stall(scope, instructions, route):
        document = scope.documents.get(file_key)
        if document is None:
            return "Nothing of mine in this slice."
        reads.append(document["chunk"]["offset"] if "chunk" in document else 0)
        if not second_read.is_set():
            second_read.set()
            return "First slice read."
        await hold.wait()
        return "Never reached: the wall cuts this pass off."

    monkeypatch.setattr(curation, "run_scope", read_one_slice_then_stall)
    monkeypatch.setattr(agent_schedules.run_curator_now, "apply_async", lambda **kw: None)
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    watermark_before = (str(agent["curated_through"]), str(agent["curated_through_event_id"]))

    with pytest.raises(RuntimeError, match="run time limit"):
        await agent_schedules._run_curator_now(UUID(agent["id"]), full_history=True, metered=False)

    cut = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert cut["drain_through"] is not None, "the committed pass left no resumable position"
    assert cut["drain_cursor"]["partial"], "the pass stopped mid-document without a cursor"
    assert str(cut["curated_through"]), "a drain that found no watermark wrote one it invented"
    assert (str(cut["curated_through"]), str(cut["curated_through_event_id"])) == (
        watermark_before
    ), "the drain advanced the incremental watermark instead of its own pair"

    # A second backfill dispatch for the same lane: the routine accident (a re-delivered
    # broker message, an operator pressing Run now twice) that a seeding bug would turn
    # into the same first slice curated a second time. `reported` is its own ledger, so
    # every offset in it belongs to THIS dispatch — nothing has to be sliced away.
    reported = []

    async def report_what_is_read(scope, instructions, route):
        document = scope.documents.get(file_key)
        if document is not None:
            offset = document["chunk"]["offset"] if "chunk" in document else 0
            reported.append(offset)
            if offset == 0:
                return "The first slice again, which was already curated."
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", report_what_is_read)
    caplog.set_level(logging.INFO, logger="backend.tasks.agent_schedules")
    await agent_schedules._run_curator_now(UUID(agent["id"]), full_history=True, metered=False)

    assert reported, "the resumed dispatch read nothing at all"
    assert 0 not in reported, "the second dispatch re-read the curated first slice"
    # The stored cursor, not the stalled pass's read, is the contract the next dispatch
    # honours: it reads the first character the checkpoint says is still unread.
    checkpointed = next(iter(cut["drain_cursor"]["partial"].values()))
    assert reported[0] == checkpointed, (
        "the second dispatch did not resume at the offset the drain cursor names"
    )
    assert reported == sorted(reported), "the resumed drain read its slices out of order"
    assert [
        line.getMessage()
        for line in caplog.records
        if "resumes the history drain" in line.getMessage()
    ], "the resumed drain was dispatched without saying it resumed"
    finished = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert (finished["drain_through"], finished["drain_cursor"]) == (None, {})
    assert finished["last_run_outcome"] == "ran"


@pytest.mark.asyncio
async def test_every_committed_pass_of_a_drain_moves_the_drain_position_and_no_more(
    dataset, pool, monkeypatch, sprite_exec, caplog
):
    """One committed pass, one drain advance: the meter and the row are one account.

    The drain-rate meter is how a cut run becomes readable at all, so it is the witness
    this test checks the durable writes against — a loop that advanced the pair once per
    RUN rather than once per PASS would keep the lane alive and leave the meter disagreeing
    with the row. The pair is watched as the database holds it, after each write, and the
    watermark is watched staying exactly where the drain found it.
    """
    from backend.services import agent_service, sprite_agent_service

    await pool.execute(
        "UPDATE files SET extracted_text=$2 WHERE id=$1", dataset.private_file, "Z" * 6000
    )
    monkeypatch.setattr(curation, "input_budget", lambda ceiling: 2048)
    agent = await agent_service.get_or_create_curator(dataset.owner, wiki="external")
    # A drain already in flight, standing where the watermark stands: everything the feed
    # holds sits ahead of it, so this run finishes the drain and its hand-off has somewhere
    # forward to move the watermark to.
    await pool.execute(
        "UPDATE agents SET curated_through=now() - interval '2 days',"
        " drain_through=now() - interval '2 days' WHERE id=$1",
        UUID(agent["id"]),
    )
    agent = await agent_service.get_agent_by_id(UUID(agent["id"]))
    watermark_before = agent["curated_through"]
    advances: list[datetime] = []
    real_advance = agent_service.advance_drain

    async def record_advance(conn, agent_id, read_at, read_cursor, through, cursor):
        await real_advance(conn, agent_id, read_at, read_cursor, through, cursor)
        stored = await conn.fetchrow(
            "SELECT drain_through, drain_cursor FROM agents WHERE id=$1", agent_id
        )
        advances.append((stored["drain_through"], dict(stored["drain_cursor"])))

    monkeypatch.setattr(agent_service, "advance_drain", record_advance)

    async def read_slice(scope, instructions, route):
        return "Slice read."

    monkeypatch.setattr(curation, "run_scope", read_slice)
    with caplog.at_level(logging.INFO, logger="backend.services.scoped_curation_service"):
        await sprite_agent_service.run_scheduled(
            agent, "drain-pair-run", curation_service.position_of(agent)
        )

    meter = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("curator pass=")
    ]
    assert meter, "a run that drained a document reported no pass"
    assert len(advances) == len(meter), (
        f"the meter counted {len(meter)} committed passes but the drain advanced "
        f"{len(advances)} times"
    )
    assert len(
        {(str(through), json.dumps(cursor, sort_keys=True)) for through, cursor in advances}
    ) == (len(advances)), "two passes committed the same drain position"
    committed = [
        datetime.fromisoformat(line.split("committed_through=")[1].split()[0]) for line in meter
    ]
    assert [through for through, _ in advances] == committed, (
        "the meter reported a position the drain pair does not hold"
    )
    done = await agent_service.get_agent_by_id(UUID(agent["id"]))
    assert (done["drain_through"], done["drain_cursor"]) == (None, {})
    assert done["curated_through"] > watermark_before, (
        "the finished drain cleared its pair and handed nothing to the watermark"
    )
