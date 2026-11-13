"""Pins the broker's re-queue window above the longest run a task may legally take.

The founder's curator lane 2026-10-01: the Redis broker re-delivered a still
unacked dispatch at its 3600 s default while the run was allowed to live 5400 s,
and the copy landed on the single-flight guard of a run that was still curating.
"""

import sys

from backend.celery_app import celery


def _longest_legal_run() -> tuple[str, int]:
    """The longest run any registered task may legally take, and who may take it.

    Each registry entry carries that task's effective hard ceiling: its own
    decorator `time_limit` where it declares one, and on the celery versions
    whose `Task.from_config` binding mirrors configuration onto the task class,
    the global `task_time_limit` on the tasks whose decorator declares none. The
    global is therefore added as a candidate instead of trusted as the answer, so
    the maximum is the same whichever of those two semantics the installed celery
    has. The registry is the only single source for that set.
    """
    celery.loader.import_default_modules()
    unimported = [m for m in celery.conf.include if m not in sys.modules]
    assert not unimported, f"include list not imported, registry is partial: {unimported}"
    ceilings = {t.name: t.time_limit for t in celery.tasks.values() if t.time_limit is not None}
    ceilings["(global task_time_limit)"] = celery.conf.task_time_limit
    return max(ceilings.items(), key=lambda item: item[1])


def test_the_redis_requeue_window_outlives_the_longest_legal_task():
    """A live task's message must not be re-offered to another worker while the
    task is still allowed to run, so `visibility_timeout` has to be strictly
    greater than the hard ceiling a task may run under - the largest per-task
    override in the registry, not the global default the registry falls back to.
    Direct key access, no `.get` fallback: a missing option is the defect this
    test exists to catch."""
    longest_task, longest_run = _longest_legal_run()
    window = celery.conf.broker_transport_options["visibility_timeout"]
    assert window > longest_run, (
        f"{longest_task} may run {longest_run}s while the broker re-queues at "
        f"{window}s: the copy lands on a second worker mid-run"
    )
