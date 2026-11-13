"""Model providers and the env var each harness CLI reads its key from.

Which provider/harness/credential a given user's turn runs on is decided in
agent_auth.resolve (bring-your-own key/OAuth, else the managed OpenRouter
agent on Pro). This module is just the provider vocabulary those decisions use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    env_var: str  # what the harness CLI reads the key from


ANTHROPIC = Provider("anthropic", "ANTHROPIC_API_KEY")
OPENAI = Provider("openai", "OPENAI_API_KEY")
OPENROUTER = Provider("openrouter", "OPENROUTER_API_KEY")
GEMINI = Provider("gemini", "GEMINI_API_KEY")
# The user's own OpenAI-compatible endpoint (Ollama, vLLM, ...), reached by
# the pi harness through a base URL stored as a credential — the key, when
# set, rides in STASH_LOCAL_KEY which pi's models.json $STASH_LOCAL_KEY
# interpolation reads.
LOCAL = Provider("local", "STASH_LOCAL_KEY")

# The reasoning levels a local endpoint can be set to. This is exactly the set
# `pi` 1.5.2 accepts for `--thinking` (its `VALID_THINKING_LEVELS`), measured
# from the installed bundle rather than invented here: a level outside it would
# be rejected by the harness at turn time, one session too late to be useful.
#
# `off` is what a fresh endpoint gets and what every endpoint had before this
# setting existed. It is not a claim that the endpoint will not reason — it is
# "ask for nothing", which is what Stash shipped before: no `--thinking` flag and
# no thinking field on the request. What each endpoint does unasked is its own
# chat template's decision (measured: a vLLM box reasoned 2048 tokens with the
# level at `off`).
THINKING_OFF = "off"
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
