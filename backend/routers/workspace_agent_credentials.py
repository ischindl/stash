"""Connect / list / disconnect the local endpoint credential the workspace's agents run on.

Workspace agents (the developer-wiki curator above all, and every agent that
runs under the workspace scope) execute under the workspace's scope account —
a login-less user row that can never connect a credential for itself. So the
operator connects from the Developer Platform console: the console sends
X-Stash-Scope, and the credential is stored on the scope account, where
agent_auth's auto-resolution already makes the workspace's agents run on PI
against that endpoint.

Only the local endpoint is connectable here (a login-less account has no
OAuth flow), and the shape is the personal local flow's: base URL + model,
key optional.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_scope
from ..services import agent_auth, agent_service
from .developer import _require_active_workspace

router = APIRouter(
    prefix="/api/v1/me/developer/agent-credentials", tags=["developer-agent-credentials"]
)


class ConnectLocalRequest(BaseModel):
    base_url: str  # OpenAI-compatible endpoint your cloud computer can reach
    model: str  # the model id on that endpoint
    api_key: str | None = None  # keyless endpoints are common


@router.get("")
async def list_credentials(scope_user_id: UUID = Depends(get_scope)):
    """The local endpoints the workspace's agents can run on (never returns any
    secret): the same endpoint entries the personal Settings list shows."""
    await _require_active_workspace(scope_user_id)
    return {
        "connected": await agent_auth.list_connected(scope_user_id),
        "endpoints": await agent_auth.list_local_endpoints(scope_user_id),
    }


@router.post("")
async def connect_local(req: ConnectLocalRequest, scope_user_id: UUID = Depends(get_scope)):
    """Connect a box for the workspace's agents. APPENDS like the personal
    connect, and probes first for the same reason: a dead box must never enter
    the list the console pins curators against."""
    await _require_active_workspace(scope_user_id)
    try:
        doc = agent_auth.local_endpoint_doc(req.base_url, req.model, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    probe = await agent_auth.probe_local_endpoint(doc["base_url"], doc["api_key"])
    if not probe["ok"]:
        raise HTTPException(
            status_code=400, detail=f"endpoint probe failed: {probe['error_detail']}"
        )
    credential_id = await agent_auth.store_credential(
        scope_user_id,
        "local",
        "endpoint",
        agent_auth.local_endpoint_secret(doc["base_url"], doc["model"], doc["api_key"]),
        name=agent_auth.endpoint_name(doc["base_url"]),
    )
    return {
        "ok": True,
        "id": str(credential_id),
        "connected": await agent_auth.list_connected(scope_user_id),
    }


@router.delete("/endpoints/{credential_id}")
async def disconnect_endpoint(credential_id: UUID, scope_user_id: UUID = Depends(get_scope)):
    """Disconnect one of the workspace's boxes, refused (409) while a workspace
    agent still pins it — the personal route's guard, mirrored."""
    await _require_active_workspace(scope_user_id)
    refs = await agent_service.disconnect_local_endpoint(scope_user_id, credential_id)
    if refs:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "agents still pin this endpoint — re-pin or unpin them first",
                "agents": refs,
            },
        )
    return {"ok": True, "connected": await agent_auth.list_connected(scope_user_id)}
