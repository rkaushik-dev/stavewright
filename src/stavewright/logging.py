"""Logging setup.

A commissioning run is the record of what happened to a machine that is about
to print onto product. When a line stops six weeks later the only thing anyone
has is this log, so every line carries the run id and the terminal id, and the
file sink is always JSON regardless of what the console is doing.

Nothing here configures logging at import time. :func:`configure` is called from
the CLI once, and library callers who never call it get structlog's default
behaviour rather than a surprise reconfiguration.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

DEFAULT_LEVEL = "INFO"

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_RUN_ID_KEY = "run_id"
_TERMINAL_KEY = "terminal"

_configured = False


def new_run_id() -> str:
    """A short identifier for one commissioning run.

    Twelve hex characters. Long enough that two benches running the same day do
    not collide, short enough that an operator can read it off a screen and type
    it into a ticket.
    """
    return uuid.uuid4().hex[:12]


def _detach_handlers(root: logging.Logger) -> None:
    """Remove every root handler and close the ones that own a file.

    Detaching without closing leaks the descriptor. On a bench that is harmless
    because the process exits, but a long lived caller that reconfigures per
    terminal runs out of handles, and the file stays locked so the next run
    cannot roll it.
    """
    for handler in list(root.handlers):
        root.removeHandler(handler)
        if isinstance(handler, logging.FileHandler):
            handler.close()


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]


def configure(
    *,
    level: str = DEFAULT_LEVEL,
    log_file: Path | None = None,
    console: bool = True,
    colours: bool | None = None,
) -> None:
    """Set up structlog and the standard library root logger.

    :param level: one of :data:`LEVELS`.
    :param log_file: when given, a JSON line is written per event. The parent
        directory is created.
    :param console: whether to render human readable output on stderr.
    :param colours: force colour on or off. ``None`` means autodetect.
    :raises ValueError: on an unknown level.
    """
    global _configured

    if level.upper() not in LEVELS:
        raise ValueError(f"unknown log level {level!r}, expected one of {', '.join(LEVELS)}")
    numeric = getattr(logging, level.upper())

    root = logging.getLogger()
    _detach_handlers(root)
    root.setLevel(numeric)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(
                    colors=sys.stderr.isatty() if colours is None else colours
                ),
                foreign_pre_chain=_shared_processors(),
            )
        )
        root.addHandler(stream)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(sort_keys=True),
                foreign_pre_chain=_shared_processors(),
            )
        )
        root.addHandler(file_handler)

    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    _configured = True


def is_configured() -> bool:
    """True once :func:`configure` has run in this process."""
    return _configured


def reset() -> None:
    """Undo :func:`configure`. Used by tests so one run cannot leak into another."""
    global _configured
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    _detach_handlers(logging.getLogger())
    _configured = False


def get_logger(name: str) -> Any:
    """Return a bound logger. Thin wrapper so callers do not import structlog."""
    return structlog.stdlib.get_logger(name)


@contextmanager
def run_context(run_id: str, terminal: str | None = None, **extra: Any) -> Iterator[str]:
    """Bind the run id, and optionally the terminal, for the duration of a block.

    Every event logged inside the block carries them, including events from code
    that never sees the identifiers itself. Yields the run id so a caller can
    write it into a report.
    """
    bindings: dict[str, Any] = {_RUN_ID_KEY: run_id, **extra}
    if terminal is not None:
        bindings[_TERMINAL_KEY] = terminal
    tokens = structlog.contextvars.bind_contextvars(**bindings)
    try:
        yield run_id
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


__all__ = [
    "DEFAULT_LEVEL",
    "LEVELS",
    "configure",
    "get_logger",
    "is_configured",
    "new_run_id",
    "reset",
    "run_context",
]
