"""The per-project shared-wiki opt-in (`session_folders.share_wiki`).

A developer's workspace holds sessions from many projects, and a project is a
session folder. This column is what lets a developer clear ONE project's history
for the shared (cross-user) wiki while leaving the rest personal — the control
that the single per-user toggle could not express.

What matters here:
- The default is OFF: a project contributes nothing to the shared wiki until the
  developer clears it on purpose.
- The toggle is scope-local: another scope's folder can never be flipped.
- The Default folder has no toggle — it is the catch-all for unfiled sessions,
  so it carries no routing decision.
"""

from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import session_folder_service

from .conftest import unique_name


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


async def _register(client: AsyncClient, prefix: str) -> tuple[str, UUID]:
    """A fresh account: its API key and its id (which is its personal scope)."""
    name = unique_name(prefix)
    resp = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@test.local"},
    )
    assert resp.status_code == 201
    body = resp.json()
    return body["api_key"], UUID(body["id"])


async def _create_folder(client: AsyncClient, key: str, name: str) -> dict:
    resp = await client.post("/api/v1/me/session-folders", json={"name": name}, headers=_auth(key))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _listed_folders(client: AsyncClient, key: str) -> list[dict]:
    resp = await client.get("/api/v1/me/session-folders", headers=_auth(key))
    assert resp.status_code == 200, resp.text
    return resp.json()["folders"]


@pytest.mark.asyncio
async def test_new_folder_starts_not_sharing(client: AsyncClient):
    """A project is opted out until the developer says otherwise — over HTTP, in
    both the create response and the list the console groups on."""
    key, _uid = await _register(client, "sw-default")
    created = await _create_folder(client, key, "riverside-truck-parts")
    assert created["share_wiki"] is False

    listed = {f["name"]: f for f in await _listed_folders(client, key)}
    assert listed["riverside-truck-parts"]["share_wiki"] is False


@pytest.mark.asyncio
async def test_toggle_round_trips_both_ways(client: AsyncClient):
    """Clearing a project and withdrawing that clearance are the same one write,
    and the read model reflects each immediately."""
    key, uid = await _register(client, "sw-roundtrip")
    folder = await _create_folder(client, key, "acme-diesel")
    folder_id = UUID(folder["id"])

    on = await session_folder_service.set_folder_share_wiki(
        scope_user_id=uid, folder_id=folder_id, share_wiki=True
    )
    assert on is not None and on["share_wiki"] is True
    listed = {f["name"]: f for f in await _listed_folders(client, key)}
    assert listed["acme-diesel"]["share_wiki"] is True

    off = await session_folder_service.set_folder_share_wiki(
        scope_user_id=uid, folder_id=folder_id, share_wiki=False
    )
    assert off is not None and off["share_wiki"] is False


@pytest.mark.asyncio
async def test_sharing_project_names_lists_only_opted_in_projects(client: AsyncClient):
    """The prompt section names exactly the cleared projects: the Default folder
    is never one of them, an untouched project is never one of them."""
    key, uid = await _register(client, "sw-names")
    await _create_folder(client, key, "beta-repair")
    cleared = await _create_folder(client, key, "acme-diesel")
    # Listing ensures the scope's Default folder exists, like any console visit.
    default = {f["name"]: f for f in await _listed_folders(client, key)}["Default"]
    assert default["is_default"] is True

    await session_folder_service.set_folder_share_wiki(
        scope_user_id=uid, folder_id=UUID(cleared["id"]), share_wiki=True
    )

    assert await session_folder_service.sharing_project_names(uid) == ["acme-diesel"]


@pytest.mark.asyncio
async def test_default_folder_has_no_toggle(client: AsyncClient, pool):
    """D5: Default is the unfiled catch-all, not a routing decision. The write
    refuses it and the row keeps its default."""
    key, uid = await _register(client, "sw-default-notoggle")
    default = {f["name"]: f for f in await _listed_folders(client, key)}["Default"]
    default_id = UUID(default["id"])

    assert (
        await session_folder_service.set_folder_share_wiki(
            scope_user_id=uid, folder_id=default_id, share_wiki=True
        )
        is None
    )
    assert (
        await pool.fetchval("SELECT share_wiki FROM session_folders WHERE id = $1", default_id)
        is False
    )


@pytest.mark.asyncio
async def test_another_scopes_folder_cannot_be_toggled(client: AsyncClient, pool):
    """The write is keyed on the folder's owner, so a stranger's project is
    unflippable — and it reports nothing happened rather than pretending."""
    owner_key, _owner = await _register(client, "sw-other-owner")
    stranger_key, stranger_uid = await _register(client, "sw-other-stranger")
    folder = await _create_folder(client, owner_key, "acme-diesel")

    assert (
        await session_folder_service.set_folder_share_wiki(
            scope_user_id=stranger_uid, folder_id=UUID(folder["id"]), share_wiki=True
        )
        is None
    )
    assert (
        await pool.fetchval(
            "SELECT share_wiki FROM session_folders WHERE id = $1", UUID(folder["id"])
        )
        is False
    )
