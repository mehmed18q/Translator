from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from tkinter import TclError

from translator_app.gui import (
    can_scroll,
    format_average_rate,
    format_duration,
    format_estimated_finish,
    maximize_window,
    mousewheel_units,
)


class FakeRoot:
    def __init__(
        self,
        *,
        state_error: bool = False,
        attributes_error: bool = False,
    ) -> None:
        self.state_error = state_error
        self.attributes_error = attributes_error
        self.calls: list[tuple[str, object]] = []

    def state(self, value: str) -> None:
        self.calls.append(("state", value))
        if self.state_error:
            raise TclError("state unsupported")

    def attributes(self, name: str, value: bool) -> None:
        self.calls.append(("attributes", (name, value)))
        if self.attributes_error:
            raise TclError("attribute unsupported")

    def winfo_screenwidth(self) -> int:
        return 1280

    def winfo_screenheight(self) -> int:
        return 720

    def geometry(self, value: str) -> None:
        self.calls.append(("geometry", value))


class GuiTimeFormatTests(unittest.TestCase):
    def test_formats_duration_as_hours_minutes_seconds(self) -> None:
        self.assertEqual(format_duration(0), "00:00:00")
        self.assertEqual(format_duration(65.8), "00:01:05")
        self.assertEqual(format_duration(3661), "01:01:01")

    def test_clamps_negative_duration_to_zero(self) -> None:
        self.assertEqual(format_duration(-3), "00:00:00")

    def test_formats_average_rate(self) -> None:
        self.assertEqual(format_average_rate(25, 10), "2.50 rec/s")

    def test_average_rate_is_unknown_without_processed_rows(self) -> None:
        self.assertEqual(format_average_rate(0, 10), "-")

    def test_formats_estimated_finish_time(self) -> None:
        self.assertEqual(
            format_estimated_finish(
                25,
                50,
                10,
                now=datetime(2026, 9, 5, 12, 0, 0),
            ),
            "2026-09-05 12:00:20",
        )

    def test_estimated_finish_is_unknown_before_progress(self) -> None:
        self.assertEqual(format_estimated_finish(0, 50, 10), "-")

    def test_mousewheel_units_supports_windows_delta(self) -> None:
        self.assertEqual(mousewheel_units(SimpleNamespace(delta=120)), -1)
        self.assertEqual(mousewheel_units(SimpleNamespace(delta=-120)), 1)

    def test_mousewheel_units_supports_linux_buttons(self) -> None:
        self.assertEqual(mousewheel_units(SimpleNamespace(num=4)), -1)
        self.assertEqual(mousewheel_units(SimpleNamespace(num=5)), 1)

    def test_can_scroll_only_when_view_has_room_in_direction(self) -> None:
        self.assertFalse(can_scroll((0.0, 1.0), 1))
        self.assertFalse(can_scroll((0.0, 0.5), -1))
        self.assertFalse(can_scroll((0.5, 1.0), 1))
        self.assertTrue(can_scroll((0.2, 0.8), -1))
        self.assertTrue(can_scroll((0.2, 0.8), 1))

    def test_maximize_window_uses_zoomed_state_first(self) -> None:
        root = FakeRoot()

        maximize_window(root)

        self.assertEqual(root.calls, [("state", "zoomed")])

    def test_maximize_window_falls_back_to_zoomed_attribute(self) -> None:
        root = FakeRoot(state_error=True)

        maximize_window(root)

        self.assertEqual(
            root.calls,
            [
                ("state", "zoomed"),
                ("attributes", ("-zoomed", True)),
            ],
        )

    def test_maximize_window_falls_back_to_screen_geometry(self) -> None:
        root = FakeRoot(state_error=True, attributes_error=True)

        maximize_window(root)

        self.assertEqual(
            root.calls,
            [
                ("state", "zoomed"),
                ("attributes", ("-zoomed", True)),
                ("geometry", "1280x720+0+0"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
