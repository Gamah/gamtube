"""Logging for the `gamtube` logger tree.

Its own module, with stdlib imports only, so it can be tested without pulling
in FastAPI, SQLAlchemy and every router.
"""

import logging

ROOT_LOGGER = "gamtube"
_FORMAT = "%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s"


def configure_logging(debug: bool = False) -> logging.Logger:
    """Give the `gamtube` logger a handler of its own.

    Uvicorn configures its own loggers and leaves the root alone, so without
    this an INFO record from `gamtube.*` is handled by `logging.lastResort` at
    WARNING and silently dropped - `isEnabledFor(INFO)` is False, meaning the
    record is never even created. Everything the bot logs would simply never
    appear in app.log.

    Idempotent: called again, it only updates the level.
    """
    log = logging.getLogger(ROOT_LOGGER)
    level = logging.DEBUG if debug else logging.INFO
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        log.addHandler(handler)
        # systemd captures stdout/stderr into app.log; don't also hand records
        # to the root logger, which uvicorn may have configured differently.
        log.propagate = False
    log.setLevel(level)
    return log
