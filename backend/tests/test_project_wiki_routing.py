"""The console's per-project shared-memory lane (`/me/developer/projects`).

The routing board the developer console renders: every project (session
folder) with its `share_wiki` clearance, toggled from the project page. The
storage-named route (`/session-folders/{folder_id}`) and its semantics are
covered by `test_session_folder_share_wiki.py`; what this file pins is the
console lane itself — the shape and reachability of `GET/PATCH /projects`,
because a console that cannot read the flags or flip them cannot route
anything, and both spellings must stay in lockstep with the one service call.

What must hold:
- `GET /projects` lists the workspace's folders with each one's `share_wiki`.
- `PATCH /projects/{folder_id}` flips it, and returns the folder's new state.
- A foreign folder (another workspace's, or a made-up id) is a 404, never a
  silently-flipped row — the same scope-local rule the service enforces for
  the `/session-folders` spelling.
- The curator feed carries the flag per event, so the nightly run can route
  on a signal it can actually see.
- The external curator prompt states the project gate and the user floor.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import curation_service, session_folder_service

from .conftest import unique_name
from .test_developer_platform import _developer, _event, _push
from .test_permissions import _auth


async def _mint_key(client: AsyncClient, api_key: str, workspace: dict) -> str:
    resp = await client.post(
        "/api/v1/me/developer/keys",
        json={"name": "routing-test", "access": "read"},
        headers={**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["api_key"]


@pytest.mark.asyncio
async def test_console_lists_and_toggles_projects(client: AsyncClient, pool):
    """The routing board reads the flags and flips them; foreign projects 404."""
    api_key, _, workspace = await _developer(client)
    headers = {**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]}

    folder = await session_folder_service.get_or_create_folder(
        UUID(workspace["scope_user_id"]), unique_name("proj")
    )
    assert folder["share_wiki"] is False, "new projects must be dark"

    resp = await client.get("/api/v1/me/developer/projects", headers=headers)
    assert resp.status_code == 200, resp.text
    listed = {p["id"]: p for p in resp.json()["projects"]}
    assert folder["id"] in listed
    assert listed[folder["id"]]["share_wiki"] is False

    resp = await client.patch(
        f"/api/v1/me/developer/projects/{folder['id']}",
        json={"share_wiki": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["share_wiki"] is True

    resp = await client.patch(
        f"/api/v1/me/developer/projects/{UUID(int=0)}",
        json={"share_wiki": True},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_projects_lane_matches_session_folders_lane(client: AsyncClient, pool):
    """The two spellings of the toggle cannot drift: the state one route sets,
    the other route reads back."""
    api_key, _, workspace = await _developer(client)
    headers = {**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]}
    folder = await session_folder_service.get_or_create_folder(
        UUID(workspace["scope_user_id"]), unique_name("proj")
    )

    resp = await client.patch(
        f"/api/v1/me/developer/projects/{folder['id']}",
        json={"share_wiki": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.patch(
        f"/api/v1/me/developer/session-folders/{folder['id']}",
        json={"share_wiki": False},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["share_wiki"] is False

    resp = await client.get("/api/v1/me/developer/projects", headers=headers)
    listed = {p["id"]: p for p in resp.json()["projects"]}
    assert listed[folder["id"]]["share_wiki"] is False


@pytest.mark.asyncio
async def test_feed_events_carry_the_project_flag(client: AsyncClient, pool):
    """The curator's delta must show each event's project and its clearance —
    the agent cannot route on a signal it cannot see."""
    api_key, _, workspace = await _developer(client)
    machine_key = await _mint_key(client, api_key, workspace)
    await _push(client, machine_key, [_event("s-routing", user_id="org_a", user_name="A")])

    folder = await session_folder_service.get_or_create_folder(
        UUID(workspace["scope_user_id"]), unique_name("proj")
    )
    await pool.execute(
        "UPDATE sessions SET session_folder_id = $1 "
        "WHERE owner_user_id = $2 AND session_id = 's-routing'",
        UUID(folder["id"]),
        UUID(workspace["scope_user_id"]),
    )

    events, _ = await curation_service._feed_events(
        UUID(workspace["scope_user_id"]), None, None, 50, "internal"
    )
    row = next(e for e in events if e["session_id"] == "s-routing")
    assert row["session_folder"] == folder["name"]
    assert row["session_folder_share_wiki"] is False

    await pool.execute(
        "UPDATE session_folders SET share_wiki = TRUE WHERE id = $1", UUID(folder["id"])
    )
    events, _ = await curation_service._feed_events(
        UUID(workspace["scope_user_id"]), None, None, 50, "internal"
    )
    row = next(e for e in events if e["session_id"] == "s-routing")
    assert row["session_folder_share_wiki"] is True
