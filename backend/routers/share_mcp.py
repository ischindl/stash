"""The MCP server behind a share URL, and the HTTP gate in front of it.

One server answers both handle families (`/api/v1/mcp/skills/<slug>` and
`/api/v1/mcp/folders/<token>`) because the tools are identical: what an agent
may do is decided by the handle in the URL, not by which route it came in on.
The handle is resolved at the HTTP layer — before the JSON-RPC ever reaches a
tool — so a handle whose share is dead is a 404 endpoint, the one signal every
MCP client reports to its user. A tool that quietly returned nothing would
leave the owner believing their agent was still reading the folder.

Requests are stateless by design: each one re-resolves its own handle, so
revocation lands on the next call rather than at some future session expiry,
and no session affinity is needed behind the product's proxy.
"""

import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.config import settings
from backend.services import security_audit_service, share_mcp_service

_HANDLE: ContextVar[dict] = ContextVar("stash_share_handle")


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection scoped to the origin the product prints.

    Every MCP URL this backend hands out is built from PUBLIC_URL, so a client
    configured from the share dialog arrives with exactly that Host. Anything
    else is a different name resolving to us, which is the rebinding attack
    this check exists for."""
    origin = urlparse(settings.PUBLIC_URL.rstrip("/"))
    host = origin.netloc
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{origin.hostname}:*"],
        allowed_origins=[f"{origin.scheme}://{host}", f"{origin.scheme}://{origin.hostname}:*"],
    )


server = FastMCP(
    "Stash share",
    instructions=(
        "These tools read one Stash share — a published Skill or a shared "
        "folder, whichever this URL was minted for. Read-only: call list to "
        "see what is there, then read a path from that list."
    ),
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security(),
)


# The names the founder froze are the ones the client shows, so they are set
# explicitly here rather than inherited from these functions' own names.
@server.tool(name="list")
async def list_shared_items() -> str:
    """List every page, file, and table in this share, with its path."""
    result = await share_mcp_service.list_items(_HANDLE.get())
    return json.dumps(result, indent=2, default=str)


@server.tool(name="read")
async def read_shared_item(path: str) -> str:
    """Read one item's content by the path `list` reported."""
    return await share_mcp_service.read_item(_HANDLE.get(), path)


asgi_app: ASGIApp = server.streamable_http_app()


@asynccontextmanager
async def session_runtime():
    """Owns the MCP session manager for the process lifetime."""
    async with server.session_manager.run():
        yield


class ShareHandleGate:
    """Resolves the share handle in the URL, then hands off to the MCP server."""

    def __init__(self, inner: ASGIApp):
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return

        subject = await self._resolve(scope)
        if subject is None:
            await self._reject(scope, receive, send)
            return

        inner_scope = dict(scope)
        inner_scope["path"] = "/"
        inner_scope["raw_path"] = b"/"
        handle_token = _HANDLE.set(subject)
        # Same audit column the auth layer stamps 'web'/'cli' into, so the
        # owner's security trail says which surface their content left by.
        via_token = security_audit_service.request_via.set("mcp")
        try:
            await self.inner(inner_scope, receive, send)
        finally:
            security_audit_service.request_via.reset(via_token)
            _HANDLE.reset(handle_token)

    async def _resolve(self, scope: Scope) -> dict | None:
        root_path = scope.get("root_path") or ""
        segments = [part for part in scope["path"][len(root_path) :].split("/") if part]
        if len(segments) != 2:
            return None
        family, handle = segments
        if family == "skills":
            return await share_mcp_service.resolve_skill_handle(handle)
        if family == "folders":
            return await share_mcp_service.resolve_folder_handle(handle)
        return None

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        """A dead or unknown handle: the endpoint does not exist.

        Not 401 — there is no credential to offer that would work here, and an
        authentication prompt invites the client to keep knocking."""
        await JSONResponse(
            status_code=404,
            content={"detail": "This Stash share URL is no longer shared."},
        )(scope, receive, send)


gate = ShareHandleGate(asgi_app)
