"""Connect / list / disconnect the model credential the cloud agent runs on.

A user pastes an API key for Claude (anthropic), Codex (openai), or
OpenRouter, or connects their own OpenAI-compatible local model (base URL +
model, key optional), and the agent runs their harness with it. OAuth connect
flows are separate.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user
from ..services import agent_auth, agent_oauth, agent_service

router = APIRouter(prefix="/api/v1/me/agent-credentials", tags=["agent-credentials"])

_PROVIDERS = {"anthropic", "openai", "openrouter", "local"}


class ConnectRequest(BaseModel):
    provider: str
    api_key: str | None = None  # required for the three key providers; optional for local
    base_url: str | None = (
        None  # local only: OpenAI-compatible endpoint your cloud computer can reach
    )
    model: str | None = None  # local only: the model id on that endpoint


class OAuthStartRequest(BaseModel):
    provider: str  # 'anthropic' (Claude) or 'openai' (Codex)


class OAuthFinishRequest(BaseModel):
    provider: str
    code: str  # the code (or code#state, or full redirect URL) the user pasted
    state: str


class ModelsJsonRequest(BaseModel):
    models_json: str  # the user's pi models.json, stored verbatim


class LocalTestRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


@router.get("")
async def list_credentials(current_user: dict = Depends(get_current_user)):
    """Which providers this user has connected, the endpoint entries for the
    Settings list (id, name, base URL, models probed live — never a key), and
    the user's own local endpoint doc for the Settings form to show and
    prefill. The key providers' secrets are never returned."""
    return {
        "connected": await agent_auth.list_connected(current_user["id"]),
        "endpoints": await agent_auth.list_local_endpoints(current_user["id"]),
        "local": await agent_auth.local_credential(current_user["id"]),
    }


@router.post("")
async def connect(req: ConnectRequest, current_user: dict = Depends(get_current_user)):
    if req.provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider: {req.provider}")
    if req.provider == "local":
        # The credential is an endpoint, not a key: an absolute http(s) base URL
        # the sprite dials at turn time plus a model id. Connecting APPENDS a
        # box — but only a live one: the probe runs before anything is stored,
        # so a typo'd URL never enters the list the user trusts.
        try:
            doc = agent_auth.local_endpoint_doc(req.base_url or "", req.model or "", req.api_key)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        probe = await agent_auth.probe_local_endpoint(doc["base_url"], doc["api_key"])
        if not probe["ok"]:
            raise HTTPException(
                status_code=400, detail=f"endpoint probe failed: {probe['error_detail']}"
            )
        credential_id = await agent_auth.store_credential(
            current_user["id"],
            "local",
            "endpoint",
            agent_auth.local_endpoint_secret(doc["base_url"], doc["model"], doc["api_key"]),
            name=agent_auth.endpoint_name(doc["base_url"]),
        )
        return {
            "ok": True,
            "id": str(credential_id),
            "connected": await agent_auth.list_connected(current_user["id"]),
        }
    if not req.api_key or not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key is required")
    await agent_auth.store_credential(
        current_user["id"], req.provider, "api_key", req.api_key.strip(), name=req.provider
    )
    return {"ok": True, "connected": await agent_auth.list_connected(current_user["id"])}


@router.post("/local/test")
async def test_local_endpoint(
    req: LocalTestRequest, current_user: dict = Depends(get_current_user)
):
    """Dial the endpoint from here and hand back what it answered, so a key can be
    verified before it is stored — the turn-time preflight on the user's cloud
    computer can only prove reachability, one turn too late. Tests exactly the
    values sent: this route never reads or writes a stored credential.

    Dialing a user-supplied URL from the server is a deliberate, bounded SSRF
    trade-off. It is bounded rather than filtered because the primary use case is
    a self-hosted or tunnelled endpoint — the help text's own example is
    http://your-host:11434/v1 — so an IP/hostname blocklist would break the
    feature. The bounds: absolute http(s) only (same check as connect), no
    redirect following, one request under the probe's 5s cap, a capped error
    body, and an authenticated caller who can only reach what their own agent
    would dial anyway.
    """
    try:
        doc = agent_auth.local_endpoint_doc(req.base_url, req.model, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await agent_auth.probe_local_endpoint(doc["base_url"], doc["api_key"])


@router.post("/oauth/start")
async def oauth_start(req: OAuthStartRequest, current_user: dict = Depends(get_current_user)):
    """Begin a Claude/Codex OAuth connect. The frontend opens authorize_url in a
    popup; the user approves and pastes the code the provider displays."""
    return agent_oauth.start(current_user["id"], req.provider)


@router.post("/oauth/finish")
async def oauth_finish(req: OAuthFinishRequest, current_user: dict = Depends(get_current_user)):
    """Exchange the pasted code and store the OAuth credential."""
    await agent_oauth.finish(current_user["id"], req.provider, req.code, req.state)
    return {"ok": True, "connected": await agent_auth.list_connected(current_user["id"])}


@router.get("/local/models-json")
async def get_local_models_json(current_user: dict = Depends(get_current_user)):
    """The effective pi models.json for the connected local endpoint: the
    user's stored override, or the synthesized default for the connect doc."""
    try:
        return await agent_auth.get_local_models_json(current_user["id"])
    except LookupError:
        raise HTTPException(status_code=404, detail="local endpoint is not connected")


@router.put("/local/models-json")
async def put_local_models_json(
    req: ModelsJsonRequest, current_user: dict = Depends(get_current_user)
):
    """Store the user's models.json verbatim. Validation is parse-don't-
    validate (parses to an object with a top-level "providers" object), a loud
    400 otherwise — pi is the rest of the validator."""
    try:
        await agent_auth.save_local_models_json(current_user["id"], req.models_json)
    except LookupError:
        raise HTTPException(status_code=404, detail="local endpoint is not connected")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "stored": True}


@router.delete("/local/models-json")
async def delete_local_models_json(current_user: dict = Depends(get_current_user)):
    """Delete the stored override; the synthesized default returns."""
    try:
        await agent_auth.reset_local_models_json(current_user["id"])
    except LookupError:
        raise HTTPException(status_code=404, detail="local endpoint is not connected")
    return {"ok": True, "stored": False}


@router.delete("/endpoints/{credential_id}")
async def disconnect_endpoint(credential_id: UUID, current_user: dict = Depends(get_current_user)):
    """Disconnect exactly one local endpoint. Refused (409, listing the agents)
    while any of the user's agents pins it — deleting the box under a running
    curator would strand that agent mid-map."""
    refs = await agent_service.disconnect_local_endpoint(current_user["id"], credential_id)
    if refs:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "agents still pin this endpoint — re-pin or unpin them first",
                "agents": refs,
            },
        )
    return {"ok": True, "connected": await agent_auth.list_connected(current_user["id"])}


@router.delete("/{provider}")
async def disconnect(provider: str, current_user: dict = Depends(get_current_user)):
    if provider == "local":
        raise HTTPException(
            status_code=400,
            detail="a local endpoint is disconnected by id: DELETE "
            "/api/v1/me/agent-credentials/endpoints/{credential_id}",
        )
    await agent_auth.delete_credential(current_user["id"], provider)
    return {"ok": True, "connected": await agent_auth.list_connected(current_user["id"])}
