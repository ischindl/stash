"""The order of the numbers that bound a scoped curator run.

Whichever of these arrives first is what stops the run, so the order *is* the
behaviour. The run's own timeout should be the thing that closes it. The wall around
the run sits past that timeout by one model completion, because a completion already in
flight when the timeout arrives is still inside the client's own allowance and
cancelling it discards writes that were about to land. The worker's limits are the last
word, and they are the exits that record nothing: a task stopped by the worker leaves
neither the run's error nor a cause behind, which is the outcome every number to its
left exists to prevent.
"""

import anthropic

from backend.celery_app import celery
from backend.config import settings
from backend.services import scoped_curation_service
from backend.tasks.agent_schedules import (
    alert_stale_curators,
    first_day_curator_tick,
    run_curator_now,
    run_due,
    run_scheduled_agent,
)

RUN_TASKS = [run_curator_now, run_scheduled_agent]
DISPATCHER_TASKS = [run_due, first_day_curator_tick, alert_stale_curators]


def test_run_tasks_carry_a_ceiling_of_their_own():
    # Without one, the worker's global 1500/1800 is the first thing that can fire on a
    # run that needed its whole wall.
    for task in RUN_TASKS:
        assert task.soft_time_limit > celery.conf.task_soft_time_limit, task.name
        assert task.time_limit > celery.conf.task_time_limit, task.name


def test_dispatcher_tasks_keep_the_global_fallback():
    # A beat sweep is cheap. It must not reserve a worker slot for the length of a run.
    for task in DISPATCHER_TASKS:
        assert task.soft_time_limit is None, task.name
        assert task.time_limit is None, task.name


def test_the_wall_does_not_collapse_onto_the_run_timeout():
    # Equal values make the wall the run's only stop, and it fires inside a completion
    # the client is still willing to wait for.
    assert scoped_curation_service.run_wall_timeout() > settings.AGENT_TURN_TIMEOUT_SECONDS


def test_the_wall_clears_one_full_completion_request():
    # The window earns its size from the client's own allowance. If a client upgrade
    # ever waits longer for one read, this names the constant that has to grow.
    assert scoped_curation_service.MODEL_COMPLETION_WINDOW_S >= anthropic.DEFAULT_TIMEOUT.read


def test_no_worker_limit_can_fire_before_the_wall():
    # Left to right: the run's timeout, the wall, the soft limit that raises inside the
    # task, the hard limit that ends it. Everything right of the wall is an exit with no
    # recorded cause, so the wall has to be the last of them to be reachable.
    wall = scoped_curation_service.run_wall_timeout()
    for task in RUN_TASKS:
        assert 0 < settings.AGENT_TURN_TIMEOUT_SECONDS < wall, task.name
        assert wall < task.soft_time_limit < task.time_limit, task.name


def test_global_limits_stay_the_ceiling_for_everything_else():
    # The run tasks opt out; nothing else does, so the globals keep their meaning.
    assert celery.conf.task_soft_time_limit == 1500
    assert celery.conf.task_time_limit == 1800
