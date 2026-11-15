"""An application log record has to reach the worker process's own stderr.

The curator's decisions — the meter line of a committed pass, the guard that declined
a dispatch, the lane a turn took — are log records, and a record nobody can read is
not evidence. This file pins their delivery in a celery process.

That guarantee is ours through one function. `backend/celery_app.py` connects a celery
`setup_logging` receiver; celery sends that signal before it configures anything and
installs a root handler of its own only when no receiver answers
(`Logging.setup_logging_subsystem`, `if not receivers:`), so the receiver answers and
`backend/logging_config.py` owns the process's single root handler. These tests are
what keeps it true: the receiver is four lines, and dropping one leaves every worker
logging through celery's default format — which prints no logger name — with no
curator line anywhere to mark the change.

Both tests read a CHILD PROCESS's stderr, never `caplog`. `caplog` attaches its own
handler to the root logger and sets levels itself, so a record it captures says nothing
about whether the process's real sink received it — a suite-wide green under `caplog`
is compatible with a worker that prints nothing, which is the failure this file exists
to catch. pytest likewise keeps its own capture handlers on the root logger, so the
child's stdout and stderr are the only honest read of what the process itself emitted.
"""

import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The meter line of one committed pass, in the field set
# `test_scoped_curation.py` pins for the scoped curator.
METER_LINE = (
    "curator pass=1 agent=probe committed_through=2026-11-14T00:00:00+00:00"
    " characters=120 scopes=1 wall=3.0s budget=1746 turn_seconds=95.0000"
    " seconds_per_character=0.0250 calibrated_seconds_per_character=0.3265"
)
# One of the lines STAS-445 added: the guard that refused a dispatch its turn.
GUARD_LINE = (
    "agent schedule: curator 00000000-0000-0000-0000-000000000000 declined its pass"
    " guard=curator_off"
)
# Celery's own default worker format prints the process name and no logger name, so its
# presence on an application record means celery configured this process again.
CELERY_OWN_FORMAT = "INFO/MainProcess]"


def _boot_a_worker_shaped_process(loglevel: str, body: str) -> subprocess.CompletedProcess[str]:
    """Configure logging the way every celery program in this repo does, then run
    ``body``, in a fresh interpreter.

    ``celery.log.setup(loglevel)`` is the call ``celery.apps.worker.Worker`` and
    ``celery.apps.beat.Beat`` both make at boot, and ``--loglevel=info`` is what
    ``docker-compose.prod.yml`` and ``start.sh`` pass to all four programs. No broker,
    no database, and no celery task is executed: the assertion is about the process's
    logging, never about a run.
    """
    script = (
        "import logging, sys\n"
        "from backend.celery_app import celery\n"
        f"celery.log.setup({loglevel!r})\n" + body
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )


def test_a_scoped_curator_log_record_reaches_the_worker_process_stderr():
    """AC (a)'s delivery half: the scoped curator's per-pass meter line, read from the
    stderr of a process that set its logging up the way the worker does.

    The whole rendered line is asserted, logger name included, because a record that
    arrives without its module name is not attributable in a log shared by four
    programs.
    """
    result = _boot_a_worker_shaped_process(
        "info",
        f"logging.getLogger('backend.services.scoped_curation_service').info({METER_LINE!r})\n",
    )

    assert result.returncode == 0, result.stderr
    assert f"INFO backend.services.scoped_curation_service: {METER_LINE}" in result.stderr
    assert METER_LINE not in result.stdout, "the record went somewhere other than stderr"


def test_the_worker_installs_one_app_handler_at_the_requested_level():
    """The anti-vacuity anchor: the one root handler is the app's, at the level asked
    for, and it is the one printing.

    Handler count and bound stream alone would not separate the ways a worker goes
    silent, because celery's own default handler is also a ``StreamHandler`` on
    ``sys.stderr`` at the same level. What separates them is the rendered record: only
    the app's formatter carries the emitting module's name. Disconnect the receiver and
    this test reddens while nothing else in the suite notices.
    """
    result = _boot_a_worker_shaped_process(
        "info",
        "root = logging.getLogger()\n"
        "streams = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]\n"
        "print('handlers=%d' % len(root.handlers))\n"
        "print('stderr_only=%s' % (len(streams) == 1 and streams[0].stream is sys.stderr))\n"
        "print('level=%d' % root.level)\n"
        f"logging.getLogger('backend.tasks.agent_schedules').info({GUARD_LINE!r})\n"
        "logging.getLogger('backend.tasks.agent_schedules').warning('a declined pass at WARNING')\n",
    )

    assert result.returncode == 0, result.stderr
    facts = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert facts["handlers"] == "1", f"root carries celery's handlers too: {facts}"
    assert facts["stderr_only"] == "True", f"the app handler is not on stderr: {facts}"
    assert int(facts["level"]) <= logging.INFO, f"root level hides INFO: {facts}"
    assert f"INFO backend.tasks.agent_schedules: {GUARD_LINE}" in result.stderr
    assert "WARNING backend.tasks.agent_schedules: a declined pass at WARNING" in result.stderr
    assert CELERY_OWN_FORMAT not in result.stderr, "celery configured this process again"


def test_the_celery_loglevel_still_gates_that_delivery():
    """The receiver takes over the handler without taking over the operator's knob.

    The level arrives through the signal, so the same two INFO records must be absent
    from a process started at ``warning``. A receiver that hardcoded INFO would leave
    both tests above green and leave an operator who asked for a quieter worker still
    reading curator lines.
    """
    result = _boot_a_worker_shaped_process(
        "warning",
        f"logging.getLogger('backend.services.scoped_curation_service').info({METER_LINE!r})\n"
        f"logging.getLogger('backend.tasks.agent_schedules').info({GUARD_LINE!r})\n",
    )

    assert result.returncode == 0, result.stderr
    assert METER_LINE not in result.stderr
    assert GUARD_LINE not in result.stderr
