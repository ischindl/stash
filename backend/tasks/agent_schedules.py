"""Run scheduled agents on their cron.

The beat task fires every minute and is a pure dispatcher: it finds agents
whose cron tick is due, consumes the tick, applies the cheap gates (credits,
credential, pending changes), and hands each eligible run to
`run_scheduled_agent` on the heavy queue — a headless agent turn runs for
minutes and must not hold a default-queue slot. The agent's own turn lock
(Redis) prevents overlap with an in-flight run.
"""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from croniter import croniter

from ..celery_app import celery
from ._celery_helpers import run_async

logger = logging.getLogger(__name__)

# Each pass keeps the existing event and time limits. Queue the next pass
# separately so a busy developer account does not wait another day for its
# next 500 inputs, or monopolize one worker indefinitely. In addition to this
# per-chain bound, executions share seven automatic continuations per 24h.
CURATOR_CATCHUP_PASSES = 8

# A lane whose `started` is older than the longest run a task may legally take has
# no live worker behind it. Neither rescuer can catch a child killed by the hard
# time limit, the memory-recycle, or a SIGKILL, so age is the only evidence left,
# and it has to be measured against the ceiling a live run may still be under —
# not against one lane's own wall: the developer-platform lane is bounded by its
# turn timeout, the personal-wiki lane by this worker ceiling, and reaping on the
# smaller of the two would fail a run that is genuinely executing.
REAP_STRIKE_AGE_SECONDS = celery.conf.task_time_limit


def _is_due(cron: str, last_run: datetime | None, now: datetime) -> bool:
    """True if a cron tick falls in (last_run, now]. Never fires on the very
    first sight of an agent — the baseline is 'now', so it starts next tick."""
    base = last_run or now
    try:
        nxt = croniter(cron, base).get_next(datetime)
    except (ValueError, KeyError):
        logger.warning("agent schedule: bad cron %r", cron)
        return False
    return nxt <= now


def _run_guarded(agent_uuid: UUID, metered: bool, run: Coroutine[Any, Any, None]) -> None:
    """Run one of the two harness-run bodies, resolving the lane if the body
    escapes it.

    `mark_run` promises that every path after `started` resolves the outcome, and
    the coroutine keeps that promise with its `except` and `finally`. Neither of
    them can catch this class of exit: Celery's prefork pool raises
    `SoftTimeLimitExceeded` from a SIGALRM handler, so the raise lands in the
    loop machinery under `run_async` — outside every frame of the coroutine.
    Measured on the founder stack on 2026-09-28: the RunFusion lane was stamped
    `started` at 12:51:57Z, its soft limit fired at 14:21:57Z, and a dispatch at
    14:03:19Z still deferred to it as a "curator run in flight" — the row stayed
    `started` and the lock stayed held until its TTL.

    So the guard sits outside `run_async`, and catches `BaseException` rather
    than `Exception`: a deploy's `CancelledError` leaves through the same hole,
    and the coroutine's `except Exception` cannot see it either. The exception is
    re-raised untouched — what Celery logs, retries, and pages on is the worker's
    own report of why the task died, and rewriting that would hide the timeout
    that is the actual finding.

    The escaped coroutine is not dead, only unscheduled: `run_until_complete`
    returns with its task still pending, and the next task this child runs
    resumes it — measured here, 2026-09-28: after the raise the task was still in
    `asyncio.all_tasks(loop)` and completed ("body-completed", its `finally` run)
    during the following unrelated `run_until_complete`. Two consequences are by
    design rather than accidents. A resumed run that really does finish may stamp
    `ran` over this guard's `failed`, which is the truth arriving late — the CAS
    only protects the other direction, a lane already resolved by the run itself.
    And the resumed run still holds its lane lock token: `_TurnLock.release`
    compares tokens, so its late release cannot delete a key the guard already
    dropped for the next dispatch.
    """
    try:
        run_async(run)
    except BaseException as exc:
        _resolve_escaped_run(agent_uuid, exc, metered=metered)
        raise


def _resolve_escaped_run(agent_uuid: UUID, exc: BaseException, *, metered: bool) -> None:
    """Record and unlock a run that is never going to resolve its own lane.

    The write is the shared compare-and-set (`agent_service.resolve_started_runs`)
    because the lane may already have resolved itself — a run that recorded `ran`
    before the raise reached the loop, or a pre-STAS-317 row a contended dispatch
    had already stamped as its skip — and neither may be stamped over as a failure.

    Nothing here outranks the escaped exception. If the write fails, the lane is
    left `started` for the stranded-run sweep, which exists for exactly that, and
    the original exception still propagates. No alert goes out from here either:
    the propagated exception is the worker's loud report, `alert_stale_curators`
    pages if the outcome persists, and `send_alert` raising for missing Slack
    credentials must not cost the lane its resolution.
    """
    from ..services import agent_service, sprite_agent_service

    cause = f"agent run aborted before it resolved: {type(exc).__name__}: {exc}"
    try:
        flipped = run_async(
            agent_service.resolve_started_runs([agent_uuid], cause, metered=metered)
        )
    except BaseException:
        logger.exception(
            "escaped agent run: could not resolve lane %s — the stranded-run sweep owns it",
            agent_uuid,
        )
        return
    try:
        run_async(sprite_agent_service.drop_agent_run_lock(agent_uuid))
    except BaseException:
        logger.exception("escaped agent run: could not release lane %s's lock", agent_uuid)
    if flipped:
        logger.error("lane %s resolved by the run-escape guard: %s", agent_uuid, cause)


@celery.task(name="backend.tasks.agent_schedules.run_due")
def run_due() -> int:
    return run_async(_run_due())


@celery.task(name="backend.tasks.agent_schedules.run_scheduled_agent")
def run_scheduled_agent(agent_id: str, stamp: str) -> None:
    # The beat consumed the tick and metered the run before dispatching (`mark_run`
    # in `_run_due`), so this body always owes a metered run.
    agent_uuid = UUID(agent_id)
    _run_guarded(agent_uuid, True, _run_scheduled_agent(agent_uuid, stamp))


@celery.task(name="backend.tasks.agent_schedules.run_curator_now")
def run_curator_now(
    agent_id: str,
    full_history: bool = False,
    metered: bool = True,
    catchup_remaining: int = CURATOR_CATCHUP_PASSES - 1,
    catchup: bool = False,
) -> None:
    agent_uuid = UUID(agent_id)
    _run_guarded(
        agent_uuid,
        metered,
        _run_curator_now(agent_uuid, full_history, metered, catchup_remaining, catchup),
    )


async def _run_curator_now(
    agent_id: UUID,
    full_history: bool = False,
    metered: bool = True,
    catchup_remaining: int = CURATOR_CATCHUP_PASSES - 1,
    catchup: bool = False,
) -> None:
    """A user-requested curator run: same execution as the daily tick, minus
    the due-check — the user is the trigger. The router already enforced the
    free-tier allowance and resolved credentials.

    `full_history` is the backfill: the run reads with no watermark, but the
    stored watermark is only advanced after success — a failed backfill must
    not have thrown away the incremental position.

    `metered=False` is for runs the platform initiates on its own (the
    first-day curator): they must not eat the user's free monthly allowance."""
    from ..services import (
        agent_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    agent = await agent_service.get_agent_by_id(agent_id)
    workspace = await scoped_curation_service.workspace_for_agent(agent)
    try:
        async with _run_lock(agent, workspace):
            agent = await agent_service.get_agent_by_id(agent_id)
            await _run_curator_locked(
                agent, workspace, full_history, metered, catchup_remaining, catchup
            )
    except sprite_agent_service.TurnInProgress:
        # No mark_run and no charge happened: leave the active run untouched.
        logger.info("agent schedule: curator %s is busy; skipping", agent_id)


def _run_lock(agent: dict, workspace):
    from ..config import settings
    from ..services import sprite_agent_service

    if workspace is not None:
        # The developer-platform lane: this key is the fence its commit re-enters
        # under `scoped_lock_held`, so the name is upstream's and stays upstream's.
        return sprite_agent_service._TurnLock(
            f"scoped-curator-{agent['id']}", settings.AGENT_TURN_TIMEOUT_SECONDS
        )
    # The personal-wiki lane has no other single-flight guard: its turn lock is
    # keyed by a per-run session id, so two runs of one agent never contend on it,
    # and the watermark CAS only refuses the clobber — it cannot stop a second paid
    # harness turn being spent on material the run in flight already read.
    # Upstream's body reads as if a fence were here (`curator is busy; skipping`);
    # this lock is that fence. Its TTL is the longest any task may legally take, so
    # it is never released while its own run is still executing.
    return sprite_agent_service.agent_run_lock(agent["id"], REAP_STRIKE_AGE_SECONDS)


async def _run_turn(agent, stamp, workspace, read):
    from ..services import sprite_agent_service

    if workspace is None:
        return await sprite_agent_service.run_scheduled(agent, stamp, read)
    return await sprite_agent_service.run_scheduled(
        agent, stamp, read, workspace=workspace, scoped_lock_held=True
    )


async def _pending_input(agent: dict, workspace) -> bool:
    from ..services import curation_service, scoped_curation_inputs

    if workspace is not None:
        return await scoped_curation_inputs.has_pending(agent, workspace)
    owner = UUID(str(agent["user_id"]))
    return await curation_service.has_changes_since(
        owner, owner, curation_service.position_of(agent), agent["curator_wiki"]
    )


async def _run_curator_locked(agent, workspace, full_history, metered, catchup_remaining, catchup):
    from ..services import (
        agent_service,
        billing_service,
        curation_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    agent_id = UUID(str(agent["id"]))
    if not metered and agent["run_mode"] != "scheduled":
        return
    if catchup:
        if workspace is None:
            return
        await scoped_curation_service.require_run_auth(agent, workspace=workspace)
        owner = UUID(str(agent["user_id"]))
        if not await billing_service.is_pro(owner) or not await _pending_input(agent, workspace):
            return
        if not await agent_service.claim_catchup(agent_id, CURATOR_CATCHUP_PASSES - 1):
            return
    # The compare-and-set anchor for mark_curated is the stored position this run
    # actually loaded — captured BEFORE the full_history override below, which zeroes
    # the feed's position but must not change what the run may write against.
    read = curation_service.position_of(agent)
    if full_history:
        agent = {
            **agent,
            "curated_through": None,
            "curated_through_event_id": None,
            "curation_cursor": {},
        }
    now = datetime.now(UTC)
    await agent_service.mark_run(agent_id, metered=metered)
    try:
        # Seconds-resolution stamp so a manual run never shares a session with
        # the beat's minute-stamped run.
        await _run_turn(agent, now.strftime("%Y%m%d%H%M%S"), workspace, read)
        if workspace is None:
            # The boundary is computed from the position the run ACTUALLY read from,
            # which the full_history override moved to `never`: a backfill counts the
            # whole corpus as its backlog, so its overflow boundary sits under the
            # stored cursor and the clamp keeps the cursor where an earlier run left
            # it. The completion write is a compare-and-set on `read`, so an ingest
            # rewind or an overlapping run cannot be written over.
            through = await curation_service.complete_through(
                UUID(str(agent["user_id"])),
                curation_service.position_of(agent),
                now,
                agent["curator_wiki"],
                agent.get("curator_folder_id"),
            )
            await agent_service.mark_curated(agent_id, read, through)
        await agent_service.mark_run_succeeded(agent_id)
    except Exception as e:
        if workspace is not None and isinstance(e, sprite_agent_service.TurnInProgress):
            await _skip_busy_curator(agent_id, metered=metered)
            return
        await agent_service.mark_run_failed(agent_id, str(e), metered=metered)
        raise
    await _dispatch_curator_catchup(agent, now, catchup_remaining, workspace=workspace)


async def _skip_busy_curator(agent_id: UUID, *, metered: bool) -> None:
    """The active run owns the shared outcome; refund only this skipped charge."""
    from ..services import agent_service

    if metered:
        await agent_service.refund_run(agent_id)
    logger.info(
        "agent schedule: curator %s is already running; skipping overlapping pass", agent_id
    )


_WORKSPACE_UNSET = object()


async def _dispatch_curator_catchup(
    agent: dict, run_started: datetime, remaining: int, *, workspace=_WORKSPACE_UNSET
) -> None:
    """A dispatch failure must not undo a successfully completed batch."""
    from ..services import alert_service

    try:
        await _queue_curator_catchup(agent, run_started, remaining, workspace=workspace)
    except Exception:
        logger.exception("agent schedule: catch-up dispatch failed for agent %s", agent["id"])
        # Asked before the call, never caught after it: a mode that sends no
        # operational alerts makes send_alert raise, and a raise here would
        # replace the failure this alert exists to report.
        if alert_service.alerts_enabled():
            await alert_service.send_alert(
                f"Curator catch-up dispatch failed for agent {agent['id']}; "
                "the completed batch and its checkpoint are preserved."
            )


async def _queue_curator_catchup(
    agent: dict, run_started: datetime, remaining: int, *, workspace=_WORKSPACE_UNSET
) -> None:
    from ..services import agent_service, billing_service, scoped_curation_service

    if workspace is _WORKSPACE_UNSET:
        workspace = await scoped_curation_service.workspace_for_agent(agent)
    if remaining <= 0 or workspace is None:
        return
    current = await agent_service.get_agent_by_id(UUID(str(agent["id"])))
    through = current["curated_through"]
    before = agent["curated_through"]
    if (
        current["run_mode"] != "scheduled"
        or current["last_run_outcome"] != "ran"
        or through is None
        or (
            before is not None
            and through <= before
            and current.get("curation_cursor") == agent.get("curation_cursor")
        )
        or not await billing_service.is_pro(UUID(str(current["user_id"])))
    ):
        return
    if not await _pending_input(current, workspace):
        return
    # Only successful, advancing, bounded batches can continue. No retry
    # chain on failure or permission revocation, and no paid personal runs.
    run_curator_now.apply_async(
        args=[str(agent["id"])],
        kwargs={"metered": False, "catchup_remaining": remaining - 1, "catchup": True},
        countdown=15,
    )


# During a scope's first day its wiki updates after every conversation, not
# just on the nightly tick — a user who just signed up (or a developer who
# just activated the platform) watches the wiki grow while they get set up.
# Debounced so a stream of event batches coalesces into at most one run per
# window; the agent's Redis turn lock already prevents overlapping runs.
FIRST_DAY_HOURS = 24
FIRST_DAY_DEBOUNCE = timedelta(minutes=10)


@celery.task(name="backend.tasks.agent_schedules.first_day_curator_tick")
def first_day_curator_tick(scope_user_id: str) -> None:
    run_async(_first_day_curator_tick(UUID(scope_user_id)))


def _within_first_day(created_at: datetime, now: datetime) -> bool:
    return created_at >= now - timedelta(hours=FIRST_DAY_HOURS)


async def _first_day_curator_tick(scope_user_id: UUID) -> None:
    from ..services import agent_service, end_user_service, user_service

    now = datetime.now(UTC)

    # Personal (and workspace-internal) Memory wiki, anchored to signup time.
    scope_user = await user_service.get_user_by_id(scope_user_id)
    if scope_user is not None and _within_first_day(scope_user["created_at"], now):
        agent = await agent_service.get_or_create_curator(scope_user_id)
        await _maybe_dispatch_first_day_run(scope_user_id, agent, now)

    # External cross-user wiki, anchored to developer-platform activation.
    workspace = await end_user_service.workspace_for_scope(scope_user_id)
    if (
        workspace is not None
        and workspace["external_wiki_folder_id"] is not None
        and _within_first_day(workspace["created_at"], now)
    ):
        agent = await agent_service.get_or_create_curator(scope_user_id, wiki="external")
        await _maybe_dispatch_first_day_run(scope_user_id, agent, now)


async def _maybe_dispatch_first_day_run(scope_user_id: UUID, agent: dict, now: datetime) -> None:
    from ..services import agent_auth, scoped_curation_service

    if agent["run_mode"] != "scheduled":
        return
    # A curator that has never run skips the debounce: its seeded last_run_at
    # is the backfill point (~account creation), which would otherwise mute
    # the very first conversations after signup.
    if (
        agent["last_run_outcome"] is not None
        and agent["last_run_at"]
        and agent["last_run_at"] > now - FIRST_DAY_DEBOUNCE
    ):
        return
    workspace = await scoped_curation_service.workspace_for_agent(agent)
    try:
        await scoped_curation_service.require_run_auth(agent, workspace=workspace)
    except (agent_auth.NeedsAuth, agent_auth.ProviderNotConfigured):
        return
    if not await _pending_input(agent, workspace):
        return
    # Unmetered: the platform is the trigger, so the run must not eat the
    # scope's free monthly curator allowance.
    run_curator_now.delay(str(agent["id"]), metered=False)


async def _run_due() -> int:
    from ..services import (
        agent_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d%H%M")
    dispatched = 0
    for agent in await agent_service.list_scheduled():
        if not _is_due(agent["schedule_cron"], agent["last_run_at"], now):
            continue
        workspace = await scoped_curation_service.workspace_for_agent(agent)
        try:
            async with _run_lock(agent, workspace):
                agent = await agent_service.get_agent_by_id(UUID(str(agent["id"])))
                if _is_due(agent["schedule_cron"], agent["last_run_at"], now):
                    dispatched += await _dispatch_due_agent(agent, stamp, workspace)
        except sprite_agent_service.TurnInProgress:
            continue
    return dispatched


async def _dispatch_due_agent(agent, stamp, workspace):
    from ..config import settings
    from ..services import agent_auth, agent_service, billing_service, scoped_curation_service

    user_id = UUID(str(agent["user_id"]))
    # Consume the tick up front so a skipped, slow, or failing run can't be
    # re-fired by the next beat. The curator's delta watermark is separate
    # (curated_through) and only advances after a successful run, so a
    # skipped or failed run never discards un-curated changes.
    month_runs = await agent_service.mark_run(agent["id"])
    # Sleep-time compute is metered: free accounts get a monthly curator
    # allowance; Pro and enterprise are unlimited.
    if (
        agent["is_curator"]
        and month_runs > settings.FREE_CURATOR_RUNS_PER_MONTH
        and not await billing_service.is_pro(user_id)
    ):
        logger.info("agent schedule: curator credits exhausted for user %s — skipping", user_id)
        await agent_service.mark_run_skipped(agent["id"], "credits")
        return 0
    # No runnable credential (unconnected free user) → nothing can run.
    try:
        await scoped_curation_service.require_run_auth(agent, workspace=workspace)
    except (agent_auth.NeedsAuth, agent_auth.ProviderNotConfigured):
        logger.info("agent schedule: no credential for agent %s — skipping", agent["id"])
        await agent_service.mark_run_skipped(agent["id"], "no_credential")
        return 0
    # Cost gate: skip the curator (and the sprite wake) when nothing changed
    # since its watermark. Idle users cost one EXISTS per day.
    if agent["is_curator"] and not await _pending_input(agent, workspace):
        await agent_service.mark_run_skipped(agent["id"], "no_changes")
        return 0
    run_scheduled_agent.delay(str(agent["id"]), stamp)
    return 1


async def _run_scheduled_agent(agent_id: UUID, stamp: str) -> None:
    from ..services import (
        agent_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    try:
        agent = await agent_service.get_agent_by_id(agent_id)
    except ValueError:
        # Deleted between the beat tick and this run — nothing to do, and no
        # agent row left to record a failure on.
        logger.info("agent schedule: agent %s deleted before its run", agent_id)
        return
    workspace = await scoped_curation_service.workspace_for_agent(agent)
    try:
        async with _run_lock(agent, workspace):
            agent = await agent_service.get_agent_by_id(agent_id)
            await _run_scheduled_locked(agent, stamp, workspace)
    except sprite_agent_service.TurnInProgress:
        if workspace is not None:
            # Upstream's scoped lane: this dispatch metered and ran nothing, so its
            # own credit comes back (`test_busy_scoped_curator_refunds_only_skipped_run`).
            await _skip_busy_curator(agent_id, metered=True)
            return
        # The personal lane's fence is ours, and the rule that comes with it: a
        # dispatch that never held the fence owns nothing to write, and the monthly
        # credit is one of those writes. The beat charged this tick when it dispatched,
        # and the run holding the fence is the one that spends it, so a refund here
        # takes back the price of a curation that is happening right now. A broker
        # requeue makes such a copy routine: measured on the founder stack 2026-10-01,
        # a 3600-second visibility timeout re-delivered a dispatch whose original was
        # 60 minutes into a 90-minute run.
        logger.info(
            "agent %s has a run in flight — this dispatch defers to it and writes nothing",
            agent_id,
        )


async def _run_scheduled_locked(agent, stamp, workspace):
    from ..database import get_pool
    from ..services import agent_service, alert_service, curation_service, sprite_agent_service

    agent_id = UUID(str(agent["id"]))
    if agent["run_mode"] != "scheduled":
        return
    user_id = UUID(str(agent["user_id"]))
    now = datetime.now(UTC)
    # The position this dispatch loaded IS the position its run reads from, so it is
    # captured before the run starts: the lanes that commit outside the run fence
    # their completion write on it, so the position written against and the position
    # read cannot drift; a move under the run fails loudly.
    read = curation_service.position_of(agent)
    try:
        await _run_turn(agent, stamp, workspace, read)
        if agent["is_curator"] and workspace is None:
            # `now` predates the run, so changes made during it stay ahead of
            # the watermark and are picked up next time. If the delta
            # overflowed the event cap, the watermark stops at the last event
            # that fit — the overflow drains on subsequent runs. Bookkeeping
            # failures share the run's try so they also record last_run_error
            # and alert, instead of dying as a bare task error. The completion
            # write is a compare-and-set on the position this run read, so an
            # ingest rewind or an overlapping run cannot be written over.
            through = await curation_service.complete_through(
                user_id,
                read,
                now,
                agent["curator_wiki"],
                agent.get("curator_folder_id"),
            )
            await agent_service.mark_curated(agent_id, read, through)
        await agent_service.mark_run_succeeded(agent_id)
    except Exception as e:
        if workspace is not None and isinstance(e, sprite_agent_service.TurnInProgress):
            await _skip_busy_curator(agent_id, metered=True)
            return
        logger.exception("agent schedule: run failed for agent %s", agent_id)
        await agent_service.mark_run_failed(agent_id, str(e))
        email = await get_pool().fetchval("SELECT email FROM users WHERE id = $1", user_id)
        # Asked before the call, never caught after it: a mode that sends no
        # operational alerts makes send_alert raise, and a raise here would
        # replace the failure this alert exists to report.
        if alert_service.alerts_enabled():
            await alert_service.send_alert(
                f"Scheduled agent run failed: {agent['name']!r} for {email}: {str(e)[:300]}"
            )
    else:
        await _dispatch_curator_catchup(agent, now, CURATOR_CATCHUP_PASSES - 1, workspace=workspace)


@celery.task(name="backend.tasks.agent_schedules.reap_stranded_runs")
def reap_stranded_runs() -> int:
    return run_async(_reap_stranded_runs())


async def _reap_stranded_runs() -> int:
    """Resolve lanes whose run died without resolving them, and free their locks.

    The in-process escape guard cannot catch every death: a child killed by the
    hard time limit, the memory-recycle, or a SIGKILL never returns from
    `run_async`, so nothing runs in it. This is the backstop for those, and it is
    a beat sweep precisely because it must fire with nobody alive to call it.

    The write is the same compare-and-set the guard uses — one statement for the
    whole sweep rather than a write per lane, and a lane that resolved itself
    between the read and the write is not touched. Locks are keyed by agent id,
    so freeing them needs no second query either.

    The allowance is deliberately not refunded: `mark_run` stores no metering
    flag, so a sweep cannot tell a credit the run was charged for from one the
    platform ran unmetered, and guessing would move a user's monthly allowance
    either way. The sweep repairs the lane, not the accounting, and its alert says
    so.

    Selection does not condition on the lane being runnable. A curator its owner
    paused between the beat's dispatch and the run's own gate is one of the lanes
    this exists to free, and waiting for it to look runnable again waits forever.
    Nor does it touch source sync lanes: those have their own termination path
    (`tasks/sources.py`, test_sync_lane_termination.py), and a second owner would
    only race it.
    """
    from ..database import get_pool
    from ..services import agent_service, alert_service, sprite_agent_service

    stale = await get_pool().fetch(
        "SELECT id FROM agents"
        " WHERE last_run_outcome = 'started'"
        "   AND last_run_at < now() - make_interval(secs => $1)",
        REAP_STRIKE_AGE_SECONDS,
    )
    if not stale:
        return 0

    cause = (
        "agent run never resolved its lane: no worker was left alive behind it, "
        "so the stranded-run sweep recorded it as failed"
    )
    flipped = await agent_service.resolve_started_runs(
        [row["id"] for row in stale], cause, metered=False
    )
    for lane in flipped:
        await sprite_agent_service.drop_agent_run_lock(lane["id"])
    # The resolution and the lock drop above are already durable, so a mode that
    # sends no operational alerts may not cost a lane its repair. The sweep's own
    # summary line below is the record in that mode.
    if alert_service.alerts_enabled():
        for lane in flipped:
            await alert_service.send_alert(
                f"Stranded agent run resolved: lane {lane['id']} ({lane['name']}) held "
                f"'started' past the {REAP_STRIKE_AGE_SECONDS}s run-lock TTL. Recorded as "
                f"failed and its run lock released; allowance untouched. Cause: {cause}"
            )
    logger.error("stranded-run sweep resolved %d lane(s): %s", len(flipped), cause)
    return len(flipped)


# Pending input older than this is stale even when individual batches succeed.
# One bad night must not page.
STALE_CURATOR_HOURS = 48


@celery.task(name="backend.tasks.agent_schedules.alert_stale_curators")
def alert_stale_curators() -> int:
    return run_async(_alert_stale_curators())


async def _alert_stale_curators() -> int:
    """Alert on curators whose watermark stopped advancing despite pending
    changes. Alerting on the stale *outcome* catches every cause — dead
    provider keys, harness bugs, a wedged beat — where per-run failure alerts
    only catch runs that started. Successful runs with an undrained backlog
    also qualify. Designed skips resolve explicitly and stay quiet.
    """
    from ..database import get_pool
    from ..services import alert_service

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=STALE_CURATOR_HOURS)
    rows = await get_pool().fetch(
        """
        SELECT a.*, u.email
        FROM agents a JOIN users u ON u.id = a.user_id
        WHERE a.is_curator AND a.run_mode = 'scheduled'
          AND a.curated_through IS NOT NULL AND a.curated_through < $1
          AND a.last_run_outcome IN ('started', 'failed', 'ran')
        """,
        cutoff,
    )
    from ..services import scoped_curation_service

    stale = []
    for row in rows:
        # Allow a slow cron its next scheduled run plus a day's grace. A daily
        # curator with an old undrained backlog still alerts after 48 hours.
        if row["last_run_outcome"] == "ran":
            try:
                due = croniter(row["schedule_cron"], row["curated_through"]).get_next(datetime)
                if now < due + timedelta(hours=24):
                    continue
            except (ValueError, KeyError, TypeError):
                pass
        workspace = await scoped_curation_service.workspace_for_agent(dict(row))
        if await _pending_input(dict(row), workspace):
            stale.append(row)
    if not stale:
        return 0
    lines = []
    for row in stale:
        outcome = {
            "ran": "backlog remains after a successful batch",
            "failed": "last run failed without an error message",
            "started": "run started but never resolved",
        }[row["last_run_outcome"]]
        detail = row["last_run_error"] or outcome
        lines.append(
            f"- {row['email']}: last curated {row['curated_through']:%Y-%m-%d %H:%M} UTC "
            f"({detail[:200]})"
        )
    # Asked before the call, never caught after it: a mode that sends no
    # operational alerts makes send_alert raise, and a raise here would
    # replace the failure this alert exists to report.
    report = (
        f"{len(stale)} Memory curator(s) stale >{STALE_CURATOR_HOURS}h with pending changes:\n"
        + "\n".join(lines)
    )
    # The record comes first and in every mode: a watchdog that only speaks by
    # delivering goes silent on exactly the installations that never deliver. The
    # cause it names is already on the agent row (`mark_run_failed`), so the row
    # needs no second write.
    logger.error("stale curator watchdog: %s", report)
    if alert_service.alerts_enabled():
        await alert_service.send_alert(report)
    return len(stale)
