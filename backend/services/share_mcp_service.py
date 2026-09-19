"""Share handles an MCP client dials, and what each one is allowed to read.

Two families of handle, one server. A Skill's handle is its slug: a name,
public by design, alive exactly as long as the publish record that names it. A
folder's handle is a secret: 256 bits minted once per folder and stored in
`folder_mcp_tokens`, because a folder is private by default and a URL that
names one cannot be guessable. Both families are read-only, and both die the
moment the share behind them dies — the slug when the skill is unpublished,
the token when the folder stops being publicly shared.

An MCP client authenticates nothing, so a handle grants exactly what a public
link grants and never more: a folder shared with named people only has no
working handle, whatever was minted for it in the past.

The authorization question is therefore identical to the public REST routes'
question, and it reuses the same answer: `permission_service.get_visibility`
says whether a folder is reachable without credentials (a published skill or a
general-access link) or with a named grant. A revoked handle is not "a session
that stops working" — it is an endpoint that stops existing, which is what an
MCP client can actually tell its user.
"""

import secrets
from uuid import UUID

import asyncpg

from backend.config import settings
from backend.database import get_pool
from backend.services import (
    permission_service,
    security_audit_service,
    shared_skill_service,
    storage_service,
)

MCP_PATH_PREFIX = "/api/v1/mcp"

MAX_LISTED_PATHS_IN_ERROR = 12


def _origin() -> str:
    """The origin the share dialog shows in its URLs — the product's public
    face, which proxies /api/v1 through to this backend."""
    return settings.PUBLIC_URL.rstrip("/")


def skill_mcp_url(slug: str) -> str:
    return f"{_origin()}{MCP_PATH_PREFIX}/skills/{slug}"


async def folder_mcp_url(folder_id: UUID, created_by: UUID) -> str:
    """The folder's MCP URL, minting its handle on first use.

    The handle is minted when an owner first asks to see it and never rotated:
    a client configured with it stays working, and rotating it would break the
    owner's own setup without being asked to."""
    pool = get_pool()
    token = await pool.fetchval(
        "SELECT token FROM folder_mcp_tokens WHERE folder_id = $1", folder_id
    )
    if not token:
        try:
            token = await pool.fetchval(
                "INSERT INTO folder_mcp_tokens (folder_id, token, created_by) "
                "VALUES ($1, $2, $3) RETURNING token",
                folder_id,
                secrets.token_urlsafe(32),
                created_by,
            )
        except asyncpg.UniqueViolationError:
            # Two share dialogs opened the same folder at once; the first
            # handle wins, the second caller shows the same URL.
            token = await pool.fetchval(
                "SELECT token FROM folder_mcp_tokens WHERE folder_id = $1", folder_id
            )
    return f"{_origin()}{MCP_PATH_PREFIX}/folders/{token}"


async def folder_share_url(folder_id: UUID, requested_by: UUID) -> str | None:
    """The folder's MCP URL while its share is public, otherwise None.

    Uses the gate's own predicate, so the share dialog can never advertise a
    URL that would 404 the moment a client dials it. An owner who has not
    shared the folder yet sees no handle until the share that earns it."""
    if await permission_service.get_visibility("folder", folder_id) != "public":
        return None
    return await folder_mcp_url(folder_id, requested_by)


async def resolve_skill_handle(slug: str) -> dict | None:
    """The subject behind a slug handle: the skill, or None if it is gone.

    Deliberately not `get_public_skill` — that one counts a page view, and an
    agent polling its own configured server is not a reader of the page."""
    row = await get_pool().fetchrow(
        "SELECT id, folder_id, slug, title, owner_user_id FROM skills WHERE slug = $1", slug
    )
    if not row:
        return None
    return {
        "kind": "skill",
        "handle": slug,
        "skill_id": row["id"],
        "folder_id": row["folder_id"],
        "title": row["title"],
        "owner_user_id": row["owner_user_id"],
    }


async def resolve_folder_handle(token: str) -> dict | None:
    """The subject behind a folder-token handle.

    The token row makes the handle *known*; the share state makes it *live*,
    and live means publicly shared: an MCP client presents no identity, so a
    handle may grant exactly what a public link grants and no more. A folder
    shared with named people only therefore has no working handle, even if one
    was minted while its link was open — downgrading either switch, or removing
    the last share, kills it. Both of the product's revoke switches kill it
    without either of them knowing this table exists."""
    row = await get_pool().fetchrow(
        "SELECT folder_id FROM folder_mcp_tokens WHERE token = $1", token
    )
    if not row:
        return None
    folder_id = row["folder_id"]
    if await permission_service.get_visibility("folder", folder_id) != "public":
        return None
    folder = await get_pool().fetchrow(
        "SELECT name, owner_user_id FROM folders WHERE id = $1", folder_id
    )
    if not folder:
        return None
    return {
        "kind": "folder",
        "handle": token,
        "skill_id": None,
        "folder_id": folder_id,
        "title": folder["name"],
        "owner_user_id": folder["owner_user_id"],
    }


def _paths(contents: dict) -> list[str]:
    paths = []
    for object_type in ("pages", "files", "tables"):
        for item in contents[object_type]:
            paths.append("/".join([*item["folder_path"], item["name"]]))
    return paths


async def list_items(subject: dict) -> dict:
    """Every item the handle grants, as `list` returns it."""
    contents = await shared_skill_service.folder_contents({"folder_id": subject["folder_id"]})
    await security_audit_service.record_entries_listed(
        target_type=subject["kind"],
        actor_user_id=None,
        owner_user_id=subject["owner_user_id"],
        metadata={"title": subject["title"]},
    )
    items = []
    for page in contents["pages"]:
        items.append(
            {
                "path": "/".join([*page["folder_path"], page["name"]]),
                "type": "page",
                "updated_at": page["updated_at"],
            }
        )
    for file in contents["files"]:
        items.append(
            {
                "path": "/".join([*file["folder_path"], file["name"]]),
                "type": "file",
                "content_type": file["content_type"],
                "size_bytes": file["size_bytes"],
            }
        )
    for table in contents["tables"]:
        items.append(
            {
                "path": "/".join([*table["folder_path"], table["name"]]),
                "type": "table",
                "columns": [column["name"] for column in table["columns"]],
                "row_count": len(table["rows"]),
            }
        )
    return {"title": subject["title"], "handle_kind": subject["kind"], "items": items}


def _find_item(contents: dict, path: str) -> tuple[str, dict] | None:
    normalized = path.strip("/")
    for object_type in ("pages", "files", "tables"):
        for item in contents[object_type]:
            if "/".join([*item["folder_path"], item["name"]]) == normalized:
                return object_type[:-1], item
    return None


def _table_to_text(table: dict) -> str:
    columns = table["columns"]
    names = [column["name"] for column in columns]
    lines = [" | ".join(names), " | ".join("---" for _ in names)]
    for row in table["rows"]:
        data = row["data"]
        # Row data is keyed by column id — what the product's own table view
        # and row embeddings read, not by column name.
        lines.append(
            " | ".join(
                "" if data.get(column["id"]) is None else str(data.get(column["id"]))
                for column in columns
            )
        )
    return "\n".join(lines)


async def _file_to_text(file: dict) -> str:
    row = await get_pool().fetchrow("SELECT storage_key FROM files WHERE id = $1", UUID(file["id"]))
    if not row:
        raise ValueError(f"File is gone: {file['name']}")
    content = await storage_service.download_file(row["storage_key"])
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        # The tool result is text; a binary's bytes are not. Its storage URL is
        # the same link the public Skill page hands out for it.
        return (
            f"(binary {file['content_type']}, {file['size_bytes']} bytes) — download: {file['url']}"
        )


async def read_item(subject: dict, path: str) -> str:
    """The exact shared content at `path`, audited as the read it is."""
    contents = await shared_skill_service.folder_contents({"folder_id": subject["folder_id"]})
    found = _find_item(contents, path)
    if not found:
        available = ", ".join(_paths(contents)[:MAX_LISTED_PATHS_IN_ERROR])
        raise ValueError(f"No item at '{path}'. Available: {available or '(none)'}")
    object_type, item = found

    if object_type == "page":
        text = shared_skill_service.page_text(item)
    elif object_type == "table":
        text = _table_to_text(item)
    else:
        text = await _file_to_text(item)

    await security_audit_service.record_content_read(
        target_type=object_type,
        target_id=str(item["id"]),
        actor_user_id=None,
        owner_user_id=subject["owner_user_id"],
        metadata={"path": path, "share": subject["title"], "share_kind": subject["kind"]},
    )
    return text
