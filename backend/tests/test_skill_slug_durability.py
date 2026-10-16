"""A published skill's handle belongs to the folder, not to the publish record.

The MCP URL is the one skill identifier that leaves Stash and gets pasted into a
coding agent's config, where it is expected to go on answering. Minting a slug per
publish record meant `unpublish` followed by `publish` — the ordinary way to retune
a skill's reach — killed every URL the owner had already handed out, permanently,
and nothing restored it. So `skill_slugs` reserves a folder's handle and the
`skills` row alone decides whether that handle answers.

These drive the real publish API rather than a helper: the defect was only ever
visible at the seam where a URL is minted, and a unit test over `_slugify` would
have passed the whole time it was live.
"""

import importlib
import os
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.services import shared_skill_service
from backend.tests.migration_ids import module_for_slug

from .conftest import unique_name

BACKFILL = module_for_slug("skill_slug_is_durable")

SKILL_MD = """---
name: keep-its-address
description: Answers on the URL it minted.
---

Body.
"""


async def _register(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/users/register",
        json={"name": unique_name(), "password": "securepassword1"},
    )
    body = response.json()
    return {"Authorization": f"Bearer {body['api_key']}"}


async def _skill_folder(client: AsyncClient, headers: dict, name: str) -> str:
    folder = await client.post("/api/v1/me/folders", json={"name": name}, headers=headers)
    assert folder.status_code == 201, folder.text
    page = await client.post(
        "/api/v1/me/pages/new",
        json={"name": "SKILL.md", "folder_id": folder.json()["id"], "content": SKILL_MD},
        headers=headers,
    )
    assert page.status_code == 201, page.text
    return folder.json()["id"]


async def _publish(client: AsyncClient, headers: dict, folder_id: str, title: str) -> dict:
    published = await client.post(
        "/api/v1/me/skills",
        json={
            "folder_id": folder_id,
            "title": title,
            "description": "Answers on the URL it minted.",
            "discoverable": False,
        },
        headers=headers,
    )
    assert published.status_code == 201, published.text
    return published.json()


async def _unpublish(client: AsyncClient, headers: dict, skill_id: str) -> None:
    response = await client.delete(f"/api/v1/skills/{skill_id}", headers=headers)
    assert response.status_code == 204, response.text


async def _reserved_slug(pool, folder_id: str) -> str | None:
    return await pool.fetchval("SELECT slug FROM skill_slugs WHERE folder_id = $1", UUID(folder_id))


@pytest.mark.asyncio
async def test_the_reservation_outlives_the_publish_record(client: AsyncClient, pool):
    """The handle is not recycled by closing the share, and it is not deleted with it.

    This is the row a republish reads back. Deleting it on unpublish would put us
    straight back to minting a new URL per publish, which is the defect.
    """
    headers = await _register(client)
    folder_id = await _skill_folder(client, headers, "Release notes")
    skill = await _publish(client, headers, folder_id, "Release notes")

    await _unpublish(client, headers, skill["id"])

    assert await _reserved_slug(pool, folder_id) == skill["slug"]
    assert await pool.fetchval("SELECT slug FROM skills WHERE id = $1", UUID(skill["id"])) is None


@pytest.mark.asyncio
async def test_republishing_answers_on_the_handle_it_minted(client: AsyncClient, pool):
    """Publish, close the share, reopen it: the reopened share must be the same URL.

    The title is chosen again on the way back in, and it still does not move the
    handle — a follower's URL is not the owner's to change.
    """
    headers = await _register(client)
    folder_id = await _skill_folder(client, headers, "Migration notes")
    first = await _publish(client, headers, folder_id, "Migration notes")

    await _unpublish(client, headers, first["id"])

    second = await _publish(client, headers, folder_id, "Migration notes, take two")
    assert second["slug"] == first["slug"]
    assert second["id"] != first["id"], "republish must be a new publish record"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM skill_slugs WHERE folder_id = $1", UUID(folder_id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_renaming_the_folder_never_rotates_the_handle(client: AsyncClient):
    """`folder_mcp_tokens` states this rule for a shared folder and it holds here too.

    Renaming while unpublished is the case that matters: the folder is private, so
    nobody can reach the old URL to be told it changed, and the owner's agent is
    still configured with it.
    """
    headers = await _register(client)
    folder_id = await _skill_folder(client, headers, "Weekly review")
    skill = await _publish(client, headers, folder_id, "Weekly review")
    await _unpublish(client, headers, skill["id"])

    renamed = await client.patch(
        f"/api/v1/me/folders/{folder_id}", json={"name": "Monthly review"}, headers=headers
    )
    assert renamed.status_code == 200, renamed.text

    republished = await _publish(client, headers, folder_id, "Monthly review")
    assert republished["slug"] == skill["slug"]


@pytest.mark.asyncio
async def test_deleting_the_folder_is_what_retires_the_handle(client: AsyncClient, pool):
    """The reservation is the folder's, so it goes when the folder goes.

    Cascade rather than an explicit delete is what lets a folder created later
    under the same title mint a handle of its own instead of clashing forever with
    one nobody can name.
    """
    headers = await _register(client)
    folder_id = await _skill_folder(client, headers, "Temporary")
    skill = await _publish(client, headers, folder_id, "Temporary")
    await _unpublish(client, headers, skill["id"])

    # The negative control: closing the share must leave the reservation standing,
    # or the assertion below would also be satisfied by an unpublish that threw the
    # handle away — which is the defect this whole change exists to stop.
    assert await _reserved_slug(pool, folder_id) == skill["slug"]

    deleted = await client.delete(f"/api/v1/me/folders/{folder_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    assert await _reserved_slug(pool, folder_id) is None

    revived = await _skill_folder(client, headers, "Temporary")
    reminted = await _publish(client, headers, revived, "Temporary")
    assert reminted["slug"].startswith("temporary-")
    assert reminted["slug"] != skill["slug"]


@pytest.mark.asyncio
async def test_two_folders_sharing_a_title_mint_their_own_handles(client: AsyncClient):
    """Uniqueness is per folder, so two skills named alike are two different handles.

    The title is what a handle is minted from, and two owners may name their skill
    the same thing. The clash branch exists for exactly this: the second publisher
    must be handed a different candidate rather than fail, or read the first
    folder's reservation and answer on someone else's URL.
    """
    headers = await _register(client)
    first_folder = await _skill_folder(client, headers, "Standup (mine)")
    second_folder = await _skill_folder(client, headers, "Standup (theirs)")

    first = await _publish(client, headers, first_folder, "Standup")
    second = await _publish(client, headers, second_folder, "Standup")

    assert first["slug"] != second["slug"]
    assert first["slug"].startswith("standup-")
    assert second["slug"].startswith("standup-")


@pytest.mark.asyncio
async def test_a_refused_publish_reserves_nothing_and_rotates_nothing(client: AsyncClient, pool):
    """A publish that never happened must not spend a handle or move an existing one.

    The reservation is taken after every ownership and `SKILL.md` check, so a
    refusal leaves no row an attacker or a typo burned, and the owner's next
    publish still gets the handle their distributed URLs already name.
    """
    owner = await _register(client)
    stranger = await _register(client)
    folder_id = await _skill_folder(client, owner, "Private recipe")
    skill = await _publish(client, owner, folder_id, "Private recipe")
    await _unpublish(client, owner, skill["id"])

    outside_scope = await client.post(
        "/api/v1/me/skills",
        json={
            "folder_id": folder_id,
            "title": "Private recipe",
            "description": "Not the stranger's to publish.",
            "discoverable": False,
        },
        headers=stranger,
    )
    assert outside_scope.status_code == 400, outside_scope.text

    republished = await _publish(client, owner, folder_id, "Private recipe")
    published_twice = await client.post(
        "/api/v1/me/skills",
        json={
            "folder_id": folder_id,
            "title": "Private recipe",
            "description": "Published twice by mistake.",
            "discoverable": False,
        },
        headers=owner,
    )
    assert published_twice.status_code == 400, published_twice.text

    assert await _reserved_slug(pool, folder_id) == skill["slug"]
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM skill_slugs WHERE folder_id = $1", UUID(folder_id)
        )
        == 1
    )
    assert republished["slug"] == skill["slug"]


class _BlindPool:
    """A pool that cannot see one existing reservation — the race window, on demand."""

    def __init__(self, pool) -> None:
        self.pool = pool
        self.blinded = False

    def __getattr__(self, name):
        return getattr(self.pool, name)

    async def fetchval(self, query, *args):
        if "FROM skill_slugs" in query and not self.blinded:
            self.blinded = True
            return None
        return await self.pool.fetchval(query, *args)


@pytest.mark.asyncio
async def test_the_losing_publisher_adopts_the_winners_handle(
    client: AsyncClient, pool, monkeypatch
):
    """Two publishers of one folder must agree on one slug, not produce two.

    The window is the read-then-insert gap inside the reservation. Standing one
    publisher in it — blind to a reservation that is already committed — makes its
    INSERT meet the real primary-key clash, so this exercises that branch against
    Postgres rather than a stub. The minted candidate it lost with must not reach
    `skills`, and must not be left reserved either: a second row for one folder is
    what a `folder_id` primary key is there to forbid.
    """
    headers = await _register(client)
    folder_id = await _skill_folder(client, headers, "Contested")
    skill = await _publish(client, headers, folder_id, "Contested")
    await _unpublish(client, headers, skill["id"])

    blind = _BlindPool(shared_skill_service.get_pool())
    monkeypatch.setattr(shared_skill_service, "get_pool", lambda: blind)

    republished = await _publish(client, headers, folder_id, "Contested")

    assert republished["slug"] == skill["slug"]
    assert await _reserved_slug(pool, folder_id) == skill["slug"]


# --- The backfill: deployed URLs must survive the deploy that made them durable ---


def _insert(conn, statement: str, **params) -> str:
    return str(conn.execute(text(statement), params).scalar_one())


def _value(conn, statement: str, **params):
    return conn.execute(text(statement), params).scalar()


def _backfill(conn) -> None:
    """The migration's real `upgrade()` — the call path a boot-time upgrade takes."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module(BACKFILL)
    with Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()


async def _rolled_back(work) -> None:
    """Run `work(sync_connection)` and put every byte back, schema changes included."""
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            await conn.run_sync(work)
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_backfill_carries_the_handles_already_handed_out():
    """Every deployed URL is a row someone configured an agent with.

    Without this copy, the migration that makes handles durable would itself be the
    thing that breaks them: the tables arrive empty while the published skills keep
    their slugs, and the first unpublish after the deploy would strand a handle no
    row remembers. Dropping the table stands the database back at the pre-migration
    shape, which is what a re-run against it should reproduce — identical data,
    because the copy derives from `skills` and invents nothing.
    """

    def work(conn):
        owner = _insert(
            conn,
            "INSERT INTO users (name, display_name) VALUES (:name, 'Carried') RETURNING id",
            name=unique_name("slug-carried"),
        )
        folder = _insert(
            conn,
            "INSERT INTO folders (owner_user_id, created_by, name) VALUES (:owner, :owner, 'Shipped') RETURNING id",
            owner=owner,
        )
        slug = unique_name("shipped")
        conn.execute(
            text(
                "INSERT INTO skills (owner_user_id, owner_id, slug, title, folder_id) "
                "VALUES (:owner, :owner, :slug, 'Shipped', :folder)"
            ),
            {"owner": owner, "slug": slug, "folder": folder},
        )

        conn.execute(text("DROP TABLE IF EXISTS skill_slugs"))
        _backfill(conn)

        assert (
            _value(conn, "SELECT slug FROM skill_slugs WHERE folder_id = :folder", folder=folder)
            == slug
        ), "the live slug was not carried into the reservation table"

        conn.execute(text("DROP TABLE IF EXISTS skill_slugs"))
        _backfill(conn)

        assert (
            _value(conn, "SELECT slug FROM skill_slugs WHERE folder_id = :folder", folder=folder)
            == slug
        ), "re-running the copy against the same shape landed different data"

        assert _value(
            conn,
            "SELECT 1 FROM pg_constraint WHERE conname = 'skill_slugs_slug_key' AND contype = 'u'",
        ), "the handle column is not UNIQUE, so two folders could reserve one URL"
        assert _value(
            conn,
            "SELECT 1 FROM pg_constraint WHERE conname = 'skill_slugs_folder_id_fkey' AND contype = 'f'",
        ), "folder_id is not a real foreign key, so ON DELETE CASCADE is not in force"

        conn.execute(text("DELETE FROM folders WHERE id = :folder"), {"folder": folder})

        assert (
            _value(
                conn, "SELECT count(*) FROM skill_slugs WHERE folder_id = :folder", folder=folder
            )
            == 0
        ), "deleting the folder did not retire its handle"

    await _rolled_back(work)
