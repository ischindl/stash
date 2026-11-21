"""The executable version of docs/scope-contract-for-integrators.md.

Every test here pins one promise that document makes about `X-Stash-Scope`, so
the prose cannot drift from the API it describes: the documented provisioning
sequence keeps working, the error contract keeps its codes, the generated
schema keeps carrying the sentence integrators are told to rely on, and the
routes the document names keep existing under those paths. The one measured
finding that is deliberately NOT pinned green: a plain (non-creator) member can
today mint a `full` developer key on the workspace - that posture is undecided,
so this file stays silent about it.
"""

import uuid

import pytest
from httpx import AsyncClient

from backend.main import app

from .conftest import unique_name
from .test_developer_platform import _auth, _developer, _mint_workspace_key, _push
from .test_permissions import _register_with_email

# Every route docs/scope-contract-for-integrators.md names, as (method, path).
_DOC_ROUTES = {
    ("POST", "/api/v1/me/developer/activate"),
    ("POST", "/api/v1/me/developer/keys"),
    ("GET", "/api/v1/me/workspaces"),
    ("POST", "/api/v1/me/sessions/events"),
    ("POST", "/api/v1/me/sessions/events/batch"),
    ("GET", "/api/v1/me/sessions/events"),
    ("GET", "/api/v1/me/sessions/events/search"),
    ("GET", "/api/v1/me/sessions/events/semantic-search"),
    ("POST", "/api/v1/me/session-folders/get-or-create"),
}


def _event(session_id: str) -> dict:
    return {
        "agent_name": "contract-probe",
        "event_type": "user_message",
        "content": f"contract probe {session_id}",
        "session_id": session_id,
    }


@pytest.mark.asyncio
async def test_documented_provisioning_sequence_end_to_end(client: AsyncClient):
    """Doc §2 + §1: activate (no workspace_id) -> mint a full key on the header
    -> the key pushes with NO header -> the creator's own personal key reads
    the machine row back through the scope. The last step is what makes "the
    scope is a real read domain for the integrator's own key" checkable."""
    email = f"{unique_name('intg')}@example.com"
    personal_key, _ = await _register_with_email(client, email)
    resp = await client.post(
        "/api/v1/me/developer/activate",
        json={"name": unique_name("project")},
        headers=_auth(personal_key),
    )
    assert resp.status_code == 200, resp.text
    workspace = resp.json()
    scope_user_id = workspace["scope_user_id"]
    assert uuid.UUID(scope_user_id)
    assert workspace["external_wiki_folder_id"] is not None

    resp = await client.post(
        "/api/v1/me/developer/keys",
        json={"name": "contract-key", "access": "full"},
        headers={**_auth(personal_key), "X-Stash-Scope": scope_user_id},
    )
    assert resp.status_code == 200, resp.text
    machine_key = resp.json()["api_key"]

    # Scope-native key: it authenticates AS the scope, so it sends no header.
    sid = f"contract-e2e-{uuid.uuid4().hex[:8]}"
    await _push(client, machine_key, [_event(sid)])

    resp = await client.get(
        f"/api/v1/me/sessions/events?session_id={sid}",
        headers={**_auth(personal_key), "X-Stash-Scope": scope_user_id},
    )
    assert resp.status_code == 200, resp.text
    session_ids = [e["session_id"] for e in resp.json()["events"]]
    assert session_ids == [sid], (
        "the workspace scope must be a real server-side read domain: the "
        f"member's scoped read should see the machine key's row, got {session_ids}"
    )


@pytest.mark.asyncio
async def test_stranger_key_with_scope_header_is_403(client: AsyncClient):
    """Doc §1: 'A header naming a workspace the caller is not a member of is a
    hard 403, not a downgrade to personal.'"""
    _, _, workspace = await _developer(client)
    stranger_key, _ = await _register_with_email(client, f"{unique_name('strng')}@example.com")
    resp = await client.get(
        "/api/v1/me/sessions/events",
        headers={**_auth(stranger_key), "X-Stash-Scope": workspace["scope_user_id"]},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_malformed_scope_header_is_400(client: AsyncClient):
    """Doc §1 error contract: 'the header value is not a UUID' is a 400."""
    api_key, _ = await _register_with_email(client, f"{unique_name('badhdr')}@example.com")
    resp = await client.get(
        "/api/v1/me/sessions/events",
        headers={**_auth(api_key), "X-Stash-Scope": "not-a-uuid"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_personal_push_without_header_is_invisible_to_scoped_read(
    client: AsyncClient,
):
    """Doc §1/§4: absence of the header reaches only the caller's own scope,
    so a header-less personal row never appears under the workspace scope."""
    api_key, _, workspace = await _developer(client)
    sid = f"contract-personal-{uuid.uuid4().hex[:8]}"
    await _push(client, api_key, [_event(sid)])  # no header -> personal scope

    resp = await client.get(
        f"/api/v1/me/sessions/events?session_id={sid}",
        headers={**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["events"] == [], "a personal (header-less) row leaked into a scoped read"


def test_documented_routes_are_registered():
    """A route rename must fail this test loudly, not turn the integrator doc
    into fiction (same route-table shape as test_api_key_access)."""
    registered = {
        (method, route.path) for route in app.routes for method in getattr(route, "methods", ())
    }
    missing = _DOC_ROUTES - registered
    assert not missing, (
        f"routes named in docs/scope-contract-for-integrators.md are gone: {missing}"
    )


def test_openapi_scope_parameter_carries_the_contract_description():
    """Doc §1 promises the contract is readable from the API itself. The
    sentence lives in `Header(description=...)` on get_scope and reaches the
    generated schema as the x-stash-scope header parameter's schema
    description (measured shape) - deleting it silently must redden this."""
    params = app.openapi()["paths"]["/api/v1/me/sessions/events"]["get"]["parameters"]
    scope_params = [
        p for p in params if p.get("name") == "x-stash-scope" and p.get("in") == "header"
    ]
    assert scope_params, "the X-Stash-Scope header vanished from the generated OpenAPI schema"
    description = scope_params[0]["schema"]["description"]
    assert "scope_user_id" in description
    assert "403" in description
    assert "docs/scope-contract-for-integrators.md" in description, (
        "the OpenAPI description no longer points integrators at the contract doc"
    )


@pytest.mark.asyncio
async def test_read_key_may_push_events_but_not_resolve_folders(client: AsyncClient):
    """Pins the doc §2 sentence: "'read" key may push events ... but is
    refused (403) on POST /api/v1/me/session-folders/get-or-create", which is
    why the documented sequence requires access: "full"."""
    api_key, _, workspace = await _developer(client)
    read_key = await _mint_workspace_key(client, api_key, workspace)  # access: "read"

    sid = f"contract-readkey-{uuid.uuid4().hex[:8]}"
    await _push(client, read_key, [_event(sid)])  # allowed: events push is read-key contract

    resp = await client.post(
        "/api/v1/me/session-folders/get-or-create",
        json={"name": unique_name("folder"), "external_key": f"contract-{uuid.uuid4().hex[:8]}"},
        headers=_auth(read_key),
    )
    assert resp.status_code == 403, (
        "a read key resolved a folder; the contract doc then must stop "
        'requiring access: "full" for folder-resolving integrators'
    )
