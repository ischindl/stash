"""Pins the broker's re-queue window above the longest legal harness run.

The founder's curator lane 2026-10-01: the Redis broker re-delivered a still
unacked dispatch at its 3600 s default while the run was allowed to live 5400 s,
and the copy landed on the single-flight guard of a run that was still curating.
"""

from backend.celery_app import celery
from backend.tasks.agent_schedules import HARNESS_TIME_LIMIT


def test_the_redis_requeue_window_outlives_the_longest_legal_agent_run():
    """A live task's message must not be re-offered to another worker while the
    task is still allowed to run, so `visibility_timeout` has to be strictly
    greater than the hard limit the harness tasks carry. Direct key access, no
    `.get` fallback: a missing option is the defect this test exists to catch."""
    assert celery.conf.broker_transport_options["visibility_timeout"] > HARNESS_TIME_LIMIT
