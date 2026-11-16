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
import time
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
# next 500 inputs, or monopolize one worker indefinitely. This constant is the
# FLOOR of the bound actually applied; read `curator_catchup_pass_ceiling()`,
# never this, from any code that has to decide whether a pass may run.
CURATOR_CATCHUP_PASSES = 8


def curator_catchup_pass_ceiling() -> int:
    """Catch-up passes one chain may plan, and one lane may claim per 24h.

    The month's curator allowance spread across the month that funds it, never
    below the floor above. Two consequences worth saying out loud:

    At the code default (`FREE_CURATOR_RUNS_PER_MONTH` = 10, which floors at 10
    // 31 = 0) the result is exactly `CURATOR_CATCHUP_PASSES`, so a default
    install is unchanged run for run. Only a deployment that raised its own
    allowance gets a wider cadence, and it gets it inside the allowance it
    already declared — this never raises the entitlement.

    This bound, not `month_run_count`, is what limits what the chain spends: the
    chain dispatches with `metered=False`, so its runs never touch the month
    counter the beat gates on. Raising the ceiling therefore costs real model
    calls that the allowance figure will not show.
    """
    from ..config import settings

    return max(CURATOR_CATCHUP_PASSES, settings.FREE_CURATOR_RUNS_PER_MONTH // 31)


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
def run_scheduled_agent(agent_id: str, stamp: str, metered: bool = True) -> None:
    # The beat charged this tick only if it dispatched it, and only metered the
    # lane the platform pays for (`metered` is that answer, resolved from the
    # credential the run dials). Everything downstream that can refund has to be
    # told which of the two this was, or an unmetered local lane's crash would
    # hand back a credit that was never taken.
    agent_uuid = UUID(agent_id)
    _run_guarded(agent_uuid, metered, _run_scheduled_agent(agent_uuid, stamp, metered))


@celery.task(name="backend.tasks.agent_schedules.run_curator_now")
def run_curator_now(
    agent_id: str,
    full_history: bool = False,
    metered: bool = True,
    catchup_remaining: int | None = None,
    catchup: bool = False,
    platform_initiated: bool = False,
) -> None:
    agent_uuid = UUID(agent_id)
    _run_guarded(
        agent_uuid,
        metered,
        _run_curator_now(
            agent_uuid,
            full_history,
            metered,
            catchup_remaining,
            catchup,
            platform_initiated,
        ),
    )


async def _run_curator_now(
    agent_id: UUID,
    full_history: bool = False,
    metered: bool = True,
    catchup_remaining: int | None = None,
    catchup: bool = False,
    platform_initiated: bool = False,
) -> None:
    """A user-requested curator run: same execution as the daily tick, minus
    the due-check — the user is the trigger. The router already enforced the
    free-tier allowance and resolved credentials.

    `catchup_remaining` defaults to the live entitlement rather than to a
    constant, because a default argument is frozen at import: a manual run would
    otherwise chain under the import-time bound while the beat chained under the
    setting, which is two behaviours from one knob.

    `full_history` is the backfill: the run reads with no watermark, but the
    stored watermark is only advanced after success — a failed backfill must
    not have thrown away the incremental position.

    `metered=False` means the platform owes nothing for this tick, either because
    it initiated the run or because the lane dials the customer's own endpoint
    (`agent_auth.costs_platform_tokens`).

    `platform_initiated=True` says who triggered the run, which is what the
    switched-off-curator guard actually asks. It used to infer that from
    `metered=False`, and the two come apart as soon as a user-triggered run on a
    local endpoint is unmetered: the inference would answer "success" and run
    nothing, which is the silent no-op this card exists to remove."""
    from ..services import (
        agent_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    agent = await agent_service.get_agent_by_id(agent_id)
    workspace = await scoped_curation_service.workspace_for_agent(agent)
    if catchup_remaining is None:
        catchup_remaining = curator_catchup_pass_ceiling() - 1
    try:
        async with _run_lock(agent, workspace):
            agent = await agent_service.get_agent_by_id(agent_id)
            await _run_curator_locked(
                agent,
                workspace,
                full_history,
                metered,
                catchup_remaining,
                catchup,
                platform_initiated,
            )
    except sprite_agent_service.TurnInProgress:
        # No mark_run and no charge happened: leave the active run untouched.
        logger.info("agent schedule: curator %s is busy; skipping", agent_id)


def _run_lock(agent: dict, workspace):
    from ..services import agent_service, sprite_agent_service

    if workspace is not None:
        # The developer-platform lane: this key is the fence its commit re-enters
        # under `scoped_lock_held`, so the name is upstream's and stays upstream's.
        # The TTL is that lane's own ceiling — the same number its `wait_for` wall
        # uses, resolved by the same resolver — because a lock that expired first
        # would let a second dispatch into a run still writing the wiki.
        # The TTL and the wall are deliberately two different numbers now: the wall
        # is the ceiling MINUS `RUN_COMMIT_RESERVE_SECONDS`, so a run stops and
        # commits before its fence expires, and shortening the fence to match the
        # wall would open the fence exactly while the commit is still in flight.
        return sprite_agent_service._TurnLock(
            f"scoped-curator-{agent['id']}", agent_service.turn_ceiling_seconds(agent)
        )
    # The personal-wiki lane has no other single-flight guard: its turn lock is
    # keyed by a per-run session id, so two runs of one agent never contend on it,
    # and the watermark CAS only refuses the clobber — it cannot stop a second paid
    # harness turn being spent on material the run in flight already read.
    # Upstream's body reads as if a fence were here (`curator is busy; skipping`);
    # this lock is that fence. Its TTL is the longest any task may legally take, so
    # it is never released while its own run is still executing.
    return sprite_agent_service.agent_run_lock(agent["id"], REAP_STRIKE_AGE_SECONDS)


async def _run_turn(agent, stamp, workspace, read, draining: bool = False):
    from ..services import sprite_agent_service

    if workspace is None:
        return await sprite_agent_service.run_scheduled(agent, stamp, read, draining=draining)
    return await sprite_agent_service.run_scheduled(
        agent, stamp, read, workspace=workspace, scoped_lock_held=True, draining=draining
    )


async def _pending_input(agent: dict, workspace) -> bool:
    from ..services import curation_service, scoped_curation_inputs

    if workspace is not None:
        return await scoped_curation_inputs.has_pending(agent, workspace)
    owner = UUID(str(agent["user_id"]))
    # A drain in flight owns the answer: its backlog is what is pending, and the watermark
    # says nothing about how much of it is left. No drain pair means no drain, and the
    # question stays the one it always was — the release-0 check on a curator that has
    # never run answers through this same unmodified path.
    position = curation_service.readout_position_of(agent)
    return await curation_service.has_changes_since(owner, owner, position, agent["curator_wiki"])


async def _commit_personal_drain(agent, watermark_read, drain, through) -> None:
    """Commit where one personal-lane pass read, on the drain's own pair.

    The personal lane has no checkpoint inside the turn — one turn reads one batch — so the
    boundary the run earned IS its committed pass. It then moves the same fenced primitive
    the scoped lane moves per pass, so neither lane can advance a drain the other holds,
    and the watermark is not written here: this run read from before it, and
    `mark_curated` would refuse that as a regression, which is the log line this card was
    opened on.

    A boundary that stopped INSIDE the instant the drain stood on cannot be stored: the
    pair has no event half, so writing the instant would claim the whole tie was read.
    That is one instant's events outweighing one pass's budget, and it raises with the
    ceiling named rather than leaving a plateau for the next dispatch to repeat.
    """
    from ..database import get_pool
    from ..services import agent_service

    agent_id = UUID(str(agent["id"]))
    read_at, read_cursor = drain if drain is not None else (None, {})
    if through.at is None:
        raise RuntimeError(
            f"curator {agent_id} drain committed no position: the feed read nothing and "
            "offered no boundary to advance to"
        )
    if read_at is not None and through.at <= read_at:
        raise RuntimeError(
            f"curator {agent_id} cannot drain past {read_at}: the events packed into that"
            " one instant outweigh what one pass of this run's input budget can hold, and"
            " the drain pair cannot record a position inside an instant — raise the"
            " curator's turn ceiling"
        )
    async with get_pool().acquire() as conn, conn.transaction():
        await agent_service.advance_drain(conn, agent_id, read_at, read_cursor, through.at, {})
        if not await _pending_input(
            {**agent, "drain_through": through.at, "drain_cursor": {}}, None
        ):
            # fenced on where this transaction just PUT the drain, not on the pair the pass
            # read: the advance above moved it, and a hand-off fenced on the old pair would
            # refuse the run's own commit — the scoped lane updates its `drain` after every
            # checkpoint for exactly this reason, and the personal lane has its checkpoint
            # and its hand-off inside one function.
            await agent_service.finish_drain(
                conn, agent_id, through.at, {}, watermark_read, through
            )

    # The drain-rate meter, now on the personal lane too. `curator pass=` was scoped-only,
    # so a personal-lane run that committed a pass left no per-pass line and its progress
    # was only readable from the row (the reason AC (a) was unobservable on the copy, whose
    # curator runs on this lane). One line per committed drain pass; the personal lane has
    # one turn and so one commit, hence pass=1, and it reports only what this function
    # actually knows - the boundary and the lane. No `characters=` or `scopes=`: this lane
    # never counted them. The prefix `curator pass=` never contains `curator <id> turn `, so
    # `_turn_bracket`'s record filter cannot pick it up.
    logger.info(
        "curator pass=1 agent=%s committed_through=%s lane=personal",
        agent_id,
        through.at.isoformat() if through.at is not None else None,
    )


async def _run_curator_locked(
    agent, workspace, full_history, metered, catchup_remaining, catchup, platform_initiated
):
    from ..services import (
        agent_service,
        curation_service,
        sprite_agent_service,
    )

    agent_id = UUID(str(agent["id"]))
    # A platform-triggered pass (first-day, catch-up) is only for a curator the
    # user left switched on. The question is the TRIGGER, so it reads the trigger:
    # inferring it from `metered` would misanswer the moment a user-triggered run
    # is unmetered for costing the platform nothing, turning a legitimate manual
    # run into a task that reports `started` and does nothing.
    if platform_initiated and agent["run_mode"] != "scheduled":
        logger.info(
            "agent schedule: curator %s declined its pass guard=curator_off run_mode=%s",
            agent_id,
            agent["run_mode"],
        )
        return
    if catchup:
        # `_chain_permitted` writes its own line for the cost question it answers, so the
        # guards that remain here are the ones that would otherwise return in silence.
        if not await _chain_permitted(agent, workspace):
            return
        if not await _pending_input(agent, workspace):
            logger.info(
                "agent schedule: curator %s declined its catch-up pass guard=no_pending_input",
                agent_id,
            )
            return
        claimed_through = curator_catchup_pass_ceiling() - 1
        if not await agent_service.claim_catchup(agent_id, claimed_through):
            logger.info(
                "agent schedule: curator %s declined its catch-up pass guard=catchup_ceiling"
                " claimed_through=%s",
                agent_id,
                claimed_through,
            )
            return
    # The compare-and-set anchor for the watermark is the stored position this run
    # actually loaded — captured BEFORE the drain pair below replaces what the run READS,
    # which must not change what the run may write against.
    read = curation_service.position_of(agent)
    # A history drain reads and writes its own resumable pair, never the watermark: the
    # backlog it works through sits behind a watermark that is monotone by contract, so a
    # run that checkpointed into the watermark would be refused by the very clamp that
    # makes the watermark safe, and would re-read its first slice on every dispatch until
    # the worker's soft time limit killed it. A dispatch that finds a pair already set
    # resumes that pair: seeding a drain in flight back to its beginning is the duplicate
    # read of the same slice that this whole mechanism exists to make impossible. And a
    # pair that has committed nothing carries no position to resume from, so the seed is
    # the ABSENCE of a pair and needs no write that could itself be mistaken for progress.
    drain = curation_service.drain_of(agent)
    if full_history and drain is not None:
        logger.info(
            "agent schedule: curator %s resumes the history drain it already has at %s"
            " instead of re-zeroing it",
            agent_id,
            drain[0],
        )
    draining = full_history or drain is not None
    if draining:
        # The pair this run reads and commits. A seeded drain has none yet, and "none yet"
        # means the whole feed: a backfill starts before the first event ever written.
        drain_at, drain_cursor = drain if drain is not None else (None, {})
        agent = {
            **agent,
            "curated_through": drain_at,
            "curated_through_event_id": None,
            "curation_cursor": drain_cursor,
        }
    now = datetime.now(UTC)
    await agent_service.mark_run(agent_id, metered=metered)
    # One line at each end of a turn. The personal-wiki lane's own body writes no INFO
    # record at all, so a run on that lane produced nothing for its whole duration and
    # a 24-minute backfill read as a stalled one. The lane is named because the two
    # lanes owe different follow-up lines: only the scoped one emits the per-pass
    # drain meter.
    lane = "scoped" if workspace is not None else "personal"
    logger.info(
        "agent schedule: curator %s turn started lane=%s full_history=%s metered=%s",
        agent_id,
        lane,
        full_history,
        metered,
    )
    try:
        # Seconds-resolution stamp so a manual run never shares a session with
        # the beat's minute-stamped run.
        stamp = now.strftime("%Y%m%d%H%M%S")
        started = time.monotonic()
        await _run_turn(agent, stamp, workspace, read, draining)
        if workspace is None:
            # The boundary is computed from the position the run ACTUALLY read from,
            # which a drain moved to its own pair: a backfill counts the whole corpus as
            # its backlog, so its overflow boundary sits under the stored cursor and the
            # clamp keeps the cursor where an earlier run left it. The completion write is
            # a compare-and-set on `read`, so an ingest rewind or an overlapping run cannot
            # be written over.
            through = await curation_service.complete_through(
                UUID(str(agent["user_id"])),
                curation_service.position_of(agent),
                now,
                agent["curator_wiki"],
                agent.get("curator_folder_id"),
                # A drain is bounded to what its own wall can finish, by the same budget
                # its feed was cut by; an incremental batch keeps the bound it has always
                # had (`_MAX_EVENTS`), which reaches its wall.
                max_event_chars=(curation_service.drain_budget_of(agent) if draining else None),
            )
            if draining:
                await _commit_personal_drain(agent, read, drain, through)
            else:
                await agent_service.mark_curated(agent_id, read, through)
        await agent_service.mark_run_succeeded(agent_id)
        logger.info(
            "agent schedule: curator %s turn finished lane=%s wall=%.1fs",
            agent_id,
            lane,
            time.monotonic() - started,
        )
    except Exception as e:
        if workspace is not None and isinstance(e, sprite_agent_service.TurnInProgress):
            await _skip_busy_curator(agent_id, metered=metered)
            return
        await agent_service.mark_run_failed(agent_id, str(e), metered=metered)
        if isinstance(e, sprite_agent_service.RunCeilingExceeded):
            # The run's own ceiling ended it, not a broken curator. Each pass it
            # finished is already durable, so the backlog it did not reach goes to the
            # next catch-up pass while that chain still has bound left - and only while
            # the lane actually advanced, which the progress gate below answers. The
            # failure stands: the row still reports `failed` and the task still raises.
            await _dispatch_curator_catchup(
                agent,
                now,
                catchup_remaining,
                workspace=workspace,
                after_ceiling=True,
                drained=draining,
            )
        raise
    await _dispatch_curator_catchup(
        agent, now, catchup_remaining, workspace=workspace, drained=draining
    )


async def _skip_busy_curator(agent_id: UUID, *, metered: bool) -> None:
    """The active run owns the shared outcome; refund only this skipped charge."""
    from ..services import agent_service

    if metered:
        await agent_service.refund_run(agent_id)
    logger.info(
        "agent schedule: curator %s is already running; skipping overlapping pass", agent_id
    )


_WORKSPACE_UNSET = object()


async def _chain_permitted(agent: dict, workspace) -> bool:
    """May this curator's chain continue to another pass, and one line saying why not.

    Permission is the COST of the next pass, not the plan of its owner. What the free
    monthly allowance rations is platform tokens, and `require_run_auth` is the single
    source of truth for whether this lane spends any: a chained pass on an endpoint the
    customer pays for themselves costs the platform nothing, so a `plan='free'` account
    draining months of history gets its chain, while a lane that does spend platform
    tokens stays unchained unless its owner genuinely has an unlimited plan. Which is why
    no lane test stands here: the metered answer is the whole predicate, and a check on
    `workspace is None` would be a second, dumber answer to the same question — the one
    that denied a founder account the only drain its own box could have paid for.

    The order is the one `_dispatch_due_agent` records as mechanical: the credential is
    resolved first, and its raise for a lane that cannot run at all (`NeedsAuth`,
    `ProviderNotConfigured`) is kept and deliberately not swallowed here — a lane that
    cannot run is not a lane that was declined for cost, and the two must not read alike.

    The line is AC (d)'s accounting record: both inputs and the decision, so a reader of
    the log can tell a declined chain from a stalled one without the row. A decline also
    carries the `guard=` token every other silent exit of this module prints, so the one
    guard that was never silent still reads alike; `not_pro` is the token for "the pass
    spends platform tokens and its owner has no unlimited plan", which is exactly the
    decline this predicate makes. The prose is phrased to collide with neither existing
    assertion on log prose (no `not moved`, and it does not begin `curator pass=`).
    """
    from ..services import billing_service, scoped_curation_service

    metered = await scoped_curation_service.require_run_auth(agent, workspace=workspace)
    pro = await billing_service.is_pro(UUID(str(agent["user_id"])))
    allowed = not metered or pro
    logger.info(
        "agent schedule: curator %s may chain its next pass: metered=%s is_pro=%s -> %s",
        agent["id"],
        metered,
        pro,
        (
            "chaining"
            if allowed
            else "declining, this pass would spend what the allowance rations guard=not_pro"
        ),
    )
    return allowed


async def _dispatch_curator_catchup(
    agent: dict,
    run_started: datetime,
    remaining: int,
    *,
    workspace=_WORKSPACE_UNSET,
    after_ceiling: bool = False,
    drained: bool = False,
) -> None:
    """A dispatch failure must not undo the work the run left behind.

    `after_ceiling` says the run ended on its own ceiling rather than on a broken
    curator, which is the one case where the outcome row says `failed` and the chain
    still has to continue: the passes under that failure completed and checkpointed.

    `drained` says the run was working a history drain, so the progress the chain must
    see is the drain pair's, not the watermark's.
    """
    from ..services import alert_service

    try:
        await _queue_curator_catchup(
            agent,
            run_started,
            remaining,
            workspace=workspace,
            after_ceiling=after_ceiling,
            drained=drained,
        )
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
    agent: dict,
    run_started: datetime,
    remaining: int,
    *,
    workspace=_WORKSPACE_UNSET,
    after_ceiling: bool = False,
    drained: bool = False,
) -> None:
    from ..services import agent_service, curation_service, scoped_curation_service

    if workspace is _WORKSPACE_UNSET:
        workspace = await scoped_curation_service.workspace_for_agent(agent)
    current = await agent_service.get_agent_by_id(UUID(str(agent["id"])))
    if drained:
        # A draining run progressed on its drain pair, and the watermark says nothing
        # about it: the backlog being drained sits BEHIND that watermark by design, so a
        # gate watching the watermark would read a lane that just committed a pass as one
        # that got nowhere and stop the chain on the pass that needed the next one most.
        progressed = (current["drain_through"], current.get("drain_cursor") or {}) != (
            agent["drain_through"],
            agent.get("drain_cursor") or {},
        ) and curation_service.drain_of(current) is not None
    else:
        through = current["curated_through"]
        before = agent["curated_through"]
        progressed = not (
            through is None
            or (
                before is not None
                and through <= before
                and current.get("curation_cursor") == agent.get("curation_cursor")
            )
        )
    # Sequential rather than one compound `or`, in the same order, so the condition that
    # ended the chain is the condition the log names. A chain stopped by a lapsed Pro
    # entitlement and a chain that finished must not read as the same line.
    if current["run_mode"] != "scheduled":
        logger.info(
            "agent schedule: curator %s catch-up chain stopped guard=curator_off run_mode=%s",
            agent["id"],
            current["run_mode"],
        )
        return
    # A failure ends the chain, except the failure a ceiling causes: that one has
    # completed passes behind it and unread material in front of it.
    if current["last_run_outcome"] != "ran" and not after_ceiling:
        logger.info(
            "agent schedule: curator %s catch-up chain stopped guard=outcome_not_ran outcome=%s",
            agent["id"],
            current["last_run_outcome"],
        )
        return
    # A missing checkpoint is the run that committed no pass, so it is the same stop as a
    # checkpoint that did not move: either way there is no progress to chain onto.
    if not progressed:
        logger.info(
            "agent schedule: curator %s catch-up chain stopped guard=no_progress", agent["id"]
        )
        return
    # What the next pass costs is `_chain_permitted`'s question, and it writes its own
    # line carrying both answers and the decision it made from them.
    if not await _chain_permitted(current, workspace):
        return
    if not await _pending_input(current, workspace):
        logger.info(
            "agent schedule: curator %s catch-up chain stopped guard=no_pending_input",
            agent["id"],
        )
        return
    if remaining <= 0:
        # Loud, because this is the one bound that silently stops a curator from ever
        # catching up: the lane is still behind and nothing else will re-queue it until
        # the next scheduled tick.
        logger.warning(
            "agent schedule: curator %s exhausted its %s catch-up passes with input still"
            " pending; the checkpoint holds the rest for the next scheduled run",
            agent["id"],
            curator_catchup_pass_ceiling(),
        )
        return
    # Advancing and bounded are what a chain always required, and the ceiling case keeps
    # both: the progress gate above refuses a cut run that checkpointed no pass, so a
    # lane that cannot get through even one pass is never turned into a chain of paid
    # retries. Permission revocation still ends it, and no personal run is paid here.
    # A lane that stays closed on a free account is closed by that predicate, not by a
    # test of which lane it is: `_chain_permitted` asks what the next pass costs, and a
    # personal run dialing the customer's own endpoint answers "nothing the allowance
    # rations", which is why a founder-account drain of months of history chains at all.
    # `metered=False` here because the PLATFORM is the trigger for a follow-on
    # pass, not because of which endpoint it dials; `platform_initiated` below is
    # that trigger, said in the open. Metering this chain is deferred to the
    # first paid tier by operator directive (STAS-423).
    run_curator_now.apply_async(
        args=[str(agent["id"])],
        kwargs={
            "metered": False,
            "platform_initiated": True,
            "catchup_remaining": remaining - 1,
            "catchup": True,
        },
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
    # Unmetered, and platform-triggered, for the same reason: the platform is the
    # trigger, so the run must not eat the scope's free monthly curator allowance,
    # and only a curator the user left switched on may be run at all. The `await`
    # above is taken for its side effect (refuse an unrunnable lane); the metering
    # answer is not consulted because this trigger is unmetered either way.
    run_curator_now.delay(str(agent["id"]), metered=False, platform_initiated=True)


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
    # Resolve the credential first, before any gate and before any counter. That
    # order is mechanical, not stylistic: the allowance gate may only be applied
    # to a lane the platform pays tokens for, so the metering answer has to exist
    # before the gate can be evaluated at all. It also changes which reason a
    # tick reports when BOTH states are true (a spent allowance and no runnable
    # credential): the row now says `skipped_no_credential` where it said
    # `skipped_credits`, because the credential is the actionable one. Neither
    # state moves the counter. Pinned by AC10.
    try:
        metered = await scoped_curation_service.require_run_auth(agent, workspace=workspace)
    except (agent_auth.NeedsAuth, agent_auth.ProviderNotConfigured):
        # WARNING, not INFO: this skip recurs every tick forever, INFO drowns
        # in a beat that ticks every minute, and `docker compose logs` is the
        # first place a self-hoster looks. The row shows the same block.
        logger.warning(
            "agent schedule: no runnable model for agent %s — every tick will skip until "
            "ANTHROPIC_API_KEY is set on the backend or a model endpoint is connected "
            "under Settings → Cloud agent model",
            agent["id"],
        )
        await agent_service.mark_run_skipped(agent["id"], "no_credential")
        return 0
    # Sleep-time compute is metered: free accounts get a monthly curator
    # allowance; Pro and enterprise are unlimited. A lane on the customer's own
    # endpoint costs the platform no tokens, so it has no allowance to spend and
    # this gate does not apply to it.
    if (
        agent["is_curator"]
        and metered
        and agent_service.free_allowance_spent(agent)
        and not await billing_service.is_pro(user_id)
    ):
        # WARNING, for the same reason as the no-credential skip below: this fires
        # once per tick in a beat that ticks every minute, and INFO there is
        # unreadable on the self-hosted box where `docker compose logs` is the only
        # console. The difference from that skip is that this one is a spend limit
        # the operator can lift, and the lane behind it is a backlog that will never
        # drain while it stands — so both numbers are named, not just the fact.
        # The count printed is the one that WORKED, not a value this tick moved.
        logger.warning(
            "agent schedule: curator monthly allowance spent for user %s (%s of %s runs used "
            "this month) — every tick will skip until the allowance is raised or the account "
            "goes Pro",
            user_id,
            agent_service.month_runs_used(agent),
            settings.FREE_CURATOR_RUNS_PER_MONTH,
        )
        await agent_service.mark_run_skipped(agent["id"], "credits")
        return 0
    # Cost gate: skip the curator (and the sprite wake) when nothing changed
    # since its watermark. Idle users cost one EXISTS per day.
    if agent["is_curator"] and not await _pending_input(agent, workspace):
        await agent_service.mark_run_skipped(agent["id"], "no_changes")
        return 0
    # The only path that charges: this tick dispatched a real run, so it consumes
    # the clock slot and, on a metered lane, one run of the allowance. Every skip
    # above resolved its tick through `mark_run_skipped` and touched no counter.
    # This applies to every due agent, curator or not — the counter means worked
    # runs for all of them; only the allowance GATE stays curator-only.
    await agent_service.mark_run(agent["id"], metered=metered)
    run_scheduled_agent.delay(str(agent["id"]), stamp, metered=metered)
    return 1


async def _run_scheduled_agent(agent_id: UUID, stamp: str, metered: bool = True) -> None:
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
            await _run_scheduled_locked(agent, stamp, workspace, metered)
    except sprite_agent_service.TurnInProgress:
        if workspace is not None:
            # Upstream's scoped lane: this dispatch charged and ran nothing, so its
            # own credit comes back (`test_busy_scoped_curator_refunds_only_skipped_run`).
            # A scoped lane is metered by definition (the backend's key pays), but the
            # value travels rather than being re-guessed here.
            await _skip_busy_curator(agent_id, metered=metered)
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


async def _run_scheduled_locked(agent, stamp, workspace, metered: bool = True):
    from ..database import get_pool
    from ..services import agent_service, alert_service, curation_service, sprite_agent_service

    agent_id = UUID(str(agent["id"]))
    if agent["run_mode"] != "scheduled":
        # The beat charged and stamped this tick before it dispatched it, and a clean
        # return out of here is exactly what `_run_guarded` reads as success: the row would
        # keep `started`, its credit would stay spent, `last_run_error` would stay NULL and
        # nothing would log — the only post-charge exit on this file with no owner for its
        # row. So the tick resolves itself, on the same compare-and-set the escape guard
        # uses (it cannot stamp a row another arm already resolved, and it refunds inside
        # the same write, which is why the metered answer travels rather than being
        # re-guessed). Deliberately NOT the body's `except Exception`: that one alerts, and
        # an agent switched to manual mid-dispatch is not an outage.
        cause = f"agent run resolved without running: run_mode={agent['run_mode']}"
        flipped = await agent_service.resolve_started_runs([agent_id], cause, metered=metered)
        if flipped:
            logger.error(
                "agent schedule: agent %s was dispatched after its run mode changed: %s",
                agent_id,
                cause,
            )
        return
    user_id = UUID(str(agent["user_id"]))
    now = datetime.now(UTC)
    # The position this dispatch loaded IS the position its run reads from, so it is
    # captured before the run starts: the lanes that commit outside the run fence
    # their completion write on it, so the position written against and the position
    # read cannot drift; a move under the run fails loudly.
    read = curation_service.position_of(agent)
    # A history drain left open by an earlier dispatch owns the next run's read too, and
    # the beat's tick is such a next run: it must commit on the drain pair rather than
    # `mark_curated`, whose monotone clamp would refuse a boundary read from behind the
    # watermark and log the drain's progress as a discarded run.
    drain = curation_service.drain_of(agent)
    if drain is not None:
        agent = {
            **agent,
            "curated_through": drain[0],
            "curated_through_event_id": None,
            "curation_cursor": drain[1],
        }
    try:
        await _run_turn(agent, stamp, workspace, read, drain is not None)
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
                curation_service.position_of(agent),
                now,
                agent["curator_wiki"],
                agent.get("curator_folder_id"),
                # The beat's tick into a drain in flight is a drain pass: it is cut by the
                # same budget, or its boundary would stand further ahead than the feed it
                # was handed and the drain would claim characters nobody showed the model.
                max_event_chars=(
                    curation_service.drain_budget_of(agent) if drain is not None else None
                ),
            )
            if drain is not None:
                await _commit_personal_drain(agent, read, drain, through)
            else:
                await agent_service.mark_curated(agent_id, read, through)
        await agent_service.mark_run_succeeded(agent_id)
    except Exception as e:
        if workspace is not None and isinstance(e, sprite_agent_service.TurnInProgress):
            await _skip_busy_curator(agent_id, metered=metered)
            return
        logger.exception("agent schedule: run failed for agent %s", agent_id)
        await agent_service.mark_run_failed(agent_id, str(e), metered=metered)
        email = await get_pool().fetchval("SELECT email FROM users WHERE id = $1", user_id)
        # Asked before the call, never caught after it: a mode that sends no
        # operational alerts makes send_alert raise, and a raise here would
        # replace the failure this alert exists to report.
        if alert_service.alerts_enabled():
            await alert_service.send_alert(
                f"Scheduled agent run failed: {agent['name']!r} for {email}: {str(e)[:300]}"
            )
    else:
        await _dispatch_curator_catchup(
            agent,
            now,
            curator_catchup_pass_ceiling() - 1,
            workspace=workspace,
            drained=drain is not None,
        )


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
    also qualify. A designed skip stays quiet *as a skip*: the row resolves
    explicitly and names its reason, and no page is raised for it. The one
    exception is the monthly-allowance stop, which is a skip the row resolves
    correctly and the drain cannot survive: a lane frozen there looks healthy
    forever, so the stale watermark now reports it as allowance exhaustion.
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
          AND a.last_run_outcome IN ('started', 'failed', 'ran', 'skipped_credits')
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
        if row["last_run_outcome"] == "skipped_credits":
            # Named by its allowance, not by `last_run_error`: `mark_run_skipped`
            # writes the outcome and nothing else, so the error column still holds
            # whatever an unrelated earlier run left in it. The CEO decision this
            # line exists to trigger needs the size of what is frozen beside the
            # number of runs spent, read for this lane's own wiki and folder and at the
            # position the lane is actually live on — a drain that has been committing
            # for a month has a different remainder than the watermark advertises.
            from ..config import settings
            from ..services import agent_service, curation_service

            backlog = await curation_service.curator_readout(
                UUID(str(row["user_id"])),
                dict(row),
                folder_id=row["curator_folder_id"],
            )
            detail = (
                f"curator credits spent: monthly allowance exhausted, "
                f"{agent_service.month_runs_used(dict(row))} of "
                f"{settings.FREE_CURATOR_RUNS_PER_MONTH} runs used; "
                f"{backlog['distinct_events']} distinct events pending "
                f"({backlog['raw_rows']} raw rows) counted from {backlog['read_from']}"
                f"{', a history drain rather than the watermark' if backlog['draining'] else ''}"
                f" — dispatch cannot resume until the "
                f"allowance is raised or the account goes Pro"
            )
        else:
            outcome = {
                "ran": "backlog remains after a successful batch",
                "failed": "last run failed without an error message",
                "started": "run started but never resolved",
            }[row["last_run_outcome"]]
            detail = (row["last_run_error"] or outcome)[:200]
        lines.append(
            f"- {row['email']}: last curated {row['curated_through']:%Y-%m-%d %H:%M} UTC ({detail})"
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
