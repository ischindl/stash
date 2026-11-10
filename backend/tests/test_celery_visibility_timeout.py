"""Pins the broker's re-queue window above the longest run a task may legally take.

The founder's curator lane 2026-10-01: the Redis broker re-delivered a still
unacked dispatch at its 3600 s default while the run was allowed to live 5400 s,
and the copy landed on the single-flight guard of a run that was still curating.
"""

from backend.celery_app import celery


def test_the_redis_requeue_window_outlives_the_longest_legal_task():
    """A live task's message must not be re-offered to another worker while the
    task is still allowed to run, so `visibility_timeout` has to be strictly
    greater than the hard ceiling a task may run under. No task carries its own
    override any more, so the global hard limit is that ceiling. Direct key
    access, no `.get` fallback: a missing option is the defect this test exists
    to catch."""
    assert celery.conf.broker_transport_options["visibility_timeout"] > celery.conf.task_time_limit
