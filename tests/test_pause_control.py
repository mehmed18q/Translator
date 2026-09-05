from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from translator_app.pause_control import wait_while_paused


class PauseControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"pause-test-{id(self)}")

    @patch("translator_app.pause_control.time.sleep")
    def test_waits_until_resume_without_cancelling(self, sleep) -> None:
        pause_states = iter((True, True, False))

        with self.assertLogs(self.logger, level="INFO") as captured:
            cancelled = wait_while_paused(
                pause_callback=lambda: next(pause_states),
                cancel_callback=lambda: False,
                logger=self.logger,
                operation_name="Test operation",
            )

        self.assertFalse(cancelled)
        sleep.assert_called_once_with(0.1)
        self.assertIn("Test operation paused.", captured.output[0])
        self.assertIn("Test operation resumed.", captured.output[1])

    def test_stop_breaks_a_paused_wait(self) -> None:
        with self.assertLogs(self.logger, level="INFO") as captured:
            cancelled = wait_while_paused(
                pause_callback=lambda: True,
                cancel_callback=lambda: True,
                logger=self.logger,
                operation_name="Test operation",
            )

        self.assertTrue(cancelled)
        self.assertIn("stopped while paused", captured.output[-1])

    def test_returns_immediately_when_not_paused(self) -> None:
        self.assertFalse(
            wait_while_paused(
                pause_callback=lambda: False,
                cancel_callback=lambda: False,
                logger=self.logger,
                operation_name="Test operation",
            )
        )


if __name__ == "__main__":
    unittest.main()
