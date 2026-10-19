"""Decorator-based logging helpers (TECH.md: decorators over inline logging).

Keeps cross-cutting log lines out of business logic. Deliberately never
records argument values — call arguments may carry image bytes or secrets.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

#: Awaitable return type; ``ParamSpec`` keeps decorated signatures intact.
AsyncCallable = Callable[P, Awaitable[R]]


def log_async_call(
    logger: logging.Logger,
) -> Callable[[AsyncCallable[P, R]], AsyncCallable[P, R]]:
    """Log entry/failure/success of an async call by name and duration.

    Args:
        logger: Logger to emit against.

    Returns:
        A decorator that logs failures loudly (name + exception type +
        duration, then re-raises) and success at debug level. Argument
        values are never logged (they may contain bytes or secrets).
    """

    def decorator(func: AsyncCallable[P, R]) -> AsyncCallable[P, R]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.monotonic()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                elapsed_ms = (time.monotonic() - started) * 1000
                logger.warning(
                    "%s raised %s after %.0fms",
                    func.__name__,
                    type(exc).__name__,
                    elapsed_ms,
                )
                raise
            elapsed_ms = (time.monotonic() - started) * 1000
            logger.debug("%s completed in %.0fms", func.__name__, elapsed_ms)
            return result

        return wrapper

    return decorator


def log_phase_transition(
    logger: logging.Logger,
    chat_id: int,
    old_phase: Any,
    new_phase: Any,
) -> None:
    """Emit one structured phase-transition line (the state driver's audit).

    Args:
        logger: Logger to emit against.
        chat_id: Session the transition belongs to.
        old_phase: Phase before the transition.
        new_phase: Phase after the transition.
    """
    logger.info("phase_transition chat_id=%s %s -> %s", chat_id, old_phase, new_phase)
