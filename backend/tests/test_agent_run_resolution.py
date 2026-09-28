"""A curator run that dies mid-turn must still resolve its lane and name its cause.

Two measured founder-stack failures drive these tests; the full measurements are
in the STAS-281 `baseline-evidence` task document.

1. The stranding. On 2026-09-28 the RunFusion lane (local/deepseek-v4) was
   stamped `started` at 12:51:57Z and was still `started` when a later dispatch
   deferred to it at 14:03:19Z ("curator run in flight ... this dispatch defers
   to it and writes nothing"). The soft time limit fired at 14:21:57Z and the
   deployed traceback shows where: `agent_schedules.py:85` -> `_celery_helpers.py:55`
   -> `base_events._run_once` -> `selectors.select` ->
   `billiard.pool.soft_timeout_sighandler`. `SoftTimeLimitExceeded` is raised by
   the loop machinery, not by a frame inside the coroutine, so the coroutine's
   `except` (which calls `mark_run_failed`) and its `finally` (which releases the
   single-flight lock) never run. The row stayed `started` and the Redis lock
   stayed held until its 6600 s TTL: the lane read as "running" for 3 h 25 min.
2. The lost evidence. Thirty-three hours of the heavy worker's logs contain
   eighteen hits of pi's cause as the bare word `terminated` and zero lines
   carrying pi's exit code or its stderr tail, because pi exits 0 whenever it
   names the error itself in-stream. The exit code and the stderr tail are what
   separate "the endpoint died mid-reasoning" from "the client stopped waiting",
   so the runner has to record them while it still has them.

Both violate the invariant `agent_service.mark_run` states: every path after
`started` must resolve the outcome as ran, failed, or skipped.
"""

from __future__ import annotations

import asyncio
import signal
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from backend import database
from backend.celery_app import celery
from backend.database import get_pool
from backend.services import agent_service, alert_service, sprite_agent_service, sprite_service
from backend.services import harness as harness_mod
from backend.tasks import _celery_helpers, agent_schedules

from .conftest import FakeRedis, unique_name

# Seeded away from zero so a missing refund OR a double refund is a visible
# number rather than a NULL.
MONTH_COUNT = 5

# Verbatim pi frame (recorded transcript, fixtures/pi_stream.jsonl): a turn that
# answered. Used to prove the evidence recording stays out of clean runs.
_PI_ANSWER_LINE = (
    '{"type":"message_end","message":{"role":"assistant",'
    '"content":[{"type":"text","text":"all curated "}],"api":"openai-completions",'
    '"provider":"local","model":"mock-1","usage":{"input":0,"output":0,'
    '"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,'
    '"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop",'
    '"timestamp":1787416541400,"responseId":"chatcmpl-1","rawStopReason":"stop"}}'
)

# The shape the founder's transcript showed four times: pi names the transport
# death itself and exits 0. Same event STAS-288 names a cause from.
_PI_TRANSPORT_DEATH_LINE = (
    '{"type":"message_end","message":{"role":"assistant",'
    '"content":[{"type":"thinking","thinking":"Counting the tokens in my head. '
    'More thinking, still thinking. ","thinkingSignature":"reasoning_content"}],'
    '"api":"openai-completions","provider":"local","model":"mock-cut",'
    '"usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,'
    '"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},'
    '"stopReason":"error","timestamp":1790515957273,"responseId":"chatcmpl-mock",'
    '"responseModel":"mock","errorMessage":"terminated"}}'
)

# pi's own words, verbatim: a transport failure with no reasoning content to
# blame, the one shape STAS-288 leaves the endpoint's message as the cause for.
# Deriving it from the transport-death line above instead would attach a thinking
# block, whose cause is the reasoning budget and whose endpoint word STAS-288
# deliberately drops (test_pi_reasoning_only_stream.py).
_PI_ENDPOINT_ERROR_LINE = (
    '{"type":"message_end","message":{"role":"assistant","content":[],'
    '"api":"openai-completions","provider":"local","model":"mock-1",'
    '"usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,'
    '"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},'
    '"stopReason":"error","timestamp":1787416676209,"errorMessage":"Connection error."}}'
)


async def _lane() -> dict:
    """A scheduled curator lane with its month counter rolled to today."""
    user_id = uuid4()
    email = f"{unique_name('resolve')}@example.com"
    await get_pool().execute(
        "INSERT INTO users (id, name, display_name) VALUES ($1, $2, $2)", user_id, email
    )
    agent = await agent_service.get_or_create_curator(user_id)
    await get_pool().execute(
        "UPDATE agents SET run_mode = 'scheduled', month_run_count = $2, "
        "month_run_anchor = date_trunc('month', now())::date WHERE id = $1",
        agent["id"],
        MONTH_COUNT,
    )
    return await agent_service.get_agent_by_id(agent["id"])


async def _row(agent_id: UUID) -> dict:
    return dict(await get_pool().fetchrow("SELECT * FROM agents WHERE id = $1", agent_id))


async def _stamp_run(agent_id: UUID, outcome: str, *, age_seconds: int) -> None:
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = $2, last_run_at = now() - make_interval(secs => $3) "
        "WHERE id = $1",
        agent_id,
        outcome,
        age_seconds,
    )


def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def fake_send_alert(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(alert_service, "send_alert", fake_send_alert)
    return sent


@pytest.fixture
def lane_locks(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """The Redis behind the single-flight lane lock.

    `FakeRedis` implements no TTL expiry, so a lock that only production's TTL
    would clear stays visibly held here — which is the stranding this task is
    about, not a test artifact."""
    redis = FakeRedis()
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: redis)
    return redis


def _held_lane_locks(redis: FakeRedis) -> list[str]:
    return [key for key in redis.data if key.startswith("agent-run:")]


@pytest.fixture
def worker_loop(pool, monkeypatch: pytest.MonkeyPatch):
    """A Celery worker process, as far as a task body can be simulated.

    `run_async` asserts on the loop that `worker_process_init` opens — a signal
    pytest never fires — and a task body is synchronous, so these tests need
    their own loop that they can drive themselves rather than the loop
    pytest-asyncio is already running. The db pool has to be opened on it too:
    an asyncpg connection belongs to the loop that created it.
    """
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(_celery_helpers, "_loop", loop)
    monkeypatch.setattr(database, "pool", None)
    loop.run_until_complete(database.init_pool())
    try:
        yield loop
    finally:
        # The abandoned run's coroutine is still suspended mid-turn — the defect
        # being reproduced, not a leak to hide. Cancel it before closing.
        abandoned = asyncio.all_tasks(loop)
        for task in abandoned:
            task.cancel()
        if abandoned:
            loop.run_until_complete(asyncio.gather(*abandoned, return_exceptions=True))
        signal.setitimer(signal.ITIMER_REAL, 0)
        loop.run_until_complete(database.close_db())
        loop.close()


@contextmanager
def _billiard_soft_time_limit():
    """Raise `SoftTimeLimitExceeded` the way Celery's prefork pool does.

    billiard installs a SIGALRM handler that raises wherever the interpreter
    happens to be when the timer fires. The stalled turns below arm it just
    before their last `await`, so the loop is parked in `selectors.select` and
    the raise lands in the loop machinery — outside the coroutine — which is
    what leaves its `except`/`finally` unrun. Raising the exception from inside
    the task body would test a much easier bug and pass without the fix."""

    def _handler(signum, frame):
        raise SoftTimeLimitExceeded()

    previous = signal.signal(signal.SIGALRM, _handler)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _arm_soft_time_limit() -> None:
    signal.setitimer(signal.ITIMER_REAL, 0.25)


async def _stall(seconds: float = 30) -> None:
    await asyncio.sleep(seconds)


# --- the escape that the coroutine cannot catch (Step 4's guard) ---


def test_soft_time_limit_escape_resolves_the_lane_and_names_its_cause(
    monkeypatch, lane_locks, worker_loop
):
    lane = worker_loop.run_until_complete(_lane())

    async def stalled_turn(*args):
        _arm_soft_time_limit()
        await _stall()

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", stalled_turn)

    with _billiard_soft_time_limit(), pytest.raises(SoftTimeLimitExceeded):
        agent_schedules.run_curator_now.run(str(lane["id"]))

    row = worker_loop.run_until_complete(_row(lane["id"]))
    assert row["last_run_outcome"] == "failed"
    assert "run aborted" in row["last_run_error"]
    # The exception's own name is the cause the operator reads; a bare
    # "agent turn failed" would send them looking at the wrong layer.
    assert "SoftTimeLimitExceeded" in row["last_run_error"]
    # The metered dispatch delivered nothing, so its allowance credit is back.
    assert row["month_run_count"] == MONTH_COUNT
    # And the lane must become dispatchable now, not after the lock's TTL.
    assert _held_lane_locks(lane_locks) == []


def test_soft_time_limit_escape_of_an_unmetered_run_refunds_nothing(
    monkeypatch, lane_locks, worker_loop
):
    """A first-day/platform run never charged a credit, so resolving it must not
    hand one back — `greatest(count - 1, 0)` would silently buy a free run."""
    lane = worker_loop.run_until_complete(_lane())

    async def stalled_turn(*args):
        _arm_soft_time_limit()
        await _stall()

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", stalled_turn)

    with _billiard_soft_time_limit(), pytest.raises(SoftTimeLimitExceeded):
        agent_schedules.run_curator_now.run(str(lane["id"]), metered=False)

    row = worker_loop.run_until_complete(_row(lane["id"]))
    assert row["last_run_outcome"] == "failed"
    assert row["month_run_count"] == MONTH_COUNT
    assert _held_lane_locks(lane_locks) == []


def test_escape_before_the_run_is_metered_invents_no_failure(monkeypatch, lane_locks, worker_loop):
    """Dying before `mark_run` leaves a lane that never started: the guard's
    resolution is a compare-and-set on `started`, so it must write nothing — and
    still hand back the lock it did take, or the lane is wedged for its TTL."""
    lane = worker_loop.run_until_complete(_lane())

    async def get_agent_then_stall(agent_id):
        _arm_soft_time_limit()
        await _stall()
        return await agent_service.get_agent_by_id(agent_id)

    monkeypatch.setattr(agent_service, "get_agent_by_id", get_agent_then_stall)

    with _billiard_soft_time_limit(), pytest.raises(SoftTimeLimitExceeded):
        agent_schedules.run_curator_now.run(str(lane["id"]))

    row = worker_loop.run_until_complete(_row(lane["id"]))
    assert row["last_run_outcome"] is None
    assert row["month_run_count"] == MONTH_COUNT
    assert _held_lane_locks(lane_locks) == []


def test_escape_after_the_lane_recorded_success_leaves_the_record_alone(
    monkeypatch, lane_locks, worker_loop
):
    """A run that already wrote `ran` resolved itself. A guard that stamps
    blindly would report a finished curation as a failure and refund an
    allowance the user actually spent."""
    lane = worker_loop.run_until_complete(_lane())
    real_mark_succeeded = agent_service.mark_run_succeeded

    async def mark_succeeded_then_stall(agent_id):
        await real_mark_succeeded(agent_id)
        _arm_soft_time_limit()
        await _stall()

    async def succeeding_turn(*args):
        return None

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", succeeding_turn)
    monkeypatch.setattr(agent_service, "mark_run_succeeded", mark_succeeded_then_stall)

    with _billiard_soft_time_limit(), pytest.raises(SoftTimeLimitExceeded):
        agent_schedules.run_curator_now.run(str(lane["id"]))

    row = worker_loop.run_until_complete(_row(lane["id"]))
    assert row["last_run_outcome"] == "ran"
    assert row["last_run_error"] is None
    assert row["month_run_count"] == MONTH_COUNT + 1
    assert _held_lane_locks(lane_locks) == []


def test_catchable_turn_failure_keeps_its_own_cause(monkeypatch, lane_locks, worker_loop):
    """A catchable turn failure already resolves its own lane from inside the
    coroutine. The escape guard in the task body must leave that record alone —
    the founder's `last_run_error` has to name the provider, not the wrapper.
    It also pins that the alert carries the cause: on 2026-09-28 the founder
    stack's `send_alert` raised for missing Slack credentials, and a lane whose
    bookkeeping depended on alerting would have been lost with the notification.
    """
    lane = worker_loop.run_until_complete(_lane())
    alerts = _capture_alerts(monkeypatch)

    async def failing_turn(*args):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(sprite_agent_service, "run_scheduled", failing_turn)

    agent_schedules.run_scheduled_agent.run(str(lane["id"]), "202609281200")

    row = worker_loop.run_until_complete(_row(lane["id"]))
    assert row["last_run_outcome"] == "failed"
    assert row["last_run_error"] == "provider exploded"
    # The nightly credit is spent by the beat when it dispatches, so a failed
    # run hands it back — one refund, no more.
    assert row["month_run_count"] == MONTH_COUNT - 1
    assert _held_lane_locks(lane_locks) == []
    assert any("provider exploded" in text for text in alerts)


# --- the escape no in-process handler can catch (Step 5's reaper) ---


async def test_reaper_resolves_only_lanes_past_the_run_lock_ttl(pool, monkeypatch, lane_locks):
    fresh = await _lane()
    stranded = await _lane()
    finished = await _lane()

    await _stamp_run(fresh["id"], "started", age_seconds=60)
    await _stamp_run(
        stranded["id"], "started", age_seconds=agent_schedules.AGENT_RUN_LOCK_TTL + 600
    )
    await _stamp_run(finished["id"], "ran", age_seconds=agent_schedules.AGENT_RUN_LOCK_TTL + 600)
    await lane_locks.set(f"agent-run:{stranded['id']}", "token-of-a-dead-worker", ex=1)

    alerts = _capture_alerts(monkeypatch)
    assert await agent_schedules._reap_stranded_runs() == 1

    stranded_row = await _row(stranded["id"])
    assert stranded_row["last_run_outcome"] == "failed"
    assert "never resolved" in stranded_row["last_run_error"]
    # The reaper repairs the lane, not the accounting: `mark_run` records no
    # metering flag, so a sweep cannot tell a credit the run was charged for from
    # one the platform ran unmetered. Guessing either way would move a user's
    # allowance on a hunch, so the count is left alone and named in the alert.
    assert stranded_row["month_run_count"] == MONTH_COUNT
    # The lock outlived the worker that took it; deleting it is what makes the
    # lane dispatchable again on the next tick instead of at TTL expiry.
    assert f"agent-run:{stranded['id']}" not in lane_locks.data

    # A `started` younger than the lock TTL is a run still executing — calling
    # that dead would fail a healthy 90-minute curation.
    assert (await _row(fresh["id"]))["last_run_outcome"] == "started"
    # A resolved lane is none of the reaper's business.
    finished_row = await _row(finished["id"])
    assert finished_row["last_run_outcome"] == "ran"
    assert finished_row["month_run_count"] == MONTH_COUNT

    assert len(alerts) == 1
    # The sweep runs unattended, so its report has to identify the lane and say
    # what it did to the allowance.
    assert str(stranded["id"]) in alerts[0]
    assert "allowance untouched" in alerts[0]


async def test_reaper_resolves_lanes_even_when_alerting_is_broken(pool, monkeypatch, lane_locks):
    """The founder stack has no Slack credentials, so `send_alert` raises. Lane
    resolution is one UPDATE committed before any alerting, or a missing env var
    strands every lane the reaper was written to free."""
    stranded = await _lane()
    await _stamp_run(
        stranded["id"], "started", age_seconds=agent_schedules.AGENT_RUN_LOCK_TTL + 600
    )

    async def broken_alert(text: str) -> None:
        raise RuntimeError("ALERT_SLACK_TEAM_ID and ALERT_SLACK_CHANNEL_ID are required")

    monkeypatch.setattr(alert_service, "send_alert", broken_alert)

    with pytest.raises(RuntimeError, match="ALERT_SLACK"):
        await agent_schedules._reap_stranded_runs()

    row = await _row(stranded["id"])
    assert row["last_run_outcome"] == "failed"
    assert "never resolved" in row["last_run_error"]


def test_reaper_runs_on_beat_without_harness_limits():
    # Nothing catches these escapes in-process, so the sweep has to be periodic:
    # a beat entry is the only thing standing between a killed worker and a
    # lane that reads as "running" forever.
    entries = celery.conf.beat_schedule
    assert "reap-stranded-agent-runs" in entries
    # It is a sweep, not a harness run: it must keep the global 1500/1800 ceiling
    # so it can never hold a worker slot for 95 minutes.
    assert agent_schedules.reap_stranded_runs.soft_time_limit is None
    assert agent_schedules.reap_stranded_runs.time_limit is None


def test_reaper_waits_out_the_lock_ttl_it_replaces():
    # Until the lock's TTL elapses, `_run_in_flight` is right to treat the lane
    # as busy; reaping any earlier would fail a run that is genuinely executing.
    assert agent_schedules.REAP_STRIKE_AGE_SECONDS == agent_schedules.AGENT_RUN_LOCK_TTL


# --- the evidence a bad turn loses (Step 3) ---


async def _drive_harness(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[dict],
    exit_code: int,
    provider_env: dict[str, str] | None = None,
):
    """Run one `_run_harness` turn over canned exec frames.

    Frames use the `{"stream": "stdout"|"stderr", "data": bytes}` shape both exec
    modes yield — the local mode the founder stack runs separates stderr into its
    own frames, which is the distinction the evidence depends on."""

    async def fake_exec_stream(sprite, argv, *, env, cwd=None):
        for frame in frames:
            yield frame
        yield {"exit_code": exit_code}

    monkeypatch.setattr(sprite_service, "exec_stream", fake_exec_stream)
    state = harness_mod.TurnState()
    events: list[dict] = []
    async for event in sprite_agent_service._run_harness(
        harness_mod.PI,
        sprite_service.Sprite(name="test-sprite"),
        ["pi", "--mode", "json"],
        state,
        provider_env or {},
    ):
        events.append(event)
    return state, events


async def test_bad_turn_names_the_cause_and_records_exit_code_and_stderr(monkeypatch):
    lane = _PI_TRANSPORT_DEATH_LINE
    state, _ = await _drive_harness(
        monkeypatch,
        [
            {"stream": "stdout", "data": (lane + "\n").encode()},
            {"stream": "stderr", "data": b"TypeError: terminated\n"},
        ],
        exit_code=0,
    )

    assert state.error is not None
    # STAS-288's named cause survives.
    assert "no answer content" in state.error
    # pi exited 0 here, so the exit code is only recoverable at this moment.
    assert "exit=0" in state.error
    assert "TypeError: terminated" in state.error


async def test_nonzero_exit_with_a_named_error_still_records_the_code(monkeypatch):
    state, _ = await _drive_harness(
        monkeypatch,
        [{"stream": "stdout", "data": (_PI_ENDPOINT_ERROR_LINE + "\n").encode()}],
        exit_code=1,
    )

    assert "Connection error." in state.error
    assert "exit=1" in state.error


async def test_stderr_never_enters_the_json_parse_buffer(monkeypatch):
    """CLI progress text on stderr is not a transcript line. Concatenating it
    into the parse buffer fabricates unparsed transcript lines and buries the
    one line that carries the cause."""
    state, _ = await _drive_harness(
        monkeypatch,
        [
            {"stream": "stderr", "data": b"npm warn unknown config\n"},
            {"stream": "stdout", "data": (_PI_TRANSPORT_DEATH_LINE + "\n").encode()},
        ],
        exit_code=0,
    )

    assert list(state.unparsed) == []
    assert "npm warn unknown config" in state.error


async def test_stderr_evidence_is_redacted(monkeypatch):
    """A CLI dying on auth echoes the env prefix it was started with, so the tail
    being kept is a secret-carrying string. It lands in a lane row that the API
    surfaces to the user, so the injected key has to be scrubbed before it does."""
    state, _ = await _drive_harness(
        monkeypatch,
        [{"stream": "stderr", "data": b"Authorization: Bearer sk-live-secret\n"}],
        exit_code=127,
        provider_env={"ANTHROPIC_API_KEY": "sk-live-secret"},
    )

    assert "sk-live-secret" not in state.error
    assert "Authorization: Bearer [redacted]" in state.error


async def test_clean_turn_records_no_failure_evidence(monkeypatch):
    state, _ = await _drive_harness(
        monkeypatch,
        [{"stream": "stdout", "data": (_PI_ANSWER_LINE + "\n").encode()}],
        exit_code=0,
    )

    # A clean turn must not collect any of the failure evidence above.
    assert state.error is None
