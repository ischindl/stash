"""The backend must own a logging path that reaches container stderr.

STAS-151. Three mechanisms stacked to hide every application record from
`docker logs`, so the 2026-08-27 founder-preview crash trace never appeared:

1. nothing attaches a handler to the root logger — uvicorn's default
   `LOGGING_CONFIG` configures only `uvicorn`, `uvicorn.error` and
   `uvicorn.access`, and never the root;
2. the boot-time Alembic step calls `fileConfig(alembic.ini)` in-process, which
   *replaces* root's handlers with its own console handler and forces root back
   to WARNING;
3. that same call defaults to `disable_existing_loggers=True`, and every
   application logger already exists by then (they are created at import time),
   so they are switched off permanently. A disabled logger never emits — not
   even through stdlib's `lastResort`.

These tests are written red-first against that boot order.

Deliberate file rules: `capsys` and never `caplog`, because the startup
function clears root handlers and would detach caplog's own handler; and
`configure_logging` is imported inside each test so a missing symbol fails one
test instead of erroring collection for the whole module.
"""

import logging
import logging.config
import sys

import pytest
import uvicorn.config

STASH_MARKER = "stas151 startup breadcrumb VISIBLE?"
# A logger this module owns, so its records cannot be attributed to some other
# test's global-logging state: it does not exist when Alembic's fileConfig runs,
# and so is untouched by `disable_existing_loggers`.
PROBE_LOGGER = "stas151.configprobe"
# The module whose `logger.exception` produced the 2026-08-27 incident trace.
INCIDENT_LOGGER = "backend.services.sprite_agent_service"
INCIDENT_SESSION = "sess_test"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def restore_global_logging():
    """Hand global logging back exactly as the rest of the suite left it.

    `configure_logging` replaces the root handler list, which would otherwise
    detach pytest's own root handler (and uvicorn's handlers, configured by
    these tests) for the ~133 modules that run after this one.
    """
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_uvicorn = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in UVICORN_LOGGERS
    }
    yield
    root.handlers[:] = saved_root_handlers
    root.setLevel(saved_root_level)
    for name, (handlers, propagate) in saved_uvicorn.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.propagate = propagate


async def test_app_logs_reach_stderr_after_real_boot_migrations(client, capsys):
    """The root-cause gate: records survive the real in-process Alembic bootstrap.

    `client` is requested for its side effect — its session fixture applies the
    Alembic migrations in-process, which is exactly the step that used to switch
    the application's loggers off.
    """
    from backend.main import configure_logging

    capsys.readouterr()
    configure_logging()

    logging.getLogger("stash").info(STASH_MARKER)
    try:
        raise FileNotFoundError(2, "No such file or directory: 'claude'")
    except FileNotFoundError:
        logging.getLogger(INCIDENT_LOGGER).exception(
            "cloud agent: turn failed for session %s", INCIDENT_SESSION
        )

    err = capsys.readouterr().err
    breadcrumb = [line for line in err.splitlines() if STASH_MARKER in line]
    assert breadcrumb, f"app INFO never reached stderr; stderr was:\n{err}"
    assert "INFO" in breadcrumb[0]
    assert " stash:" in breadcrumb[0]

    failure = [line for line in err.splitlines() if INCIDENT_LOGGER in line]
    assert failure, f"crash trace is unattributed; stderr was:\n{err}"
    assert "ERROR" in failure[0]
    assert "cloud agent: turn failed for session sess_test" in failure[0]
    trace = err.split("cloud agent: turn failed for session sess_test", 1)[1]
    assert "FileNotFoundError" in trace
    assert "No such file or directory: 'claude'" in trace

    # Mechanism 3: a disabled logger emits nothing however it is configured.
    assert logging.getLogger("stash").disabled is False
    assert logging.getLogger(INCIDENT_LOGGER).disabled is False


def test_startup_config_installs_one_stderr_handler_at_info(capsys):
    """One codepath guard: a re-entrant startup must not stack handlers."""
    from backend.main import configure_logging

    configure_logging()
    configure_logging()

    root = logging.getLogger()
    assert len(root.handlers) == 1, f"expected one root handler, got {root.handlers}"
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr
    assert root.level == logging.INFO

    capsys.readouterr()
    logging.getLogger(PROBE_LOGGER).info("startup formatter probe")
    lines = [
        line for line in capsys.readouterr().err.splitlines() if "startup formatter probe" in line
    ]
    assert lines, "the startup handler produced no output"
    assert "INFO" in lines[0]
    assert PROBE_LOGGER in lines[0]


def test_uvicorn_config_alone_drops_app_info_records(capsys):
    """Mechanism 1, pinned: uvicorn's own config gives application records nowhere to go.

    Green before and after the fix on purpose — it documents why the startup
    handler is needed rather than trusting prose about uvicorn's config.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)

    stash = logging.getLogger("stash")
    assert stash.getEffectiveLevel() == logging.WARNING
    assert stash.handlers == []
    assert stash.propagate is True

    capsys.readouterr()
    stash.info("uvicorn-alone probe")
    assert capsys.readouterr().err == ""


def test_uvicorn_records_are_not_double_logged(capsys):
    """The new root handler must not duplicate uvicorn's own lines.

    `uvicorn` has `propagate: False`, so its records stop at uvicorn's handler;
    the app handler replacing root's must leave them printed exactly once.
    """
    from backend.main import configure_logging

    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    configure_logging()

    capsys.readouterr()
    logging.getLogger("uvicorn.error").info("uvicorn single-print probe")
    err = capsys.readouterr().err
    assert err.count("uvicorn single-print probe") == 1, f"uvicorn line printed twice:\n{err}"
