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
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import agent_service, curation_service, sprite_agent_service

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
