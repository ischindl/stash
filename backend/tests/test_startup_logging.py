"""App startup must put application log records on the process's standard error.

The 2026-08-27 STAS-131 preview: a cloud-agent turn died with
``FileNotFoundError: 'claude'`` and container logs stayed silent for about a
day. Two layers hid it. No handler existed for app loggers in the web-service
path, and the in-process Alembic run inside ``init_db()`` calls ``fileConfig``
with ``disable_existing_loggers=True``, which replaces the root handlers and
switches off the ``stash`` logger plus every already-imported ``backend.*``
module logger.
"""

import io
import logging
import sys

import pytest
import pytest_asyncio

from backend import database as db_module
from backend import main as main_module

SPRITE_LOGGER = "backend.services.sprite_agent_service"


def _handlers_on(stream):
    """Root handlers bound to ``stream``.

    Filtering by bound stream — never by root handler count — because pytest
    keeps its own capture and log-file handlers on the root logger.
    """
    return [
        handler
        for handler in logging.root.handlers
        if isinstance(handler, logging.StreamHandler)
        and getattr(handler, "stream", None) is stream
    ]


@pytest.fixture
def restored_root_logging():
    """Put root logging back exactly as the session left it.

    The session's Alembic bootstrap runs before this module, so ``backend.*``
    loggers are already disabled on entry and get restored, not cleared.
    Handlers are removed and re-added, never closed: pytest's own log-file
    stream has to stay usable for the rest of the session.
    """
    handlers = list(logging.root.handlers)
    level = logging.root.level
    disabled = {
        name: logger.disabled
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }

    yield

    for handler in list(logging.root.handlers):
        logging.root.removeHandler(handler)
    for handler in handlers:
        logging.root.addHandler(handler)
    logging.root.setLevel(level)
    for name, logger in logging.Logger.manager.loggerDict.items():
        if isinstance(logger, logging.Logger) and name in disabled:
            logger.disabled = disabled[name]


@pytest_asyncio.fixture
async def app_startup(restored_root_logging, monkeypatch):
    """Run the real app lifespan with standard error captured.

    ``init_db`` is deliberately not stubbed: the in-process migration run is
    one of the two masking layers this file exists to defeat. The pool is
    restored afterwards because ``init_db`` replaces the module-global pool
    and lifespan shutdown nulls it, which would strand every later test.
    """
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured)
    monkeypatch.setenv("STASH_DISABLE_DEMO_SEED", "1")
    saved_pool = db_module.pool
    try:
        async with main_module.lifespan(main_module.app):
            yield captured
    finally:
        db_module.pool = saved_pool


def test_startup_leaves_one_root_handler_on_stderr(app_startup):
    assert len(_handlers_on(app_startup)) == 1
    assert logging.root.level <= logging.INFO


def test_info_from_the_stash_logger_reaches_stderr(app_startup):
    logging.getLogger("stash").info("web process booting")

    written = app_startup.getvalue()
    assert "INFO" in written
    assert "stash" in written
    assert "web process booting" in written


def test_turn_crash_traceback_reaches_stderr(app_startup):
    """The STAS-131 symptom, reproduced as an assertion."""
    incident_logger = logging.getLogger(SPRITE_LOGGER)
    try:
        raise FileNotFoundError(2, "No such file or directory: 'claude'")
    except FileNotFoundError:
        incident_logger.exception("cloud agent: turn failed for session %s", "sess-preview")

    written = app_startup.getvalue()
    assert "Traceback (most recent call last)" in written
    assert "FileNotFoundError" in written
    assert "'claude'" in written


def test_startup_enables_app_loggers_and_owns_the_root_handler(app_startup):
    assert not logging.getLogger("stash").disabled
    assert not logging.getLogger(SPRITE_LOGGER).disabled

    foreign = logging.StreamHandler(io.StringIO())
    logging.root.addHandler(foreign)

    main_module._configure_logging()
    main_module._configure_logging()

    assert foreign not in logging.root.handlers
    assert len(_handlers_on(app_startup)) == 1
