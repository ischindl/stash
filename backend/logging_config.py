"""Configure this process's application logging: one stderr handler, nothing else.

This is the only place in the repo that owns the root handler, so an application
`INFO` record is readable in the process that emitted it. The web process calls
`configure_logging` from its lifespan; a celery process calls it from the
`setup_logging` receiver in `backend/celery_app.py`, which is what makes a
curator's decision lines readable in a worker's log rather than only in the API
process that dispatched it.
"""

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Make one stderr handler the authoritative sink for application records.

    Root handlers are removed rather than skipped so this stays correct when
    something else has already configured logging — the Alembic run inside
    ``init_db`` installs its own console handler and raises the root level.
    Removing and reinstalling is also what makes a repeated call idempotent.
    """
    for handler in list(logging.root.handlers):
        logging.root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.root.addHandler(handler)
    logging.root.setLevel(level)
    # The Alembic run inside init_db() can also switch off the loggers that
    # already existed when it configured logging; a handler alone would leave
    # the application silent, so re-enable this process's logger here.
    logging.getLogger("stash").disabled = False
