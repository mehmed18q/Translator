from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def run_with_retry(
    operation: Callable[[], T],
    *,
    operation_name: str,
    attempts: int,
    initial_delay_seconds: float,
    backoff_factor: float,
    logger: logging.Logger,
) -> T:
    last_error: Exception | None = None
    max_attempts = max(attempts, 1)
    delay = max(initial_delay_seconds, 0)

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            logger.warning(
                "%s failed on attempt %s/%s: %s. Retrying in %.1fs",
                operation_name,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            if delay > 0:
                time.sleep(delay)
            delay *= max(backoff_factor, 1)

    assert last_error is not None
    raise last_error
