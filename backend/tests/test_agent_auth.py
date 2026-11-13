"""Per-user harness + credential resolution: BYO keys, managed OpenRouter, gate."""

import json
import uuid
from pathlib import Path

import pytest

from backend.config import settings
from backend.services import agent_auth, billing_service, sprite_service
from backend.services import harness as h


@pytest.mark.asyncio
async def test_local_mode_without_local_credential_raises_needs_auth(monkeypatch):
    """Local exec mode with nothing connected must fail loud. The silent
    machine-CLAUDE fallback died mid-stream on boxes without a claude binary
    and masked the missing credential row (STAS-131); the clean contract is
    NeedsAuth → the 402 "connect a local model"."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")

    async def no_cred(user_id, provider=None):
        assert provider == "local"
        return None

    monkeypatch.setattr(agent_auth, "_get_credential", no_cred)
    with pytest.raises(agent_auth.NeedsAuth):
        await agent_auth.resolve(uuid.uuid4())


@pytest.mark.asyncio
async def test_byo_anthropic_key_runs_claude(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {"provider": "anthropic", "kind": "api_key", "secret": "sk-ant-mine"}

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.CLAUDE
    assert auth.env == {"ANTHROPIC_API_KEY": "sk-ant-mine"} and auth.files == {}


@pytest.mark.asyncio
async def test_byo_openai_key_runs_codex(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {"provider": "openai", "kind": "api_key", "secret": "sk-openai"}

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.CODEX and auth.env == {"OPENAI_API_KEY": "sk-openai"}


@pytest.mark.asyncio
async def test_byo_openrouter_key_runs_opencode(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {"provider": "openrouter", "kind": "api_key", "secret": "sk-or"}

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.OPENCODE and auth.env == {"OPENROUTER_API_KEY": "sk-or"}


@pytest.mark.asyncio
async def test_byo_claude_oauth_writes_credentials_file(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {"provider": "anthropic", "kind": "oauth", "secret": '{"claudeAiOauth":{}}'}

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.CLAUDE
    assert auth.env["CLAUDE_CONFIG_DIR"] == "/home/sprite/.claude"
    assert "/home/sprite/.claude/.credentials.json" in auth.files


@pytest.mark.asyncio
async def test_no_credential_pro_gets_managed_openrouter_glm(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-managed-or")

    async def no_cred(_uid):
        return None

    async def is_pro(_uid):
        return True

    monkeypatch.setattr(agent_auth, "_get_credential", no_cred)
    monkeypatch.setattr(billing_service, "is_pro", is_pro)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.OPENCODE
    assert auth.harness.default_model == "z-ai/glm-5.2"
    assert auth.env == {"OPENROUTER_API_KEY": "sk-managed-or"}
    # The customer-facing no-training guarantee: every managed turn must pin
    # OpenRouter routing to providers that don't retain or train on prompts.
    opencode_config = json.loads(auth.files["/home/sprite/.config/opencode/opencode.json"])
    model_options = opencode_config["provider"]["openrouter"]["models"]["z-ai/glm-5.2"]["options"]
    assert model_options["provider"] == {"data_collection": "deny"}


@pytest.mark.asyncio
async def test_no_credential_free_is_gated(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def no_cred(_uid):
        return None

    async def not_pro(_uid):
        return False

    monkeypatch.setattr(agent_auth, "_get_credential", no_cred)
    monkeypatch.setattr(billing_service, "is_pro", not_pro)
    with pytest.raises(agent_auth.NeedsAuth):
        await agent_auth.resolve(uuid.uuid4())


@pytest.mark.asyncio
async def test_managed_but_no_openrouter_key(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)

    async def no_cred(_uid):
        return None

    async def is_pro(_uid):
        return True

    monkeypatch.setattr(agent_auth, "_get_credential", no_cred)
    monkeypatch.setattr(billing_service, "is_pro", is_pro)
    with pytest.raises(agent_auth.ProviderNotConfigured):
        await agent_auth.resolve(uuid.uuid4())


def test_openrouter_rejects_oauth():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.get_event_loop().run_until_complete(
            agent_auth.store_credential(uuid.uuid4(), "openrouter", "oauth", "x", name="openrouter")
        )


@pytest.mark.asyncio
async def test_endpoint_kind_rejected_for_non_local_provider():
    with pytest.raises(ValueError):
        await agent_auth.store_credential(
            uuid.uuid4(), "openrouter", "endpoint", "http://x", name="x"
        )


@pytest.mark.asyncio
async def test_local_endpoint_with_key_runs_pi(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {
                    "base_url": "http://tunnel.example/v1",
                    "model": "llama3.1:8b",
                    "api_key": "my-secret-key",
                    "thinking": "off",
                }
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.PI
    assert auth.env == {
        "PI_OFFLINE": "1",
        "HOME": "/home/sprite",
        "STASH_LOCAL_KEY": "my-secret-key",
    }
    assert auth.endpoint == "http://tunnel.example/v1"
    assert auth.model == "llama3.1:8b"

    models = json.loads(auth.files["/home/sprite/.pi/agent/models.json"])
    local = models["providers"]["local"]
    # The key rides ONLY via env interpolation — never inline in the file.
    assert local["apiKey"] == "$STASH_LOCAL_KEY"
    assert "my-secret-key" not in json.dumps(models)
    assert local["api"] == "openai-completions"
    assert local["baseUrl"] == "http://tunnel.example/v1"
    assert local["compat"] == {"supportsDeveloperRole": False, "supportsReasoningEffort": False}
    assert local["models"] == [{"id": "llama3.1:8b", "contextWindow": 131072, "maxTokens": 8192}]


# Non-default formatting on purpose: 4-space indent, reordered keys, a
# second model, custom contextWindow/maxTokens — proves the stored override
# is written byte-for-byte, never re-serialized.
OVERRIDE_MODELS_JSON = """{
    "providers": {
        "local": {
            "api": "openai-completions",
            "baseUrl": "http://tunnel.example/v1",
            "apiKey": "$STASH_LOCAL_KEY",
            "models": [
                {"id": "llama3.1:8b", "maxTokens": 4096, "contextWindow": 32768},
                {"id": "qwen2:7b", "maxTokens": 8192, "contextWindow": 65536}
            ],
            "compat": {"supportsDeveloperRole": false, "supportsReasoningEffort": false}
        }
    }
}
"""


@pytest.mark.asyncio
async def test_local_override_written_verbatim(monkeypatch):
    """A stored models.json override is written into the box home byte-for-byte
    (no re-serialization); endpoint/model still come from the connect doc, and
    the key still rides only via STASH_LOCAL_KEY env interpolation."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {
                    "base_url": "http://tunnel.example/v1",
                    "model": "llama3.1:8b",
                    "api_key": "my-secret-key",
                    "thinking": "off",
                }
            ),
            "models_json": OVERRIDE_MODELS_JSON,
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.PI
    # The exact stored text — 4-space indent and reordered keys survive.
    assert auth.files["/home/sprite/.pi/agent/models.json"] == OVERRIDE_MODELS_JSON
    # Env + endpoint + model stay doc-based; the override's model list does not
    # leak into RunAuth.
    assert auth.env == {
        "PI_OFFLINE": "1",
        "HOME": "/home/sprite",
        "STASH_LOCAL_KEY": "my-secret-key",
    }
    assert auth.endpoint == "http://tunnel.example/v1"
    assert auth.model == "llama3.1:8b"


@pytest.mark.asyncio
async def test_local_override_keyless_no_key_env(monkeypatch):
    """Keyless endpoint + override: no STASH_LOCAL_KEY env (the user's own
    apiKey value in the override rides in the file, exactly as stored)."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {"base_url": "http://host:11434/v1", "model": "qwen2:7b", "thinking": "off"}
            ),
            "models_json": OVERRIDE_MODELS_JSON,
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert "STASH_LOCAL_KEY" not in auth.env
    assert auth.env == {"PI_OFFLINE": "1", "HOME": "/home/sprite"}
    assert auth.files["/home/sprite/.pi/agent/models.json"] == OVERRIDE_MODELS_JSON


@pytest.mark.asyncio
async def test_local_endpoint_keyless_has_no_key_env(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(_uid):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {"base_url": "http://host:11434/v1", "model": "qwen2:7b", "thinking": "off"}
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.PI
    assert "STASH_LOCAL_KEY" not in auth.env
    assert auth.env == {"PI_OFFLINE": "1", "HOME": "/home/sprite"}
    models = json.loads(auth.files["/home/sprite/.pi/agent/models.json"])
    # Keyless endpoints get the literal dummy so the file shape is one codepath.
    assert models["providers"]["local"]["apiKey"] == "local"


def test_local_auth_missing_field_fails_loud():
    with pytest.raises(ValueError):
        agent_auth._local_auth(
            {"provider": "local", "kind": "endpoint", "secret": json.dumps({"model": "m"})}
        )
    with pytest.raises(ValueError):
        agent_auth._local_auth(
            {
                "provider": "local",
                "kind": "endpoint",
                "secret": json.dumps({"base_url": "http://x"}),
            }
        )


@pytest.mark.asyncio
async def test_prefer_local_with_local_credential_runs_pi(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def cred(user_id, provider=None):
        assert provider == "local"
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps({"base_url": "http://x/v1", "model": "m", "thinking": "off"}),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4(), "local")
    assert auth.harness is h.PI and auth.model == "m"


@pytest.mark.asyncio
async def test_prefer_local_without_local_credential_fails_loud(monkeypatch):
    """Picking the local model with only OpenRouter connected must NOT silently
    run opencode — fail loud (no cross-provider fallback)."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def only_openrouter(user_id, provider=None):
        if provider == "local":
            return None
        return {"provider": "openrouter", "kind": "api_key", "secret": "sk-or"}

    monkeypatch.setattr(agent_auth, "_get_credential", only_openrouter)
    with pytest.raises(agent_auth.NeedsAuth):
        await agent_auth.resolve(uuid.uuid4(), "local")


@pytest.mark.asyncio
async def test_prefer_unconnected_model_fails_loud(monkeypatch):
    """An agent that picks Claude when only OpenAI is connected must NOT silently
    run Codex — it fails loud (no-fallback rule)."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")

    async def only_openai(user_id, provider=None):
        if provider == "anthropic":
            return None
        return {"provider": "openai", "kind": "api_key", "secret": "sk-openai"}

    monkeypatch.setattr(agent_auth, "_get_credential", only_openai)
    with pytest.raises(agent_auth.NeedsAuth):
        await agent_auth.resolve(uuid.uuid4(), "anthropic")


@pytest.mark.asyncio
async def test_local_mode_local_credential_runs_pi(monkeypatch):
    """Local exec mode (self-hosters, no sprites): a connected local endpoint
    still runs on this machine's pi — the credential is self-contained — and
    the turn is isolated on the simulated box's home, not the developer's
    machine pi config."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")
    monkeypatch.setattr(sprite_service, "local_box_home", lambda: Path("/tmp/fake-box-home"))

    async def cred(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1:8b", "thinking": "off"}
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.harness is h.PI
    assert auth.endpoint == "http://127.0.0.1:11434/v1"
    assert auth.model == "llama3.1:8b"
    assert auth.env["HOME"] == "/tmp/fake-box-home"
    assert auth.env["PI_OFFLINE"] == "1"
    assert "/tmp/fake-box-home/.pi/agent/models.json" in auth.files


@pytest.mark.asyncio
async def test_local_mode_override_writes_verbatim_to_box_home(monkeypatch):
    """Local exec mode (self-hosters, no sprites) must honor the stored
    override the same way sprites mode does — written verbatim to the
    simulated box home."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")
    monkeypatch.setattr(sprite_service, "local_box_home", lambda: Path("/tmp/fake-box-home"))

    async def cred(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1:8b", "thinking": "off"}
            ),
            "models_json": OVERRIDE_MODELS_JSON,
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    assert auth.files["/tmp/fake-box-home/.pi/agent/models.json"] == OVERRIDE_MODELS_JSON
    assert auth.model == "llama3.1:8b"  # still the connect doc's model


@pytest.mark.asyncio
async def test_local_mode_prefer_local_with_credential_runs_pi(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")

    async def cred(user_id, provider=None):
        assert provider == "local"
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps({"base_url": "http://x/v1", "model": "m", "thinking": "off"}),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4(), "local")
    assert auth.harness is h.PI and auth.model == "m"


@pytest.mark.asyncio
async def test_local_mode_other_pin_keeps_machine_login(monkeypatch):
    """An agent pinned to another provider in local exec mode keeps the
    machine-login default — the local endpoint credential is not a
    cross-provider fallback."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "local")

    async def local_only(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps({"base_url": "http://x/v1", "model": "m", "thinking": "off"}),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", local_only)
    auth = await agent_auth.resolve(uuid.uuid4(), "anthropic")
    assert auth.harness is h.CLAUDE and auth.env == {} and auth.files == {}


@pytest.mark.asyncio
async def test_local_credential_returns_the_stored_doc(monkeypatch):
    """The Settings form reads base URL, model, and the user's own key out of
    this doc — it is the one place the local row's secret is handed back."""

    async def cred(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {"base_url": "http://x/v1", "model": "m", "api_key": "sk-local", "thinking": "off"}
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    assert await agent_auth.local_credential(uuid.uuid4()) == {
        "base_url": "http://x/v1",
        "model": "m",
        "api_key": "sk-local",
        "thinking": "off",
    }


# --- The stored level reaches pi: the wire consequence, both directions ---
#
# The intake's failure is a turn that burns its whole output on reasoning while the
# screen says nothing was asked for. A test asserting only that a dict grew would
# stay green while pi sent nothing, so these assert what pi RECEIVES: the argv it is
# started with, and the provider file it reads its `reasoning` marker and its budget
# field out of. The negative half is asserted with the same instruments, because a
# knob that could not be turned off would be the same bug wearing a different label.


def _turn_shape(auth) -> tuple:
    """What a turn hands pi, as pi sees it: the argv it is started with, and the
    provider file the turn writes for it. Both are taken from what leaves the
    service — the argv list and the bytes destined for `.pi/agent/models.json` —
    rather than from an intermediate object, so a projection that stops short of the
    child process cannot pass this."""
    argv = h.build_argv(
        h.PI,
        "Say when.",
        session_key="studio",
        resume=False,
        system_prompt="You are a curator.",
        model=auth.model,
        thinking=auth.thinking,
    )
    provider_files = [
        content for path, content in auth.files.items() if path.endswith(".pi/agent/models.json")
    ]
    assert len(provider_files) == 1, f"a pi turn writes exactly one provider file: {auth.files}"
    return argv, json.loads(provider_files[0])


@pytest.mark.asyncio
async def test_a_chosen_level_reaches_pi_as_a_level_and_a_bounded_budget(monkeypatch):
    """`low` on the stored doc becomes `--thinking low` on argv AND a reasoning marker
    plus a thinking-token budget in the provider file.

    Both halves are needed and neither implies the other: pi-ai sends a budget only
    when the model is marked as reasoning and the compat names the field, and it only
    reserves a thinking phase at all when a level was named. Measured on the founder's
    endpoint, the budget is what turns 'reasoning consumed every output token' into an
    answer — with it, zero empty answers in ten turns; without it, four in ten.
    """

    async def cred(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {
                    "base_url": "http://host:11434/v1",
                    "model": "qwen2:7b",
                    "api_key": "sk-local",
                    "thinking": "low",
                }
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    argv, doc = _turn_shape(auth)

    assert argv[argv.index("--thinking") + 1] == "low"
    assert doc["providers"]["local"]["models"][0]["reasoning"] is True
    # The field pi puts the number under is the vLLM one; the founder's box is vLLM,
    # and the sibling spellings (`thinking_budget`, `thinking_budget_tokens`) are
    # Qwen-SGLang and llama.cpp, which would be ignored here.
    assert doc["providers"]["local"]["compat"]["supportsThinkingTokenBudget"] is True


@pytest.mark.asyncio
async def test_off_leaves_argv_and_provider_file_exactly_as_they_were(monkeypatch):
    """`off` is not a new behaviour to verify, it is the OLD one, and it is the level
    every box the founder already connected was given. So the assertion is equality
    with the literal shapes this card inherited — the exact argv tail and the exact
    provider keys — not merely 'no budget field'.

    It holds for a reason worth naming: pi's own default is `medium`, so a flag that
    named nothing would start a thinking phase nobody asked for. That is also why the
    level is not smuggled into the model shorthand: pi parses the LAST colon of
    `provider/id:level`, and a vLLM model id may itself contain one.
    """

    async def cred(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps(
                {
                    "base_url": "http://host:11434/v1",
                    "model": "qwen2:7b",
                    "api_key": "sk-local",
                    "thinking": "off",
                }
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    auth = await agent_auth.resolve(uuid.uuid4())
    argv, doc = _turn_shape(auth)

    assert "--thinking" not in argv
    assert not any("qwen2:7b:" in arg for arg in argv)  # no level hidden in the shorthand
    provider = doc["providers"]["local"]
    # Absent, not `false`: the file an `off` box gets is the file this card
    # inherited, key for key, and a marker written as False would be a new field
    # for pi to interpret rather than the absence of one.
    assert "reasoning" not in provider["models"][0]
    assert "supportsThinkingTokenBudget" not in provider["compat"]
    assert "thinkingFormat" not in provider["compat"]
    assert provider["compat"] == {"supportsDeveloperRole": False, "supportsReasoningEffort": False}


@pytest.mark.asyncio
async def test_a_row_written_before_the_level_existed_refuses_the_turn_instead_of_reasoning(
    monkeypatch,
):
    """A stored doc with no `thinking` key is refused, not read as `off`.

    The refusal is the point. `off` is NOT 'the model does not think' — measured on
    the founder's endpoint, pi with no level still received 2,170 reasoning tokens
    with the flag off, because the serving engine's chat template decides. A row that
    lost its level would therefore resume thinking while the screen reported a bound,
    which is the failure this card exists to end. And a silent default here would also
    hide a migration that never ran, so the message names it.
    """

    async def cred(_uid, _provider=None):
        return {
            "provider": "local",
            "kind": "endpoint",
            "secret": json.dumps({"base_url": "http://legacy:11434/v1", "model": "legacy:1b"}),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    with pytest.raises(ValueError, match="missing thinking"):
        await agent_auth.resolve(uuid.uuid4())


@pytest.mark.asyncio
async def test_a_level_paired_with_a_verbatim_override_is_refused_at_the_turn(monkeypatch):
    """The stored models.json wins over the level, so a box carrying both would show a
    level it never sends. The turn refuses rather than running the lie — the same rule
    `model_override` follows, enforced where the two are actually read together.
    """

    async def cred(_uid, provider=None):
        # The override is a column on the endpoint's own row, not another provider:
        # one box, one row, and the verbatim file is that row's second document.
        return {
            "provider": "local",
            "kind": "endpoint",
            "models_json": '{"providers": {"stash-local": {"baseUrl": "http://host:11434/v1"}}}',
            "secret": json.dumps(
                {
                    "base_url": "http://host:11434/v1",
                    "model": "qwen2:7b",
                    "api_key": "sk-local",
                    "thinking": "high",
                }
            ),
        }

    monkeypatch.setattr(agent_auth, "_get_credential", cred)
    with pytest.raises(ValueError, match="thinking level cannot be applied"):
        await agent_auth.resolve(uuid.uuid4())


@pytest.mark.asyncio
async def test_local_credential_none_when_absent(monkeypatch):
    async def no_cred(_uid, _provider=None):
        return None

    monkeypatch.setattr(agent_auth, "_get_credential", no_cred)
    assert await agent_auth.local_credential(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_local_credential_rejects_a_non_endpoint_row(monkeypatch):
    """A local row that is not an endpoint doc means the store is corrupt —
    say so instead of returning a half-parsed doc."""

    async def wrong_kind(_uid, _provider=None):
        return {"provider": "local", "kind": "api_key", "secret": "sk-surprise"}

    monkeypatch.setattr(agent_auth, "_get_credential", wrong_kind)
    with pytest.raises(ValueError):
        await agent_auth.local_credential(uuid.uuid4())
