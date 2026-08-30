"""Tools & Chat visibility is operator config, not a frontend hardcode.

`/api/v1/users/me` carries `show_tools_and_chat`, computed from the caller's
email domain against `TOOLS_AND_CHAT_DOMAINS` — so a self-hoster (or the
founder's dogfood account) can open the Agents chat rail without shipping a
new frontend bundle.
"""

import uuid

import pytest
from httpx import AsyncClient

from backend.config import settings


async def _register_with_email(client: AsyncClient, email: str) -> dict:
    resp = await client.post(
        "/api/v1/users/register",
        json={
            "name": f"tac{uuid.uuid4().hex[:10]}",
            "password": "securepassword1",
            "email": email,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return {"Authorization": f"Bearer {resp.json()['api_key']}"}


@pytest.mark.asyncio
async def test_me_flag_is_false_for_non_listed_domain(client: AsyncClient):
    headers = await _register_with_email(client, f"a{uuid.uuid4().hex[:8]}@example.com")
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.json()["show_tools_and_chat"] is False


@pytest.mark.asyncio
async def test_me_flag_true_for_default_and_env_domains(client: AsyncClient, monkeypatch):
    headers = await _register_with_email(client, f"boss{uuid.uuid4().hex[:6]}@progis.sk")
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.json()["show_tools_and_chat"] is False

    monkeypatch.setattr(settings, "TOOLS_AND_CHAT_DOMAINS", "progis.sk, FerganaLabs.com")
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.json()["show_tools_and_chat"] is True
