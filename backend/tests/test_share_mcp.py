"""The share URLs an MCP client dials, and what kills them.

These are protocol tests: they drive a real MCP client session (the mcp SDK's
streamable-http client, the same one a coding agent's `mcp add` uses) against
the endpoint mounted in the app, at the URL the product itself printed into the
share dialog. Asserting on the protocol rather than on a service call is the
point — an owner configures a client with a URL, and what they need to be true
is that the URL works while the share lives and stops working the moment it
does not.

The revocation tests are the reason this surface exists. A share that stops
being a share must not keep answering: unpublished skills and withdrawn
folders die at the HTTP layer, before a tool is ever reached. And a handle
grants exactly what a public link grants — an MCP client presents no identity,
so a folder shared with named people only has no working URL.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.main import app
from backend.routers import share_mcp
from backend.services import storage_service
from backend.tasks import extraction

from .conftest import unique_name


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


async def _register(client: AsyncClient, prefix: str) -> str:
    name = unique_name(prefix)
    resp = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@test.local"},
    )
    assert resp.status_code == 201
    return resp.json()["api_key"]


@pytest.fixture
def stub_storage(monkeypatch):
    """Shared files live in S3; CI has none, so blobs live in a dict."""
    blobs: dict[str, bytes] = {}

    async def _upload(owner_user_id, filename, content, content_type):
        key = f"test/{owner_user_id}/{filename}"
        blobs[key] = content
        return key

    async def _url(storage_key, expires_in=3600):
        return f"https://files.test/{storage_key}"

    async def _download(storage_key):
        return blobs[storage_key]

    monkeypatch.setattr(storage_service, "is_configured", lambda: True)
    monkeypatch.setattr(storage_service, "upload_file", _upload)
    monkeypatch.setattr(storage_service, "get_file_url", _url)
    monkeypatch.setattr(storage_service, "download_file", _download)
    monkeypatch.setattr(extraction.extract_file_text, "delay", lambda *a, **k: None)


@pytest_asyncio.fixture(scope="session")
async def mcp_runtime():
    """The endpoint's session-manager runtime, for the whole test session.

    The MCP server owns one task group and refuses a second `run()` on the same
    instance, so exactly one holder exists — as in the deployed app, where the
    FastAPI lifespan is that holder. It is a task of our own rather than the
    fixture's own frame because a cancel scope must be entered and left by the
    same task, and pytest-asyncio finalizes a session fixture in a different
    task than it set it up in."""
    started = asyncio.Event()
    stopping = asyncio.Event()

    async def hold():
        async with share_mcp.session_runtime():
            started.set()
            await stopping.wait()

    holder = asyncio.create_task(hold())
    await started.wait()
    try:
        yield
    finally:
        stopping.set()
        await holder


@pytest_asyncio.fixture
async def mcp_session(mcp_runtime):
    """Open a client session at an MCP URL, against the mounted endpoint."""

    @asynccontextmanager
    async def _open(url: str):
        async with httpx.AsyncClient(transport=ASGITransport(app=app)) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session

    return _open


async def _call(session: ClientSession, tool: str, **arguments) -> str:
    result = await session.call_tool(tool, arguments)
    assert not result.isError, result.content[0].text
    return result.content[0].text


async def _tools(session: ClientSession) -> list[str]:
    return [tool.name for tool in (await session.list_tools()).tools]


def _item_text(response) -> str:
    return response.content[0].text


async def _folder(client: AsyncClient, api_key: str, name: str) -> str:
    resp = await client.post("/api/v1/me/folders", json={"name": name}, headers=_auth(api_key))
    assert resp.status_code == 201
    return resp.json()["id"]


async def _page(client: AsyncClient, api_key: str, folder_id: str, name: str, content: str) -> str:
    resp = await client.post(
        "/api/v1/me/pages/new",
        json={"name": name, "content": content, "folder_id": folder_id},
        headers=_auth(api_key),
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _file(
    client: AsyncClient, api_key: str, folder_id: str, name: str, content: bytes
) -> str:
    resp = await client.post(
        "/api/v1/me/files",
        files={"file": (name, content, "text/plain")},
        data={"folder_id": folder_id},
        headers=_auth(api_key),
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _table(
    client: AsyncClient, api_key: str, folder_id: str, name: str, rows: list[dict]
) -> str:
    resp = await client.post(
        "/api/v1/me/tables",
        json={
            "name": name,
            "columns": [
                {"name": "host", "type": "text"},
                {"name": "port", "type": "number"},
            ],
            "folder_id": folder_id,
        },
        headers=_auth(api_key),
    )
    assert resp.status_code == 201
    table = resp.json()
    by_name = {column["name"]: column["id"] for column in table["columns"]}
    for row in rows:
        created = await client.post(
            f"/api/v1/me/tables/{table['id']}/rows",
            json={"data": {by_name[key]: value for key, value in row.items()}},
            headers=_auth(api_key),
        )
        assert created.status_code == 201
    return table["id"]


async def _publish(client: AsyncClient, api_key: str, folder_id: str, title: str) -> dict:
    resp = await client.post(
        "/api/v1/me/skills",
        json={"folder_id": folder_id, "title": title, "description": "shared for the test"},
        headers=_auth(api_key),
    )
    assert resp.status_code == 201
    return resp.json()


async def _set_general_access(client: AsyncClient, api_key: str, folder_id: str, level: str):
    return await client.patch(
        "/api/v1/share/general-access",
        json={"object_type": "folder", "object_id": folder_id, "public_permission": level},
        headers=_auth(api_key),
    )


async def _share_by_link(client: AsyncClient, api_key: str, folder_id: str, level: str):
    """Move the folder's link to `level` and report the URL the dialog is shown."""
    changed = await _set_general_access(client, api_key, folder_id, level)
    assert changed.status_code == 200
    return (await _share_state(client, api_key, folder_id))["mcp_url"]


async def _share_with(client: AsyncClient, api_key: str, folder_id: str, email: str):
    return await client.post(
        "/api/v1/share",
        json={"object_type": "folder", "object_id": folder_id, "email": email},
        headers=_auth(api_key),
    )


async def _list_shares(client: AsyncClient, api_key: str, folder_id: str):
    return await client.get(
        f"/api/v1/share?object_type=folder&object_id={folder_id}", headers=_auth(api_key)
    )


async def _share_state(client: AsyncClient, api_key: str, folder_id: str) -> dict:
    """The share dialog's state, which is where a folder's MCP URL is published."""
    return (await _list_shares(client, api_key, folder_id)).json()


async def _raw_post(client: AsyncClient, url: str) -> httpx.Response:
    """One initialize handshake, as an MCP client's first call looks on the wire."""
    return await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )


# --- Skill leg: the slug is the handle ---------------------------------------


@pytest.mark.asyncio
async def test_published_skill_serves_its_own_url(client: AsyncClient, mcp_session):
    api_key = await _register(client, "mcp_skill")
    folder_id = await _folder(client, api_key, "Release notes")
    await _page(client, api_key, folder_id, "Draft", "# Finding\n\nRoll back the migration.")
    skill = await _publish(client, api_key, folder_id, "Release notes")

    published = await client.get(f"/api/v1/skills/{skill['slug']}")
    assert published.status_code == 200
    mcp_url = published.json()["skill"]["mcp_url"]
    assert mcp_url == f"http://localhost:3457/api/v1/mcp/skills/{skill['slug']}"

    async with mcp_session(mcp_url) as session:
        assert await _tools(session) == ["list", "read"]
        listing = json.loads(await _call(session, "list"))
        assert listing["title"] == "Release notes"
        paths = {item["path"] for item in listing["items"]}
        assert {"SKILL.md", "Draft"} <= paths
        assert "Roll back the migration." in await _call(session, "read", path="Draft")


@pytest.mark.asyncio
async def test_skill_read_returns_the_exact_shared_skill_md(client: AsyncClient, mcp_session):
    api_key = await _register(client, "mcp_skillmd")
    folder_id = await _folder(client, api_key, "Runbook")
    await _page(
        client,
        api_key,
        folder_id,
        "SKILL.md",
        "---\nname: Runbook\ndescription: What to do at 3am\n---\n\nPage the on-call human.\n",
    )
    skill = await _publish(client, api_key, folder_id, "Runbook")

    async with mcp_session(skill["mcp_url"]) as session:
        body = await _call(session, "read", path="SKILL.md")
    assert "description: What to do at 3am" in body
    assert "Page the on-call human." in body


@pytest.mark.asyncio
async def test_unpublishing_kills_the_skill_endpoint(
    client: AsyncClient, mcp_session, stub_storage
):
    api_key = await _register(client, "mcp_unpublish")
    folder_id = await _folder(client, api_key, "Temporary")
    await _page(client, api_key, folder_id, "Notes", "still here")
    skill = await _publish(client, api_key, folder_id, "Temporary")

    async with mcp_session(skill["mcp_url"]) as session:
        assert "Notes" in {
            item["path"] for item in json.loads(await _call(session, "list"))["items"]
        }

    deleted = await client.delete(f"/api/v1/skills/{skill['id']}", headers=_auth(api_key))
    assert deleted.status_code == 204

    gone = await _raw_post(client, skill["mcp_url"])
    assert gone.status_code == 404
    assert "no longer shared" in gone.json()["detail"]

    # The SDK reports a dead endpoint as a transport failure rather than a
    # clean status, which is exactly what an agent's client shows its user: the
    # server it was configured with is gone. The 404 above is the wire truth.
    with pytest.raises(Exception):
        async with mcp_session(skill["mcp_url"]):
            pass


# --- Folder leg: an unguessable handle, revoked by the switches that exist ---


@pytest.mark.asyncio
async def test_general_access_share_hands_out_a_folder_url(
    client: AsyncClient, mcp_session, stub_storage
):
    api_key = await _register(client, "mcp_folder")
    folder_id = await _folder(client, api_key, "Field research")
    notes = b"verbatim bytes from the shared file"
    await _page(client, api_key, folder_id, "Interview", "# Q: pricing\n\nA: usage based.")
    await _file(client, api_key, folder_id, "notes.txt", notes)
    await _table(client, api_key, folder_id, "Hosts", [{"host": "db-1", "port": 5432}])

    mcp_url = await _share_by_link(client, api_key, folder_id, "read")
    assert mcp_url.startswith("http://localhost:3457/api/v1/mcp/folders/")
    assert folder_id not in mcp_url

    async with mcp_session(mcp_url) as session:
        assert await _tools(session) == ["list", "read"]
        listing = json.loads(await _call(session, "list"))
        assert listing["title"] == "Field research"
        assert listing["handle_kind"] == "folder"
        by_path = {item["path"]: item for item in listing["items"]}
        assert {"Interview", "notes.txt", "Hosts"} == set(by_path)
        assert by_path["notes.txt"]["size_bytes"] == len(notes)

        assert "usage based." in await _call(session, "read", path="Interview")
        assert (
            await _call(session, "read", path="notes.txt") == "verbatim bytes from the shared file"
        )
        table_text = await _call(session, "read", path="Hosts")
        assert "host | port" in table_text
        assert "db-1 | 5432" in table_text


@pytest.mark.asyncio
async def test_folder_url_survives_a_reopen_and_never_rotates(client: AsyncClient, stub_storage):
    api_key = await _register(client, "mcp_stable")
    folder_id = await _folder(client, api_key, "Stable handle")
    await _share_by_link(client, api_key, folder_id, "read")

    first = (await _share_state(client, api_key, folder_id))["mcp_url"]
    again = (await _share_state(client, api_key, folder_id))["mcp_url"]
    assert first == again

    await _share_with(client, api_key, folder_id, "teammate@example.com")
    assert (await _share_state(client, api_key, folder_id))["mcp_url"] == first


@pytest.mark.asyncio
async def test_private_folder_is_handed_no_url_and_no_row(client: AsyncClient, pool):
    api_key = await _register(client, "mcp_private")
    folder_id = await _folder(client, api_key, "Not shared at all")

    listing = await _list_shares(client, api_key, folder_id)
    assert listing.status_code == 200
    assert listing.json()["mcp_url"] is None
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM folder_mcp_tokens WHERE folder_id = $1", folder_id
        )
        == 0
    )


@pytest.mark.asyncio
async def test_downgrading_general_access_kills_the_endpoint(
    client: AsyncClient, mcp_session, stub_storage
):
    api_key = await _register(client, "mcp_downgrade")
    folder_id = await _folder(client, api_key, "Link only")
    await _page(client, api_key, folder_id, "Doc", "link-only content")
    mcp_url = await _share_by_link(client, api_key, folder_id, "read")

    async with mcp_session(mcp_url) as session:
        assert "Doc" in {item["path"] for item in json.loads(await _call(session, "list"))["items"]}

    assert await _share_by_link(client, api_key, folder_id, "none") is None

    assert (await _raw_post(client, mcp_url)).status_code == 404


@pytest.mark.asyncio
async def test_a_named_share_alone_earns_no_url_and_revives_no_handle(
    client: AsyncClient, mcp_session
):
    api_key = await _register(client, "mcp_named")
    folder_id = await _folder(client, api_key, "Link then named only")
    await _page(client, api_key, folder_id, "Doc", "named-only content")
    mcp_url = await _share_by_link(client, api_key, folder_id, "read")

    async with mcp_session(mcp_url) as session:
        assert "Doc" in {item["path"] for item in json.loads(await _call(session, "list"))["items"]}

    await _share_by_link(client, api_key, folder_id, "none")
    await _share_with(client, api_key, folder_id, "teammate@example.com")

    # Named people can read it; an MCP client presents no identity, so the
    # handle grants what a public link granted and nothing more.
    assert (await _share_state(client, api_key, folder_id))["mcp_url"] is None
    assert (await _raw_post(client, mcp_url)).status_code == 404


@pytest.mark.asyncio
async def test_removing_the_last_share_leaves_the_handle_dead(client: AsyncClient):
    api_key = await _register(client, "mcp_unshare_all")
    folder_id = await _folder(client, api_key, "Named list only")
    await _share_with(client, api_key, folder_id, "teammate@example.com")
    assert (await _share_state(client, api_key, folder_id))["mcp_url"] is None

    revoked = await client.request(
        "DELETE",
        "/api/v1/share/invite",
        json={"object_type": "folder", "object_id": folder_id, "email": "teammate@example.com"},
        headers=_auth(api_key),
    )
    assert revoked.status_code == 200
    assert (await _share_state(client, api_key, folder_id))["mcp_url"] is None


@pytest.mark.asyncio
async def test_publishing_shares_the_folder_too_so_both_urls_work(client: AsyncClient, mcp_session):
    api_key = await _register(client, "mcp_publish_folder")
    folder_id = await _folder(client, api_key, "Published source")
    await _page(client, api_key, folder_id, "Guide", "published through the skill")
    skill = await _publish(client, api_key, folder_id, "Published source")

    # A publish makes the folder publicly readable — that is how its Skill page
    # renders — so the folder dialog has a live URL to show as well.
    folder_url = (await _share_state(client, api_key, folder_id))["mcp_url"]
    assert folder_url.startswith("http://localhost:3457/api/v1/mcp/folders/")
    assert skill["mcp_url"].endswith(f"/mcp/skills/{skill['slug']}")

    for url in (folder_url, skill["mcp_url"]):
        async with mcp_session(url) as session:
            paths = {item["path"] for item in json.loads(await _call(session, "list"))["items"]}
        assert "Guide" in paths


# --- Unknown and malformed handles ------------------------------------------


@pytest.mark.asyncio
async def test_garbage_handle_is_404_not_a_session(client: AsyncClient):
    unknown = await _raw_post(client, "http://localhost:3457/api/v1/mcp/skills/no-such-skill")
    assert unknown.status_code == 404

    guessed_token = await _raw_post(
        client, "http://localhost:3457/api/v1/mcp/folders/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert guessed_token.status_code == 404

    wrong_family = await _raw_post(client, "http://localhost:3457/api/v1/mcp/pages/whatever")
    assert wrong_family.status_code == 404


@pytest.mark.asyncio
async def test_read_outside_the_share_reports_the_available_paths(
    client: AsyncClient, mcp_session, stub_storage
):
    api_key = await _register(client, "mcp_outside")
    folder_id = await _folder(client, api_key, "Small share")
    await _page(client, api_key, folder_id, "Only", "the one page")
    mcp_url = await _share_by_link(client, api_key, folder_id, "read")

    async with mcp_session(mcp_url) as session:
        result = await session.call_tool("read", {"path": "../../etc/passwd"})
        assert result.isError
        text = _item_text(result)
        assert "No item at" in text
        assert "Only" in text


# --- The read is audited like every other externally-visible read -----------


@pytest.mark.asyncio
async def test_mcp_reads_land_in_the_security_audit_trail(
    client: AsyncClient, mcp_session, pool, stub_storage
):
    api_key = await _register(client, "mcp_audit")
    folder_id = await _folder(client, api_key, "Audited")
    await _page(client, api_key, folder_id, "Secret", "audited body")
    owner_id = await pool.fetchval("SELECT owner_user_id FROM folders WHERE id = $1", folder_id)
    mcp_url = await _share_by_link(client, api_key, folder_id, "read")

    async with mcp_session(mcp_url) as session:
        await _call(session, "list")
        await _call(session, "read", path="Secret")

    rows = await pool.fetch(
        "SELECT action, via, actor_user_id, metadata FROM security_audit_events "
        "WHERE owner_user_id = $1 ORDER BY created_at",
        owner_id,
    )
    actions = [row["action"] for row in rows]
    assert "content.entries_listed" in actions
    read_rows = [row for row in rows if row["action"] == "content.page_read"]
    assert read_rows, f"no page-read row among {actions}"
    assert read_rows[-1]["via"] == "mcp"
    assert read_rows[-1]["actor_user_id"] is None
    assert json.loads(read_rows[-1]["metadata"])["path"] == "Secret"
