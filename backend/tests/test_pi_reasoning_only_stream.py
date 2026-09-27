"""pi turns that spend their output on reasoning and answer nothing must name a cause.

The truncation fixture is a real `pi --mode json` transcript, recorded against a
local throwaway OpenAI-compatible SSE endpoint with the pinned product build
(pi 0.84.2 / pi-ai 0.84.4): the endpoint streamed reasoning deltas only
(`reasoning_content`) and then stopped at the output limit — the behaviour the
founder's endpoint measures as `finish_reason=length` with a null message
content. The second shape below comes from the same capture session, where the
stream drops mid-reasoning: pi reports its own `errorMessage: "terminated"`
with only a thinking block attached. Both shapes make pi exit 0, so the mapped
error — never the exit code — is the only thing the operator ever reads, and a
bare transport word there is undebuggable.
"""

from pathlib import Path

import pytest

from backend.services import harness as h
from backend.services import sprite_agent_service as svc
from backend.services import sprite_service

FIXTURE = Path(__file__).parent / "fixtures" / "pi_reasoning_only_stream.jsonl"

# Verbatim message_end from the same capture session: the endpoint died mid
# reasoning, so pi carries the thinking block, no finish_reason, and its own
# transport word. This is the shape the founder's transcript showed 4x.
_TRANSPORT_DEATH_DURING_REASONING = (
    '{"type":"message_end","message":{"role":"assistant",'
    '"content":[{"type":"thinking","thinking":"Counting the tokens in my head. '
    'More thinking, still thinking. ","thinkingSignature":"reasoning_content"}],'
    '"api":"openai-completions","provider":"local","model":"mock-cut",'
    '"usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,'
    '"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},'
    '"stopReason":"error","timestamp":1790515957273,"responseId":"chatcmpl-mock",'
    '"responseModel":"mock","errorMessage":"terminated"}}'
)
_RETRY_SUMMARY_OF_THAT_TURN = (
    '{"type":"auto_retry_end","success":false,"attempt":3,"finalError":"terminated"}'  # noqa: E501
)


def _map_truncation_fixture() -> tuple[list[dict], h.TurnState]:
    state = h.TurnState()
    events: list[dict] = []
    for line in FIXTURE.read_text().splitlines():
        events.extend(h.map_line(h.PI, line, state))
    return events, state


def _map_lines(*lines: str) -> h.TurnState:
    state = h.TurnState()
    for line in lines:
        h.map_line(h.PI, line, state)
    return state


def test_reasoning_only_truncation_names_its_cause():
    events, state = _map_truncation_fixture()

    assert state.error is not None
    assert "finish_reason=length" in state.error
    assert "no answer content" in state.error
    assert state.error != "terminated"
    # The operator reads this in an alert; it must stay one plain sentence.
    assert len(state.error) <= 200
    # The reasoning tokens are reported as the evidence they are.
    assert "reasoning=8" in state.error
    # Nothing was answered, so no result text and no text delta was streamed.
    assert state.result_text is None
    assert [e for e in events if e["type"] == "text"] == []
    # This is not a lost transcript — reseeding would hide the real cause.
    assert state.resume_missing is False


def test_transport_death_during_reasoning_names_its_cause():
    state = _map_lines(_TRANSPORT_DEATH_DURING_REASONING)

    assert state.error is not None
    assert "no answer content" in state.error
    assert "terminated" not in state.error
    assert len(state.error) <= 200


def test_retry_summary_does_not_erase_the_named_cause():
    # pi retries the dead turn and closes with auto_retry_end carrying the same
    # transport word; the summary must not overwrite the cause already named
    # from the event that actually holds the evidence.
    state = _map_lines(_TRANSPORT_DEATH_DURING_REASONING, _RETRY_SUMMARY_OF_THAT_TURN)

    assert "terminated" not in state.error
    assert "no answer content" in state.error


def test_retry_summary_alone_still_reports_the_final_error():
    # The summary is still the error path when nothing earlier named a cause.
    state = _map_lines('{"type":"auto_retry_end","success":false,"attempt":3,"finalError":"boom"}')
    assert state.error == "boom"


def test_genuine_endpoint_error_keeps_its_own_words():
    # No reasoning, no tokens, no truncation signal: naming a budget here would
    # invent a cause, so the endpoint's own message survives verbatim.
    state = _map_lines(
        '{"type":"message_end","message":{"role":"assistant","content":[],'
        '"api":"openai-completions","provider":"local","model":"mock-1",'
        '"usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,'
        '"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,'
        '"cacheWrite":0,"total":0}},"stopReason":"error",'
        '"timestamp":1787416676209,"errorMessage":"Connection error."}}'
    )
    assert state.error == "Connection error."


def test_tool_call_only_turn_stays_green():
    # A tool round-trip answers with a tool call, not text — always green.
    state = _map_lines(
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"toolCall","id":"call_1","name":"bash",'
        '"arguments":{"command":"echo hi"}}],"api":"openai-completions",'
        '"provider":"local","model":"mock-1","usage":{"input":0,"output":0,'
        '"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,'
        '"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},'
        '"stopReason":"toolUse","timestamp":1787416541357,"rawStopReason":"tool_calls"}}'
    )
    assert state.error is None


def test_partial_answer_at_the_length_limit_is_success():
    # Truncated mid-sentence but it did answer: the partial text is the result.
    state = _map_lines(
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"a partial answer"}],'
        '"api":"openai-completions","provider":"local","model":"mock-1",'
        '"usage":{"input":21,"output":8,"reasoning":2,"cacheRead":0,"cacheWrite":0,'
        '"totalTokens":29,"cost":{"input":0,"output":0,"cacheRead":0,'
        '"cacheWrite":0,"total":0}},"stopReason":"length",'
        '"timestamp":1790515939573,"rawStopReason":"length"}}'
    )
    assert state.error is None
    assert state.result_text == "a partial answer"


def test_empty_turn_without_any_evidence_fails_loudly():
    # The silent-success hole: a terminal assistant turn that is neither an
    # answer nor a tool call used to leave the turn green with no text at all.
    # With no truncation signal it must report what it observed instead of
    # claiming a budget it cannot see.
    state = _map_lines(
        '{"type":"message_end","message":{"role":"assistant","content":[],'
        '"api":"openai-completions","provider":"local","model":"mock-1",'
        '"usage":{"input":9,"output":0,"cacheRead":0,"cacheWrite":0,'
        '"totalTokens":9,"cost":{"input":0,"output":0,"cacheRead":0,'
        '"cacheWrite":0,"total":0}},"stopReason":"stop",'
        '"timestamp":1790515939573,"rawStopReason":"stop"}}'
    )
    assert state.error is not None
    assert "no answer content" in state.error
    assert "stopReason=stop" in state.error
    assert "finish_reason=length" not in state.error


def test_empty_user_message_end_stays_green():
    # The role filter runs first: pi echoes user and toolResult messages
    # through message_end, and those are not the assistant's output.
    state = _map_lines('{"type":"message_end","message":{"role":"user","content":[]}}')
    assert state.error is None


@pytest.mark.asyncio
async def test_run_harness_surfaces_named_cause_on_a_zero_exit(monkeypatch):
    # The production path, not just the mapper: pi exits 0 on this turn, so an
    # unmapped stream would end the turn green, and a mapped-but-overwritten one
    # would end it as "agent exited with code 0".
    from backend.services import sprite_service as ss

    async def fake_exec_stream(sprite, argv, *, env, cwd=None):
        for line in FIXTURE.read_text().splitlines():
            yield {"stream": "stdout", "data": (line + "\n").encode()}
        yield {"exit_code": 0}

    monkeypatch.setattr(ss, "exec_stream", fake_exec_stream)
    state = h.TurnState()
    events = [
        e
        async for e in svc._run_harness(
            h.PI,
            sprite_service.Sprite(name="s"),
            ["pi", "--mode", "json"],
            state,
            {"STASH_LOCAL_KEY": "local-model-secret"},
        )
    ]

    assert events == []
    assert state.error is not None
    assert "finish_reason=length" in state.error
    assert "no answer content" in state.error
    assert "exited with code" not in state.error
    assert "local-model-secret" not in state.error
    assert state.resume_missing is False


def test_named_cause_is_not_mistaken_for_a_lost_transcript():
    # A named cause that matched RESUME_MISSING_RE would silently reseed the
    # session and bury the truncation under a fresh transcript.
    _, state = _map_truncation_fixture()
    assert h.RESUME_MISSING_RE.search(state.error) is None
