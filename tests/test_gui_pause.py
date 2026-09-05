from __future__ import annotations

import threading
import unittest

from translator_app.gui import TranslatorGuiApp


class FakeButton:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **options: object) -> None:
        self.options.update(options)


class FakeVariable:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class RunningWorker:
    def is_alive(self) -> bool:
        return True


class GuiPauseTests(unittest.TestCase):
    def make_app(self, job_name: str = "all-tables") -> TranslatorGuiApp:
        app = TranslatorGuiApp.__new__(TranslatorGuiApp)
        app.worker_thread = RunningWorker()
        app.running_job_name = job_name
        app.cancel_event = threading.Event()
        app.pause_event = threading.Event()
        app.pause_button = FakeButton()
        app.resx_pause_button = FakeButton()
        app.stop_button = FakeButton()
        app.resx_stop_button = FakeButton()
        app.status_var = FakeVariable()
        app.resx_status_var = FakeVariable()
        app.test_messages = []
        app._append_log = app.test_messages.append
        return app

    def test_pause_button_toggles_between_pause_and_resume(self) -> None:
        app = self.make_app()

        app.toggle_pause()

        self.assertTrue(app.pause_event.is_set())
        self.assertEqual(app.pause_button.options["text"], "Resume")
        self.assertEqual(app.pause_button.options["style"], "Resume.TButton")
        self.assertEqual(app.status_var.value, "Pause requested...")

        app.toggle_pause()

        self.assertFalse(app.pause_event.is_set())
        self.assertEqual(app.pause_button.options["text"], "Pause")
        self.assertEqual(app.pause_button.options["style"], "Pause.TButton")
        self.assertEqual(app.status_var.value, "Resuming...")

    def test_stop_while_paused_cancels_and_disables_controls(self) -> None:
        app = self.make_app(job_name="resx-translate")
        app.pause_event.set()

        app.stop_operation()

        self.assertTrue(app.cancel_event.is_set())
        self.assertEqual(app.resx_status_var.value, "Stop requested...")
        for button in (
            app.pause_button,
            app.resx_pause_button,
            app.stop_button,
            app.resx_stop_button,
        ):
            self.assertEqual(button.options["state"], "disabled")

    def test_cancelled_job_handlers_report_stopped(self) -> None:
        database_app = self.make_app()
        database_app.cancel_event.set()
        database_app.log_file_var = FakeVariable()
        database_app._set_running = lambda _running: None
        database_app._finish_operation_timing = lambda: None

        database_app._handle_job_done("all-tables", object(), "database.log")

        self.assertEqual(database_app.status_var.value, "Stopped")
        self.assertEqual(database_app.log_file_var.value, "database.log")
        self.assertIn("Job stopped: all-tables", database_app.test_messages)

        resx_app = self.make_app(job_name="resx-translate")
        resx_app.cancel_event.set()
        resx_app.resx_log_file_var = FakeVariable()
        resx_app._set_running = lambda _running: None
        resx_app._finish_resx_timing = lambda: None

        resx_app._handle_resx_job_done("resx-translate", object(), "resx.log")

        self.assertEqual(resx_app.resx_status_var.value, "Stopped")
        self.assertEqual(resx_app.resx_log_file_var.value, "resx.log")
        self.assertIn("Job stopped: resx-translate", resx_app.test_messages)


if __name__ == "__main__":
    unittest.main()
