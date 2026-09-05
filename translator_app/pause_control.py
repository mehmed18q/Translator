from __future__ import annotations

import logging
import time
from collections.abc import Callable


PauseCallback = Callable[[], bool]
CancelCallback = Callable[[], bool]


def wait_while_paused(
    *,
    pause_callback: PauseCallback | None,
    cancel_callback: CancelCallback | None,
    logger: logging.Logger,
    operation_name: str,
    poll_interval_seconds: float = 0.1,
) -> bool:
    """Wait at a safe checkpoint and return whether cancellation was requested."""
    if not pause_callback or not pause_callback():
        return bool(cancel_callback and cancel_callback())

    logger.info("%s paused.", operation_name)
    while pause_callback():
        if cancel_callback and cancel_callback():
            logger.info("%s stopped while paused.", operation_name)
            return True
        time.sleep(max(poll_interval_seconds, 0.01))

    if cancel_callback and cancel_callback():
        logger.info("%s stopped while paused.", operation_name)
        return True

    logger.info("%s resumed.", operation_name)
    return False
