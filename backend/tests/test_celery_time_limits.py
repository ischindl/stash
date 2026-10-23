"""Per-task time limits for the harness-execution tasks.

PI curation runs routinely take 13+ minutes and exceed 25 minutes on large
deltas. The global 1500s soft / 1800s hard ceiling (celery_app.py) killed
them mid-run on 2026-08-24: the run was marked failed, the curated_through
watermark did not advance, and the next run re-read the same delta. The two
tasks that actually run a harness agent therefore carry their own limits,
while the global stays the ceiling for everything else and the cheap beat
dispatchers keep the global fallback so a sweep never holds a slot for 95
minutes.
"""

from backend.celery_app import celery
from backend.config import settings
from backend.tasks.agent_schedules import (
    alert_stale_curators,
    drain_curator_backlog,
    first_day_curator_tick,
    run_curator_now,
    run_due,
    run_scheduled_agent,
)

HARNESS_TASKS = [run_curator_now, run_scheduled_agent]
DISPATCHER_TASKS = [run_due, drain_curator_backlog, first_day_curator_tick, alert_stale_curators]


def test_harness_tasks_carry_extended_limits():
    # 90 min soft / 95 min hard, comfortably above observed PI run durations
    # (13–40+ min) while still bounding a runaway loop.
    for task in HARNESS_TASKS:
        assert task.soft_time_limit == 5400, task.name
        assert task.time_limit == 5700, task.name


def test_harness_limits_stay_ordered():
    # Celery rejects soft > hard at dispatch time (celery/app/task.py), so a
    # soft limit equal to or above the hard limit would kill runs at the
    # wrong boundary.
    for task in HARNESS_TASKS:
        assert 0 < task.soft_time_limit < task.time_limit, task.name


def test_curator_run_budget_stays_under_the_worker_ceiling():
    # The scoped curator stops itself at `CURATOR_RUN_TIMEOUT_SECONDS` and keeps the
    # slices it committed (STAS-266). That only helps while the task is still alive to
    # record its own failure: a budget at or above this task's soft limit would have
    # Celery kill the run mid-slice instead, which records no cause and no failure —
    # the outcome the budget exists to avoid.
    assert 0 < settings.CURATOR_RUN_TIMEOUT_SECONDS < run_curator_now.soft_time_limit


def test_dispatcher_tasks_keep_global_fallback():
    # No per-task override: the worker pool applies the global conf defaults.
    # A beat sweep must never hold a worker slot for 95 minutes.
    for task in DISPATCHER_TASKS:
        assert task.time_limit is None, task.name
        assert task.soft_time_limit is None, task.name


def test_global_limits_unchanged():
    # The global ceiling stays as-is; only the harness tasks opt out.
    assert celery.conf.task_time_limit == 1800
    assert celery.conf.task_soft_time_limit == 1500
