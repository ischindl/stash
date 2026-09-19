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
and no session affinity is needed behind the product's proxy. The handle is the
whole authorization story — the network layer adds nothing to it, which is why
this mount runs without a Host allowlist (_transport_security says why).

The server is built per app runtime rather than at import: a FastMCP instance
may run its session manager exactly once, and an app can be started more than
once inside one process (tests do this per test, a reloaded process does it per
reload). One endpoint object per runtime, resolved by the gate at request time,
keeps the second start from inheriting a spent manager.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.services import security_audit_service, share_mcp_service

_HANDLE: ContextVar[dict] = ContextVar("stash_share_handle")


def _transport_security() -> TransportSecuritySettings:
    """No Host allowlist here: the Host this mount is served on is never the printed one.

    A pasted MCP URL carries PUBLIC_URL's origin, and in every topology we ship a
    different Host reaches us. Managed prod serves /api/v1 through the Next.js rewrite
    to the API (live check: the app origin's /health is answered by this backend), and
    that proxy rewrites Host to its own target while writing the printed origin into
    X-Forwarded-Host (Next's proxy-request sets `changeOrigin: true`) — the endpoint is
    reached as the API host while the URL names the app origin. Self-host rewrites to
    BACKEND_INTERNAL_URL, so Host is `backend:3456`. An allowlist derived from
    PUBLIC_URL therefore answers 421 to the product's own URLs, before any handle is
    read (test_the_managed_proxy_host_reads_the_published_skill). Nothing in this
    backend's config names the hosts it is served on, so any list written here is a
    guess about someone's deployment, and a wrong guess is a dead URL. X-Forwarded-Host
    is no more honest: the edge in front of the API host writes its own value over ours.

    The Host check guards a surface that answers an attacker's browser using a
    credential the browser supplies. This one carries none — no cookie, no session, no
    Authorization header — so a rebound DNS name rides nothing. The grant is the handle
    in the path (unguessable for a shared folder, public by design for a published
    skill) and it is re-resolved through the share cascade on every request, so a
    revoked share is a 404 before a tool runs. Browsers stay gated by CORS_ORIGINS, and
    the MCP SDK requires application/json on POST regardless of this flag.
    """

    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


INSTRUCTIONS = (
    "These tools read one Stash share — a published Skill or a shared "
    "folder, whichever this URL was minted for. Read-only: call list to "
    "see what is there, then read a path from that list."
)


def _register_tools(server: FastMCP) -> None:
    # The names the founder froze are the ones the client shows, so they are
    # set explicitly here rather than inherited from these functions' names.

    @server.tool(name="list")
    async def list_shared_items() -> str:
        """List every page, file, and table in this share, with its path."""
        result = await share_mcp_service.list_items(_HANDLE.get())
        return json.dumps(result, indent=2, default=str)

    @server.tool(name="read")
    async def read_shared_item(path: str) -> str:
        """Read one item's content by the path `list` reported."""
        return await share_mcp_service.read_item(_HANDLE.get(), path)


class ShareEndpoint:
    """One MCP server, and the one run its session manager is allowed."""

    def __init__(self) -> None:
        self.server = FastMCP(
            "Stash share",
            instructions=INSTRUCTIONS,
            streamable_http_path="/",
            stateless_http=True,
            json_response=True,
            transport_security=_transport_security(),
        )
        _register_tools(self.server)
        self.app: ASGIApp = self.server.streamable_http_app()


_active: ShareEndpoint | None = None


@asynccontextmanager
async def session_runtime():
    """Owns the MCP session manager for as long as this app runtime lives.

    The manager is a one-shot anyio task group, and a task group only lets go
    in the task that picked it up. Whoever drives our lifespan is not
    guaranteed to do that: a caller that resumes startup and shutdown from
    different tasks — which is how pytest-asyncio runs an async fixture — gets
    'exit cancel scope in a different task'. So the manager is handed to a
    task of our own, and the lifespan only ever waits on that task.

    One runtime answers the endpoint at a time, and a runtime hands the
    endpoint back to whoever held it when it exits. A test session that boots
    the app itself and then drives the app's own lifespan must not strand the
    endpoint on the first runtime.
    """
    global _active
    held = _active
    endpoint = ShareEndpoint()
    _active = endpoint
    started = asyncio.Event()
    stopping = asyncio.Event()

    async def own_the_session_manager() -> None:
        async with endpoint.server.session_manager.run():
            started.set()
            await stopping.wait()

    owner = asyncio.create_task(own_the_session_manager())
    try:
        await started.wait()
        yield
    finally:
        stopping.set()
        await owner
        _active = held


class ShareHandleGate:
    """Resolves the share handle in the URL, then hands off to the MCP server."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Mounted here for HTTP alone; the app's own lifespan never arrives.
            return

        endpoint = _active
        if endpoint is None:
            await JSONResponse(
                status_code=503,
                content={"detail": "The Stash MCP endpoint is not running."},
            )(scope, receive, send)
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
            await endpoint.app(inner_scope, receive, send)
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


gate = ShareHandleGate()
