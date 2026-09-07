"""Folder-bound curators: one project, one curator, one model pick.

The founder's history backfill is the bottleneck a workspace-wide curator
cannot escape: one run reads one feed capped at _MAX_EVENTS across every
session ever recorded. A curator bound to a session folder reads only that
folder's events — the feed SQL scopes them, so out-of-folder material is
never fetched (the privacy invariant holds at the data level, not in the
prompt) — and can run its own model while folder curation is dogfooded
against a self-hosted endpoint. The project's wiki itself lives in the file
tree (pages only hang off `folders`), so the first curator a project gets
opens its wiki home and links it through session_folders.wiki_folder_id.

These tests pin the scope at the feed, the gate, the watermark, and the API
that steers them.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from backend.config import settings
from backend.services import agent_auth, agent_service, curation_service, sprite_agent_service

from .test_curator import _auth, _push_events, _register

OLD = datetime(2020, 1, 1, tzinfo=UTC)
BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
INTERNAL = "internal"


async def _folder(client: AsyncClient, key: str, name: str) -> str:
    r = await client.post("/api/v1/me/session-folders", json={"name": name}, headers=_auth(key))
    assert r.status_code == 200
    return str(r.json()["id"])


async def _file_session(
    client: AsyncClient,
    key: str,
    uid: UUID,
    pool,
    session_id: str,
    folder_id: str,
    content: str,
    at: datetime = BASE,
) -> None:
    """Push one event for `session_id`, then file the session into `folder_id`
    through the production assign route."""
    await _push_events(
        client,
        key,
        [
            {
                "agent_name": "heavi-chat",
                "event_type": "user_message",
                "content": content,
                "session_id": session_id,
                "created_at": at.isoformat(),
            }
        ],
    )
    row_id = await pool.fetchval(
        "SELECT id FROM sessions WHERE owner_user_id = $1 AND session_id = $2",
        uid,
        session_id,
    )
    r = await client.post(
        "/api/v1/me/session-folders/assign",
        json={"session_row_ids": [str(row_id)], "folder_id": folder_id},
        headers=_auth(key),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_scoped_feed_reads_only_its_project(client: AsyncClient, _db_pool, pool):
    """The scope is enforced where the feed is built: out-of-folder sessions —
    filed elsewhere or unfiled — are never fetched, and everything the folder
    curator does not read (its own wiki, files, saves, sources) comes back
    empty rather than as a prompt-level "ignore this"."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    models = await _folder(client, key, "Dátový model")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    await _file_session(client, key, uid, pool, "conv-model", models, "the embedding table")
    await _push_events(
        client,
        key,
        [
            {
                "agent_name": "heavi-chat",
                "event_type": "user_message",
                "content": "unfiled noise",
                "session_id": "conv-loose",
                "created_at": BASE.isoformat(),
            }
        ],
    )

    feed = await curation_service.changes_since(uid, uid, OLD, INTERNAL, UUID(rozvrh))
    assert feed["counts"]["history"] == 1
    assert feed["history"][0]["content"] == "seminars on tuesday"
    assert feed["pages"] == []
    assert all(
        s == [] for s in (feed["files"], feed["source_docs"], feed["saves"], feed["sources"])
    )
    backlog = await curation_service.curator_event_backlog(uid, INTERNAL, OLD, UUID(rozvrh))
    assert backlog["distinct_events"] == 1


@pytest.mark.asyncio
async def test_scoped_watermark_advances_within_its_scope(client: AsyncClient, _db_pool, pool):
    """A folder curator's watermark advances through the folder's events only;
    the unscoped backlog still owes the rest, so the workspace curator's job
    is untouched by the folder curator draining its slice."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    models = await _folder(client, key, "Dátový model")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    await _file_session(
        client,
        key,
        uid,
        pool,
        "conv-model",
        models,
        "the embedding table",
        at=BASE + timedelta(hours=1),
    )

    position = await curation_service.complete_through(uid, None, BASE, INTERNAL, UUID(rozvrh))
    assert position == BASE
    scoped = await curation_service.curator_event_backlog(uid, INTERNAL, position, UUID(rozvrh))
    assert scoped["distinct_events"] == 0
    unscoped = await curation_service.curator_event_backlog(uid, INTERNAL, position)
    assert unscoped["distinct_events"] == 1  # the other folder is still owed


@pytest.mark.asyncio
async def test_folder_curator_crud_over_the_api(client: AsyncClient, _db_pool, pool):
    """The founder steers folder curators over the API: create (idempotent,
    opening the project's wiki home), list with progress, retune the model
    pick and cadence, retire."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")

    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "model_provider": "local", "model_id": "qwen"},
        headers=_auth(key),
    )
    assert r.status_code == 201, r.text
    curator = r.json()["curator"]
    assert curator["curator_folder_id"] is not None
    assert curator["model_id"] == "qwen"
    assert curator["curator_wiki"] == "internal"
    assert curator["curated_through"] is not None  # seeded at the folder's first event
    wiki_folder_id = curator["wiki_folder_id"]
    assert wiki_folder_id is not None

    # The wiki home is one file-tree folder per project, reused when the
    # same binding is posted again (idempotency must not fork the wiki).
    again = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "model_provider": "local", "model_id": "qwen"},
        headers=_auth(key),
    )
    assert again.status_code == 201
    assert again.json()["curator"]["id"] == curator["id"]
    assert again.json()["curator"]["wiki_folder_id"] == wiki_folder_id

    # The list carries the provisioned pair plus this one, with progress.
    r = await client.get("/api/v1/me/curators", headers=_auth(key))
    assert r.status_code == 200
    listed = {c["id"]: c for c in r.json()["curators"]}
    assert curator["id"] in listed
    entry = listed[curator["id"]]
    assert entry["folder_name"] == "Rozvrh"
    assert entry["wiki_folder_id"] == wiki_folder_id
    assert entry["next_run_at"] is not None
    assert entry["event_backlog"]["distinct_events"] == 0  # watermark seeded at that event
    # A plain account is provisioned exactly one workspace curator (internal);
    # the external one only exists on the developer platform.
    assert sum(1 for c in r.json()["curators"] if c["curator_folder_id"] is None) == 1

    # New activity in the folder shows as owed work, nothing else does.
    await _file_session(
        client,
        key,
        uid,
        pool,
        "conv-rozvrh-2",
        rozvrh,
        "office hours moved",
        at=BASE + timedelta(hours=2),
    )
    r = await client.get("/api/v1/me/curators", headers=_auth(key))
    entry = {c["id"]: c for c in r.json()["curators"]}[curator["id"]]
    assert entry["event_backlog"]["distinct_events"] == 1

    # Retune the model pick; absent fields stay untouched.
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"model_id": "other-model"},
        headers=_auth(key),
    )
    assert r.status_code == 200
    assert r.json()["curator"]["model_id"] == "other-model"
    assert r.json()["curator"]["schedule_cron"] == curator["schedule_cron"]

    # Clearing the schedule idles it (the runtime has no enabled column).
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"schedule_cron": None},
        headers=_auth(key),
    )
    assert r.status_code == 200
    assert r.json()["curator"]["schedule_cron"] is None

    # Retire.
    r = await client.delete(f"/api/v1/me/curators/{curator['id']}", headers=_auth(key))
    assert r.status_code == 200
    r = await client.get("/api/v1/me/curators", headers=_auth(key))
    assert curator["id"] not in {c["id"] for c in r.json()["curators"]}


@pytest.mark.asyncio
async def test_folder_curator_requires_local_provider_and_own_folder(client: AsyncClient, _db_pool):
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")

    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "model_provider": "anthropic"},
        headers=_auth(key),
    )
    assert r.status_code == 400

    # Someone else's project folder is not found, not "forbidden".
    other_key, _ = await _register(client)
    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "model_provider": "local"},
        headers=_auth(other_key),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_workspace_curators_are_permanent_and_scoped(client: AsyncClient, _db_pool):
    """DELETE refuses the provisioned pair to its owner; another account's
    curator — seen or unseen — is a 404, never a 400 that leaks its shape."""
    key, uid = await _register(client)
    internal = await agent_service.get_or_create_curator(uid)
    r = await client.delete(f"/api/v1/me/curators/{internal['id']}", headers=_auth(key))
    assert r.status_code == 400

    other_key, _ = await _register(client)
    r = await client.delete(f"/api/v1/me/curators/{internal['id']}", headers=_auth(other_key))
    assert r.status_code == 404
    r = await client.patch(
        f"/api/v1/me/curators/{internal['id']}", json={"model_id": "x"}, headers=_auth(other_key)
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_folder_curator_turn_builds_the_project_prompt(client: AsyncClient, _db_pool, pool):
    """The scheduled turn a folder curator runs names its project in the read
    scope and its wiki home in the write commands — and a deleted project or a
    curator without its wiki home fails loud instead of running the workspace
    prompt."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    curator = await agent_service.create_folder_curator(uid, UUID(rozvrh), "local", "qwen")
    agent = await agent_service.get_curator_by_id(UUID(curator["id"]))

    session_id, prompt = await sprite_agent_service.build_scheduled_turn(agent, "2026-01-02T03-04")
    assert "Rozvrh" in prompt
    assert f"--folder {rozvrh}" in prompt  # the read side: the project folder scopes the feed
    assert f"--folder {curator['wiki_folder_id']}" in prompt  # the write side: the wiki home
    assert "--parent " + curator["wiki_folder_id"] in prompt

    await pool.execute(
        "UPDATE session_folders SET wiki_folder_id = NULL WHERE id = $1", UUID(rozvrh)
    )
    with pytest.raises(ValueError, match="no wiki home"):
        await sprite_agent_service.build_scheduled_turn(agent, "2026-01-02T03-05")

    await pool.execute("DELETE FROM session_folders WHERE id = $1", UUID(rozvrh))
    with pytest.raises(ValueError, match="no longer exists"):
        await sprite_agent_service.build_scheduled_turn(agent, "2026-01-02T03-06")


@pytest.mark.asyncio
async def test_changes_endpoint_accepts_a_folder(client: AsyncClient, _db_pool, pool):
    """`stash changes --folder` lands here: the feed and the backlog it reports
    are both scoped, and a folder that is not the caller's is a 404."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    other = await _folder(client, key, "Other")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    await _file_session(client, key, uid, pool, "conv-other", other, "unrelated work")

    r = await client.get(
        "/api/v1/me/changes",
        params={"wiki": "internal", "since": OLD.isoformat(), "folder": rozvrh},
        headers=_auth(key),
    )
    assert r.status_code == 200
    feed = r.json()
    assert feed["counts"]["history"] == 1
    assert feed["pages"] == []
    assert feed["event_backlog"]["distinct_events"] == 1

    other_key, _ = await _register(client)
    r = await client.get(
        "/api/v1/me/changes",
        params={"wiki": "internal", "folder": rozvrh},
        headers=_auth(other_key),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_repushed_history_does_not_reopen_the_workspace_watermark(
    client: AsyncClient, _db_pool
):
    """The founder's stall, pinned: his Fusion instance re-pushes its own
    transcripts, and the ingest rewind read every re-push as fresh history —
    pulling the watermark back an hour's run at a time, forever. The feed
    dedupes twins by identity; the rewind must apply the same anti-join, or
    ingest and run spend the day fighting each other instead of draining."""
    key, uid = await _register(client)
    curator = await agent_service.get_or_create_curator(uid)
    cid = UUID(curator["id"])
    old = BASE - timedelta(days=1)
    await _db_pool.execute("UPDATE agents SET curated_through = $2 WHERE id = $1", cid, BASE)

    events = [
        {
            "agent_name": "fusion",
            "event_type": "user_message",
            "content": "task SANE-367 transcript, pushed again",
            "session_id": "fusion-task-SANE-367",
            "created_at": old.isoformat(),
        }
    ]
    await _push_events(client, key, events)
    first = await _db_pool.fetchval("SELECT curated_through FROM agents WHERE id = $1", cid)
    assert first == old - timedelta(microseconds=1)  # genuinely old history reopens

    await _push_events(client, key, events)  # the re-push that used to relivelock
    again = await _db_pool.fetchval("SELECT curated_through FROM agents WHERE id = $1", cid)
    assert again == first


@pytest.mark.asyncio
async def test_folder_curator_is_not_at_the_ingest_s_leash(client: AsyncClient, _db_pool, pool):
    """An old event ingested into an unfiled session belongs to no project yet.
    It reopens the workspace curator as always — but must not touch a folder
    curator's position, or any unrelated push holds every project's drain."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    curator = await agent_service.create_folder_curator(uid, UUID(rozvrh), "local", "qwen")
    internal = await agent_service.get_or_create_curator(uid)
    future = BASE + timedelta(days=1)
    await _db_pool.execute(
        "UPDATE agents SET curated_through = $2 WHERE id = $1", UUID(internal["id"]), future
    )

    await _push_events(
        client,
        key,
        [
            {
                "agent_name": "fusion",
                "event_type": "user_message",
                "content": "an old transcript in a session with no folder",
                "session_id": "fusion-task-loose",
                "created_at": (BASE - timedelta(hours=3)).isoformat(),
            }
        ],
    )

    folder_wm = await _db_pool.fetchval(
        "SELECT curated_through FROM agents WHERE id = $1", UUID(curator["id"])
    )
    assert folder_wm == BASE  # untouched
    workspace_wm = await _db_pool.fetchval(
        "SELECT curated_through FROM agents WHERE id = $1", UUID(internal["id"])
    )
    assert workspace_wm == BASE - timedelta(
        hours=3, microseconds=1
    )  # workspace rewinds as designed


@pytest.mark.asyncio
async def test_filing_old_sessions_reopens_the_folder_position(client: AsyncClient, _db_pool, pool):
    """The folder curator's promise — everything filed into the project gets
    read — is kept where filing happens: assigning a session whose events
    predate the watermark reopens the folder curator's position to them."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    curator = await agent_service.create_folder_curator(uid, UUID(rozvrh), "local", "qwen")
    cid = UUID(curator["id"])
    assert await _db_pool.fetchval("SELECT curated_through FROM agents WHERE id = $1", cid) == BASE

    older = BASE - timedelta(hours=6)
    await _file_session(client, key, uid, pool, "conv-old", rozvrh, "the old kickoff", at=older)

    wm = await _db_pool.fetchval("SELECT curated_through FROM agents WHERE id = $1", cid)
    assert wm == older - timedelta(microseconds=1)
    backlog = await curation_service.curator_event_backlog(uid, INTERNAL, wm, UUID(rozvrh))
    assert backlog["distinct_events"] == 2  # both the filed history and the fresh event


@pytest.mark.asyncio
async def test_digest_model_travels_with_the_curator(client: AsyncClient, _db_pool, pool):
    """The two-phase knobs are configuration, not fate: set at create, retuned
    by PATCH, cleared by an explicit null — and never a nonsensical pair
    (a digest model needs a digest provider, and a folder curator's digest
    lives on the self-hosted endpoint like its main model)."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")

    r = await client.post(
        "/api/v1/me/curators",
        json={
            "folder_id": rozvrh,
            "model_provider": "local",
            "model_id": "qwen",
            "digest_provider": "local",
            "digest_model_id": "qwen-fast",
        },
        headers=_auth(key),
    )
    assert r.status_code == 201, r.text
    curator = r.json()["curator"]
    assert curator["digest_provider"] == "local"
    assert curator["digest_model_id"] == "qwen-fast"

    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"digest_model_id": "other-fast"},
        headers=_auth(key),
    )
    assert r.status_code == 200
    assert r.json()["curator"]["digest_model_id"] == "other-fast"
    assert r.json()["curator"]["digest_provider"] == "local"  # absent, untouched

    # An explicit null drops the curator back to single-phase.
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"digest_provider": None},
        headers=_auth(key),
    )
    assert r.status_code == 200
    assert r.json()["curator"]["digest_provider"] is None

    # Nonsensical pairs are refused at both doors.
    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "digest_model_id": "orphan"},
        headers=_auth(key),
    )
    assert r.status_code == 400
    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "digest_provider": "anthropic"},
        headers=_auth(key),
    )
    assert r.status_code == 400
    await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"digest_provider": None},
        headers=_auth(key),
    )
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"digest_provider": "anthropic"},
        headers=_auth(key),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_external_curator_refuses_a_second_model(client: AsyncClient, _db_pool):
    """The external feed is end-user material: a digest model would be a
    second processor of customer data. The API says no."""
    key, uid = await _register(client)
    internal = await agent_service.get_or_create_curator(uid)
    await _db_pool.execute(
        "UPDATE agents SET curator_wiki = 'external' WHERE id = $1", UUID(internal["id"])
    )
    r = await client.patch(
        f"/api/v1/me/curators/{internal['id']}",
        json={"digest_provider": "local"},
        headers=_auth(key),
    )
    assert r.status_code == 400

    # The workspace (internal, unbound) curator may use the hosters' providers.
    await _db_pool.execute(
        "UPDATE agents SET curator_wiki = 'internal' WHERE id = $1", UUID(internal["id"])
    )
    r = await client.patch(
        f"/api/v1/me/curators/{internal['id']}",
        json={"digest_provider": "anthropic"},
        headers=_auth(key),
    )
    assert r.status_code == 200
    assert r.json()["curator"]["digest_provider"] == "anthropic"


@pytest.mark.asyncio
async def test_two_phase_run_digests_then_writes_from_the_report(
    client: AsyncClient, _db_pool, pool, monkeypatch
):
    """The run the founder asked for: a fast model reads the scoped feed and
    reports extracts, then the curator's own model writes the wiki from the
    report — never rerunning the feed command itself. The digest turn lands in
    its own `agent-curate-` session so its transcript stays out of the feed."""

    async def fake_run_chat(user_id, owner_name, uid, session_id, message, **kwargs):
        calls.append((session_id, message, kwargs))
        return "EXTRACT: seminars moved to thursday (conv-rozvrh)" if len(calls) == 1 else "LOG"

    calls: list = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", fake_run_chat)

    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    curator = await agent_service.create_folder_curator(
        uid, UUID(rozvrh), "local", "qwen", digest_provider="local", digest_model_id="qwen-fast"
    )
    agent = await agent_service.get_curator_by_id(UUID(curator["id"]))

    result = await sprite_agent_service.run_scheduled(agent, "20260102030405")
    assert result == "LOG"

    assert len(calls) == 2
    digest_session, digest_prompt, digest_kwargs = calls[0]
    writer_session, writer_prompt, writer_kwargs = calls[1]

    assert digest_session.startswith(f"agent-curate-{curator['id']}-")
    assert digest_session.endswith("-digest")
    assert not writer_session.endswith("-digest")
    assert digest_kwargs["model_id"] == "qwen-fast"
    assert writer_kwargs["model_id"] == "qwen"

    assert "Digest the Curation Delta" in digest_prompt
    assert f"stash changes --folder {rozvrh}" in digest_prompt  # the same feed, fast model

    assert "## Digest report" in writer_prompt
    assert "EXTRACT: seminars moved to thursday" in writer_prompt
    assert f"stash changes --folder {rozvrh}" not in writer_prompt  # no rereading


@pytest.mark.asyncio
async def test_empty_digest_fails_the_run_before_the_writer(
    client: AsyncClient, _db_pool, pool, monkeypatch
):
    """A digest that yields nothing must fail the run — writing a wiki from an
    empty report would curate nothing and advance the watermark over
    everything, silently dropping the backlog."""

    async def fake_run_chat(user_id, owner_name, uid, session_id, message, **kwargs):
        calls.append(session_id)
        return "   "

    calls: list = []
    monkeypatch.setattr(sprite_agent_service, "run_chat", fake_run_chat)

    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    await _file_session(client, key, uid, pool, "conv-rozvrh", rozvrh, "seminars on tuesday")
    curator = await agent_service.create_folder_curator(
        uid, UUID(rozvrh), "local", "qwen", digest_provider="local", digest_model_id="qwen-fast"
    )
    agent = await agent_service.get_curator_by_id(UUID(curator["id"]))

    with pytest.raises(RuntimeError, match="no extracts"):
        await sprite_agent_service.run_scheduled(agent, "20260102030406")
    assert len(calls) == 1  # the writer never ran


@pytest.mark.asyncio
async def test_folder_curator_without_model_selection_inherits(client: AsyncClient, _db_pool, pool):
    """Scope B's promise: a curator created with no model selection stores
    NULL/NULL/NULL — the same row shape the workspace curators carry — so its
    turns resolve on the shared path (the default curator model), not through
    a folder-curator branch. A selection without a provider is refused: the
    row would carry a pin that decides nothing and silently reads as someone
    having un-inherited it."""
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")

    r = await client.post("/api/v1/me/curators", json={"folder_id": rozvrh}, headers=_auth(key))
    assert r.status_code == 201, r.text
    curator = r.json()["curator"]
    assert curator["model_provider"] is None
    assert curator["model_id"] is None
    assert curator["credential_id"] is None
    stored = await pool.fetchrow(
        "SELECT model_provider, model_id, credential_id FROM agents WHERE id = $1",
        UUID(curator["id"]),
    )
    assert stored["model_provider"] is None
    assert stored["model_id"] is None
    assert stored["credential_id"] is None

    # The provisioned workspace curator carries the identical shape — this is
    # the same resolution path, demonstrably not a new special case.
    workspace = await agent_service.get_or_create_curator(uid)
    assert workspace["model_provider"] is None

    # A dangling selection is refused at PATCH time too, and nothing lands.
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}", json={"model_id": "qwen"}, headers=_auth(key)
    )
    assert r.status_code == 400
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"credential_id": str(uuid4())},
        headers=_auth(key),
    )
    assert r.status_code == 400
    stored = await pool.fetchrow(
        "SELECT model_provider, model_id, credential_id FROM agents WHERE id = $1",
        UUID(curator["id"]),
    )
    assert stored["model_id"] is None
    assert stored["credential_id"] is None


@pytest.mark.asyncio
async def test_folder_curator_pin_and_digest_round_trip(
    client: AsyncClient, _db_pool, pool, monkeypatch
):
    """Explicit local + credential_id + digest fields: the pin survives later
    PATCHes (digest retunes must not silently drop the curator's box), and
    moving the pin moves the box."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    key, uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    box_a = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret("http://box-a:11434/v1", "llama"),
        name="box-a",
    )
    box_b = await agent_auth.store_credential(
        uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret("http://box-b:11434/v1", "qwen"),
        name="box-b",
    )

    r = await client.post(
        "/api/v1/me/curators",
        json={
            "folder_id": rozvrh,
            "model_provider": "local",
            "model_id": "llama",
            "credential_id": str(box_a),
        },
        headers=_auth(key),
    )
    assert r.status_code == 201, r.text
    curator = r.json()["curator"]
    assert curator["credential_id"] == str(box_a)
    assert curator["model_id"] == "llama"

    # Retuning the digest leaves the endpoint pin exactly where it was.
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"digest_provider": "local", "digest_model_id": "cheap"},
        headers=_auth(key),
    )
    assert r.status_code == 200, r.text
    assert r.json()["curator"]["credential_id"] == str(box_a)
    assert r.json()["curator"]["digest_model_id"] == "cheap"

    # Moving the pin moves the box — the id, not the model name, decides.
    r = await client.patch(
        f"/api/v1/me/curators/{curator['id']}",
        json={"credential_id": str(box_b)},
        headers=_auth(key),
    )
    assert r.status_code == 200, r.text
    assert r.json()["curator"]["credential_id"] == str(box_b)


@pytest.mark.asyncio
async def test_folder_curator_refuses_dangling_and_foreign_pins(
    client: AsyncClient, _db_pool, monkeypatch
):
    """Create-time refusals: credential_id without a model_provider, and a pin
    pointing at someone else's endpoint — saving either would only blow up at
    the next run, when the cause is hardest to see."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    key, uid = await _register(client)
    other_key, other_uid = await _register(client)
    rozvrh = await _folder(client, key, "Rozvrh")
    foreign = await agent_auth.store_credential(
        other_uid,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret("http://not-yours:11434/v1", "llama"),
        name="not-yours",
    )

    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "credential_id": str(foreign)},
        headers=_auth(key),
    )
    assert r.status_code == 400  # dangling AND foreign

    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "model_provider": "local", "credential_id": str(foreign)},
        headers=_auth(key),
    )
    assert r.status_code == 400  # foreign, even with a provider

    r = await client.post(
        "/api/v1/me/curators",
        json={"folder_id": rozvrh, "model_id": "qwen"},
        headers=_auth(key),
    )
    assert r.status_code == 400  # model_id without a provider

    # Nothing half-saved: no curator row exists for the folder.
    listed = await client.get("/api/v1/me/curators", headers=_auth(key))
    assert listed.status_code == 200
    assert all(c["curator_folder_id"] is None for c in listed.json()["curators"])
