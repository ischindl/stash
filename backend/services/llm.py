"""Direct Claude completions for non-agent, non-tool-use tasks.

Lives next to agent_runtime.py. Use this when you need a one-shot
synthesis from Claude with no MCP tools, no multi-turn loop, no SSE
streaming — e.g. generating UI copy, classifying a snippet, returning
structured JSON. The Agent SDK in agent_runtime.py is the wrong primitive
for those tasks.

Single source of truth for the provider a backend-side completion runs on.

Two providers answer, and a run picks one of them once: :func:`anthropic_route`
for a backend that has its own key, :func:`local_route` for a stack that runs on
an operator's own OpenAI-compatible box. Which one is configuration, never a
retry — see the resolution policy in scoped_curation_service.require_route.
"""

from __future__ import annotations

import enum
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)


class ModelTier(enum.Enum):
    """Two-tier model selection for non-agent Claude calls.

    QUALITY = Sonnet (settings.ANTHROPIC_MODEL). Use for reasoning, the
    ask-the-stash loop, anything user-facing where accuracy matters.

    FAST = Haiku (settings.ANTHROPIC_FAST_MODEL). Use for short
    classification / synthesis / structured-output tasks where speed
    and cost matter and Sonnet would be overkill."""

    QUALITY = "quality"
    FAST = "fast"


def _model_for(tier: ModelTier) -> str:
    if tier == ModelTier.QUALITY:
        return settings.ANTHROPIC_MODEL
    return settings.ANTHROPIC_FAST_MODEL


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set on the backend")
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


async def complete_text(
    *,
    prompt: str,
    system: str | None = None,
    tier: ModelTier = ModelTier.FAST,
    max_tokens: int = 1024,
) -> str:
    """One-shot Claude completion. Returns concatenated assistant text."""
    client = _get_client()
    kwargs: dict = {
        "model": _model_for(tier),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    msg = await client.messages.create(**kwargs)
    return "".join(getattr(b, "text", "") for b in msg.content)


async def complete_chat(
    *,
    messages: list[dict],
    system: str | None = None,
    tier: ModelTier = ModelTier.FAST,
    max_tokens: int = 1024,
) -> str:
    """Multi-turn Claude completion over a full message transcript.

    Same contract as complete_text but the caller supplies the whole
    conversation ([{"role": "user"|"assistant", "content": str}, ...])."""
    client = _get_client()
    kwargs: dict = {
        "model": _model_for(tier),
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    msg = await client.messages.create(**kwargs)
    return "".join(getattr(b, "text", "") for b in msg.content)


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked for, in the shape both providers agree on."""

    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class Turn:
    """One completion round: why it stopped, what it said, what it asked for.

    ``stop_reason`` is Anthropic vocabulary ("end_turn", "tool_use", or the
    provider's own word for a failure like truncation), because the caller's
    error messages name it. ``blocks`` is the assistant turn in Anthropic block
    form, which is what a caller appends to its own history to ask again."""

    stop_reason: str
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)


class Route(Protocol):
    """One provider's completion, called repeatedly by the caller's own loop."""

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str | None,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> Turn: ...


def anthropic_route() -> Route:
    return _AnthropicRoute()


def local_route(base_url: str, model: str, api_key: str | None) -> Route:
    return _LocalRoute(base_url, model, api_key)


class _AnthropicRoute:
    """The hosted provider: the backend's own key, exactly as before this layer."""

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str | None,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> Turn:
        # `_get_client` is called by module-level name so a test seam on the
        # module intercepts the provider, as it did before this layer existed.
        kwargs: dict = {
            "model": _model_for(tier),
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        response = await _get_client().messages.create(**kwargs)
        return Turn(
            stop_reason=response.stop_reason,
            text="\n".join(getattr(b, "text", "") for b in response.content if b.type == "text"),
            tool_calls=[
                ToolCall(id=b.id, name=b.name, input=b.input)
                for b in response.content
                if b.type == "tool_use"
            ],
            blocks=[b.model_dump() for b in response.content],
        )


# A local box reads a backlog in one request: the probe's 5-second budget is a
# reachability knock, not a completion budget. The whole run is still capped
# upstream by settings.AGENT_TURN_TIMEOUT_SECONDS.
_LOCAL_COMPLETION_TIMEOUT_S = 300.0
# The two words a chat-completions server uses for what a tool loop cares about.
_LOCAL_STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use"}


class _LocalRoute:
    """One OpenAI-compatible chat-completions server: the operator's own box.

    `tier` is Anthropic's quality/FAST vocabulary and has no meaning here — a
    local endpoint has exactly the one model id its credential stores.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key

    @property
    def completions_url(self) -> str:
        """The chat endpoint of a base URL (http://box:11434/v1 → …/v1/chat/completions),
        the same join `agent_auth.local_probe_url` uses for its model listing."""
        return self.base_url.rstrip("/") + "/chat/completions"

    async def complete(
        self,
        *,
        tier: ModelTier,
        system: str | None,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> Turn:
        payload: dict = {
            "model": self.model,
            "messages": _chat_messages(system, messages),
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = _chat_tools(tools)
        headers = {
            "Content-Type": "application/json",
            # A keyless box still gets a bearer: the dummy keeps this one
            # codepath, as `_local_auth`'s dummy key does for pi's config.
            "Authorization": f"Bearer {self.api_key or 'local'}",
        }
        async with httpx.AsyncClient(timeout=_LOCAL_COMPLETION_TIMEOUT_S) as http:
            response = await http.post(self.completions_url, json=payload, headers=headers)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"local model endpoint returned {response.status_code}: "
                f"{_body_snippet(response.text)}"
            )
        return _chat_turn(response.text)


def _chat_tools(tools: list[dict]) -> list[dict]:
    """Anthropic tool declarations → the chat-completions function form."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


def _chat_messages(system: str | None, messages: list[dict]) -> list[dict]:
    """Anthropic-form history → chat form. Anything unmappable raises: a tool
    result quietly dropped here is a model that resumes as if it had read it."""
    out: list[dict] = []
    if system:
        # "developer" is the newer OpenAI role and the one Ollama-style servers
        # reject, which is why pi's synthesized config switches it off too.
        out.append({"role": "system", "content": system})
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            out.append({"role": message["role"], "content": content})
        elif message["role"] == "assistant":
            out.append(_chat_assistant(content))
        elif message["role"] == "user":
            out.extend(_chat_tool_results(content))
        else:
            raise RuntimeError(f"role {message['role']} has no local-endpoint form")
    return out


def _chat_assistant(blocks: list[dict]) -> dict:
    text = "\n".join(_block_field(b, "text") for b in blocks if b["type"] == "text")
    tool_calls = []
    for block in blocks:
        if block["type"] == "tool_use":
            tool_calls.append(
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block["input"]),
                    },
                }
            )
        elif block["type"] != "text":
            raise RuntimeError(f"assistant block {block['type']} has no local-endpoint form")
    message: dict = {
        "role": "assistant",
        # A turn that only called tools has no content to send back, and chat
        # servers read an empty string as an answer, not as a pending tool call.
        "content": text or None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _chat_tool_results(blocks: list[dict]) -> list[dict]:
    results = []
    for block in blocks:
        if block["type"] != "tool_result":
            raise RuntimeError(f"user block {block['type']} has no local-endpoint form")
        # `is_error` has no chat equivalent: the refusal is already words inside
        # the content, which is why scope.tool() phrases them as an error.
        results.append(
            {"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]}
        )
    return results


def _block_field(block: dict, key: str) -> str:
    value = block.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f"{block['type']} block is missing a string {key!r}")
    return value


def _chat_turn(body: str) -> Turn:
    """A chat-completions answer → one Turn. Every malformed answer names the
    endpoint's own bytes, because that is the only text that says which server
    answered (vLLM, Ollama, and a captive-portal proxy all answer 200)."""
    payload = _json_body(body)
    choice = _first_choice(payload, body)
    message = choice.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"local model endpoint choice has no message: {_body_snippet(body)}")
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        raise RuntimeError(
            f"local model endpoint choice has no finish_reason: {_body_snippet(body)}"
        )
    text = message.get("content") or ""
    blocks = [{"type": "text", "text": text}] if text else []
    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        tool_id = call.get("id") if isinstance(call, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not tool_id or not name:
            raise RuntimeError(
                f"local model endpoint tool call needs an id and a function name: "
                f"{_body_snippet(body)}"
            )
        arguments = _tool_arguments(function.get("arguments"), body)
        tool_calls.append(ToolCall(id=tool_id, name=name, input=arguments))
        blocks.append({"type": "tool_use", "id": tool_id, "name": name, "input": arguments})
    return Turn(
        stop_reason=_LOCAL_STOP_REASONS.get(finish_reason, finish_reason),
        text=text,
        tool_calls=tool_calls,
        blocks=blocks,
    )


def _json_body(body: str) -> dict:
    try:
        payload = json.loads(body)
    except ValueError:
        raise RuntimeError(f"local model endpoint did not answer JSON: {_body_snippet(body)}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"local model endpoint did not answer an object: {_body_snippet(body)}")
    return payload


def _first_choice(payload: dict, body: str) -> dict:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError(f"local model endpoint answered no choice: {_body_snippet(body)}")
    return choices[0]


def _tool_arguments(raw: object, body: str) -> dict:
    if not isinstance(raw, str):
        raise RuntimeError(
            f"local model endpoint tool call arguments are not a JSON string: {_body_snippet(body)}"
        )
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError(
            f"local model endpoint tool call arguments are not JSON: {_body_snippet(raw)}"
        )


def _body_snippet(text: str, limit: int = 500) -> str:
    """The endpoint's own words, whitespace-collapsed and capped, so a proxy's
    full HTML page never lands in a curator's run error."""
    return " ".join(text.split())[:limit] or "(empty body)"


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


async def complete_json(
    *,
    prompt: str,
    system: str | None = None,
    tier: ModelTier = ModelTier.FAST,
    max_tokens: int = 1024,
) -> dict:
    """One-shot Claude completion that returns parsed JSON.

    Strips markdown code fences if present, then json.loads. Raises
    json.JSONDecodeError if the response can't be parsed."""
    text = await complete_text(prompt=prompt, system=system, tier=tier, max_tokens=max_tokens)
    m = _JSON_FENCE.search(text)
    payload = (m.group(1) if m else text).strip()
    return json.loads(payload)


async def complete_structured[ResponseModel: BaseModel](
    *,
    prompt: str,
    system: str,
    output_model: type[ResponseModel],
    tier: ModelTier,
    max_tokens: int,
) -> ResponseModel:
    """Constrain generation to the schema and validate it with the SDK."""
    response = await _get_client().messages.parse(
        model=_model_for(tier),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        system=system,
        output_format=output_model,
    )
    if response.parsed_output is None:
        raise ValueError(f"Structured completion returned no result ({response.stop_reason})")
    return response.parsed_output
