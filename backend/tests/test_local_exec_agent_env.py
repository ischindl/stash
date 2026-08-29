"""Local exec mode must leave the harness able to reach Stash.

An agent turn exec'd locally shells out to the `stash` CLI, so two things
have to hold in every local-exec deployment:

  - the image carries the CLI (a sprites box gets it from the seed; a
    containerized deployment has nothing seeding it), and
  - STASH_URL points somewhere the harness process can actually connect to
    — `localhost` is only the backend when they share a machine, so
    containerized splits (celery in its own container) need the knob.
"""

import uuid
from pathlib import Path

from backend import config
from backend.services import sprite_service

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_backend_image_ships_the_stash_cli():
    # Local exec mode runs the harness as a subprocess of THIS image; if the
    # CLI install step disappears, curator turns fail with "stash: command
    # not found" and only on containerized deployments.
    text = DOCKERFILE.read_text()
    assert "COPY cli/" in text
    assert "pip install --no-cache-dir ." in text


def test_stash_url_defaults_to_localhost_for_a_laptop_dev_mode():
    assert config.settings.LOCAL_STASH_API_URL == f"http://localhost:{config.settings.PORT}"


async def test_local_agent_env_uses_the_configured_url(monkeypatch):
    async def fake_create_api_key(user_id, *, name, key_type):
        return "sk-test"

    monkeypatch.setattr("backend.auth.create_api_key", fake_create_api_key)
    monkeypatch.setattr(config.settings, "LOCAL_STASH_API_URL", "http://backend:3456")

    env = await sprite_service.local_agent_env(uuid.uuid4())

    assert env == {"STASH_API_KEY": "sk-test", "STASH_URL": "http://backend:3456"}
