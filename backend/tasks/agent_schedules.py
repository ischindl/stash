"""Run scheduled agents on their cron.

The beat task fires every minute and is a pure dispatcher: it finds agents
whose cron tick is due, consumes the tick, applies the cheap gates (credits,
credential, pending changes), and hands each eligible run to
`run_scheduled_agent` on the heavy queue — a headless agent turn runs for
minutes and must not hold a default-queue slot.

One run per agent at a time, enforced by `agent_run_lock` (Redis, keyed by
agent id) at the task entry points. The per-session turn lock is not that
lock: a scheduled run's session id carries a per-run stamp, so two runs of the
same agent never contend on it — which is how two overlapping curator runs
came to discard each other's finished curation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from croniter import croniter

from ..celery_app import celery
from ._celery_helpers import run_async

logger = logging.getLogger(__name__)

# A harness agent run (PI curation) routinely takes 13+ minutes and can
# exceed 25 minutes on a large delta — the global 1500s soft / 1800s hard
# ceiling (celery_app.py) was killing them mid-run: partial wiki writes
# survive, but the run is marked failed and the watermark does not advance,
# so the next run re-reads the same delta. These two tasks are the only
# Celery tasks that run a harness agent; they carry their own limits so the
# global stays the ceiling for everything else.
HARNESS_SOFT_TIME_LIMIT = 5400  # 90 min
HARNESS_TIME_LIMIT = 5700  # 95 min

# The single-flight run lock has to outlive the limit that kills the run, or an
# honest long run loses its lock mid-turn and a second dispatch joins it. The
# margin covers the gap between the hard timeout firing and the worker exiting;
# a worker killed before its `finally` releases the lock on this TTL rather
# than wedging the agent's schedule forever.
AGENT_RUN_LOCK_TTL = HARNESS_TIME_LIMIT + 900


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


@celery.task(name="backend.tasks.agent_schedules.run_due")
def run_due() -> int:
    return run_async(_run_due())


@celery.task(
    name="backend.tasks.agent_schedules.run_scheduled_agent",
    soft_time_limit=HARNESS_SOFT_TIME_LIMIT,
    time_limit=HARNESS_TIME_LIMIT,
)
def run_scheduled_agent(agent_id: str, stamp: str) -> None:
    run_async(_run_scheduled_agent(UUID(agent_id), stamp))


@celery.task(
    name="backend.tasks.agent_schedules.run_curator_now",
    soft_time_limit=HARNESS_SOFT_TIME_LIMIT,
    time_limit=HARNESS_TIME_LIMIT,
)
def run_curator_now(agent_id: str, full_history: bool = False, metered: bool = True) -> None:
    run_async(_run_curator_now(UUID(agent_id), full_history, metered))


async def _run_curator_now(
    agent_id: UUID, full_history: bool = False, metered: bool = True
) -> None:
    """A user-requested curator run: same execution as the daily tick, minus
    the due-check — the user is the trigger. The router already enforced the
    free-tier allowance and resolved credentials.

    `full_history` is the backfill: the run reads with no watermark, so the
    prompt bootstraps from everything ever uploaded. It never clears the stored
    watermark — a failed backfill keeps the incremental position, and because
    the advance is a monotonic compare-and-set (see agent_service.mark_curated),
    even a successful one cannot move it back to where the re-read started.

    `metered=False` is for runs the platform initiates on its own (the
    first-day curator): they must not eat the user's free monthly allowance.

    One run per agent: a dispatch that lands while this agent's run is still
    mid-turn resolves as the designed `already_running` skip. It costs nothing
    and is not a failure — the run in flight advances the watermark past
    exactly what it read, so a skipped dispatch discards no work, and it never
    charges the allowance because the skip happens before the run is metered."""
    from ..services import (
        agent_service,
        curation_service,
        scoped_curation_service,
        sprite_agent_service,
    )

    lock = sprite_agent_service.agent_run_lock(agent_id, AGENT_RUN_LOCK_TTL)
    try:
        await lock.acquire()
    except sprite_agent_service.TurnInProgress:
        logger.info("curator run in flight for agent %s — skipping this dispatch", agent_id)
        await agent_service.mark_run_skipped(agent_id, "already_running")
        return
    try:
        agent = await agent_service.get_agent_by_id(agent_id)
        # An unmetered run is a platform trigger (the first-day tick). A curator
        # the user parked as chat-only must stay parked: the platform may not
        # run an agent the user turned off, metered or not.
        if not metered and agent["run_mode"] != "scheduled":
            return
        # The compare-and-set anchor for mark_curated is the stored position this
        # run actually loaded — captured BEFORE the full_history override below,
        # which zeroes the feed's `since` but must not change what the run may
        # write against. A rewind or an overlapping run that moves the stored
        # position during the turn makes this run's completion refuse loudly.
        read_position = agent["curated_through"]
        if full_history:
            agent = {**agent, "curated_through": None}
        now = datetime.now(UTC)
        await agent_service.mark_run(agent_id, metered=metered)
        try:
            # Seconds-resolution stamp so a manual run never shares a session
            # with the beat's minute-stamped run. The stamp separates history
            # only — single flight is `lock` above, not this.
            await sprite_agent_service.run_scheduled(agent, now.strftime("%Y%m%d%H%M%S"))
            # A scoped workspace run commits its own watermark under the same
            # permission lock as its writes, so a concurrent opt-out's reset
            # cannot be overwritten by this stamp.
            if await scoped_curation_service.workspace_for_agent(agent) is None:
                through = await curation_service.complete_through(
                    UUID(str(agent["user_id"])),
                    agent["curated_through"],
                    now,
                    agent["curator_wiki"],
                    agent.get("curator_folder_id"),
                )
                await agent_service.mark_curated(agent_id, read_position, through)
            await agent_service.mark_run_succeeded(agent_id)
        except Exception as e:
            await agent_service.mark_run_failed(agent_id, str(e), metered=metered)
            raise
    finally:
        await lock.release()


# During a scope's first day its wiki updates after every conversation, not
# just on the nightly tick — a user who just signed up (or a developer who
# just activated the platform) watches the wiki grow while they get set up.
# Debounced so a stream of event batches coalesces into at most one dispatch
# per window; a dispatch that still lands on a run in flight resolves as the
# `already_running` skip (see `_run_curator_now`).
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


async def _require_run_auth(scope_user_id: UUID, agent: dict) -> None:
    """Preflight the credential a run needs, raising NeedsAuth/ProviderNotConfigured.

    A developer-platform workspace curates through the backend's own key with no
    user credential in play, so its gate is that key. Everything else — personal
    Memory, project-folder curators, scheduled non-curator agents — runs on the
    scope's credential, and that credential is a *pinned local endpoint* as much
    as a key provider: resolving without `model_id`/`credential_id` would send
    the run to a provider the row never chose (or fail it as a mismatch).

    `scope_user_id` is the owner of the feed, which for a folder curator is not
    necessarily `agent["user_id"]`.
    """
    from ..services import agent_auth, scoped_curation_service

    if await scoped_curation_service.workspace_for_agent(agent) is not None:
        scoped_curation_service.require_configured()
        return
    await agent_auth.resolve(
        scope_user_id,
        agent["model_provider"],
        model_id=agent.get("model_id"),
        credential_id=agent.get("credential_id"),
    )


async def _maybe_dispatch_first_day_run(scope_user_id: UUID, agent: dict, now: datetime) -> None:
    from ..services import agent_auth, curation_service

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
    try:
        await _require_run_auth(scope_user_id, agent)
    except (agent_auth.NeedsAuth, agent_auth.ProviderNotConfigured):
        return
    if not await curation_service.has_changes_since(
        scope_user_id,
        scope_user_id,
        agent["curated_through"],
        agent["curator_wiki"],
        agent.get("curator_folder_id"),
    ):
        return
    # Unmetered: the platform is the trigger, so the run must not eat the
    # scope's free monthly curator allowance.
    run_curator_now.delay(str(agent["id"]), metered=False)


async def _run_due() -> int:
    from ..config import settings
    from ..services import (
        agent_auth,
        agent_service,
        billing_service,
        curation_service,
    )

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d%H%M")
    dispatched = 0
    for agent in await agent_service.list_scheduled():
        if not _is_due(agent["schedule_cron"], agent["last_run_at"], now):
            continue
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
            continue
        # No runnable credential (unconnected free user) → nothing can run.
        try:
            await _require_run_auth(user_id, agent)
        except (agent_auth.NeedsAuth, agent_auth.ProviderNotConfigured):
            logger.info("agent schedule: no credential for agent %s — skipping", agent["id"])
            await agent_service.mark_run_skipped(agent["id"], "no_credential")
            continue
        # Cost gate: skip the curator (and the sprite wake) when nothing changed
        # since its watermark. Idle users cost one EXISTS per day. Gated on the
        # agent's own wiki, so an external curator is never woken for activity
        # its feed cannot show it.
        if agent["is_curator"] and not await curation_service.has_changes_since(
            user_id,
            user_id,
            agent["curated_through"],
            agent["curator_wiki"],
            agent.get("curator_folder_id"),
        ):
            await agent_service.mark_run_skipped(agent["id"], "no_changes")
            continue
        run_scheduled_agent.delay(str(agent["id"]), stamp)
        dispatched += 1
    return dispatched


async def _run_scheduled_agent(agent_id: UUID, stamp: str) -> None:
    from ..database import get_pool
    from ..services import (
        agent_service,
        alert_service,
        curation_service,
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
    if agent["run_mode"] != "scheduled":
        return
    user_id = UUID(str(agent["user_id"]))
    now = datetime.now(UTC)
    lock = sprite_agent_service.agent_run_lock(agent_id, AGENT_RUN_LOCK_TTL)
    try:
        await lock.acquire()
    except sprite_agent_service.TurnInProgress:
        # The beat already consumed this tick and its credit when it dispatched
        # — the same accounting as every other designed skip in this module.
        # The run this one deferred to is doing the work right now.
        logger.info("agent %s has a run in flight — skipping this dispatch", agent_id)
        await agent_service.mark_run_skipped(agent_id, "already_running")
        return
    try:
        await sprite_agent_service.run_scheduled(agent, stamp)
        if agent["is_curator"] and await scoped_curation_service.workspace_for_agent(agent) is None:
            # `now` predates the run, so changes made during it stay ahead of
            # the watermark and are picked up next time. If the delta
            # overflowed the event cap, the watermark stops at the last event
            # that fit — the overflow drains on subsequent runs. Bookkeeping
            # failures share the run's try so they also record last_run_error
            # and alert, instead of dying as a bare task error. The CAS anchor is
            # the very `agent["curated_through"]` handed to `complete_through` as
            # `since` — one value, so the position written against and the
            # position read cannot drift; a move under the run fails loudly here.
            read_position = agent["curated_through"]
            through = await curation_service.complete_through(
                user_id,
                read_position,
                now,
                agent["curator_wiki"],
                agent.get("curator_folder_id"),
            )
            await agent_service.mark_curated(agent_id, read_position, through)
        await agent_service.mark_run_succeeded(agent_id)
    except Exception as e:
        logger.exception("agent schedule: run failed for agent %s", agent_id)
        await agent_service.mark_run_failed(agent_id, str(e))
        email = await get_pool().fetchval("SELECT email FROM users WHERE id = $1", user_id)
        await alert_service.send_alert(
            f"Scheduled agent run failed: {agent['name']!r} for {email}: {str(e)[:300]}"
        )
    finally:
        await lock.release()


# A curator whose watermark is older than this while changes are pending has
# been failing for multiple nightly runs — one bad night must not page.
STALE_CURATOR_HOURS = 48


@celery.task(name="backend.tasks.agent_schedules.alert_stale_curators")
def alert_stale_curators() -> int:
    return run_async(_alert_stale_curators())


async def _alert_stale_curators() -> int:
    """Alert on curators whose watermark stopped advancing despite pending
    changes. Alerting on the stale *outcome* catches every cause — dead
    provider keys, harness bugs, a wedged beat — where per-run failure alerts
    only catch runs that started. A failed outcome or an unresolved started
    outcome qualifies. Designed skips resolve explicitly and stay quiet.
    """
    from ..database import get_pool
    from ..services import alert_service, curation_service

    cutoff = datetime.now(UTC) - timedelta(hours=STALE_CURATOR_HOURS)
    rows = await get_pool().fetch(
        """
        SELECT a.user_id, a.curated_through, a.last_run_error, a.last_run_outcome,
               a.curator_wiki, a.curator_folder_id, u.email
        FROM agents a JOIN users u ON u.id = a.user_id
        WHERE a.is_curator AND a.run_mode = 'scheduled'
          AND a.curated_through IS NOT NULL AND a.curated_through < $1
          AND a.last_run_outcome IN ('started', 'failed')
        """,
        cutoff,
    )
    stale = [
        r
        for r in rows
        if await curation_service.has_changes_since(
            r["user_id"],
            r["user_id"],
            r["curated_through"],
            r["curator_wiki"],
            r["curator_folder_id"],
        )
    ]
    if not stale:
        return 0
    lines = [
        f"- {r['email']}: last curated {r['curated_through']:%Y-%m-%d %H:%M} UTC "
        f"({(r['last_run_error'] or 'run started but never resolved')[:200]})"
        for r in stale
    ]
    await alert_service.send_alert(
        f"{len(stale)} Memory curator(s) stale >{STALE_CURATOR_HOURS}h with pending changes:\n"
        + "\n".join(lines)
    )
    return len(stale)
