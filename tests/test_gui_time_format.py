from __future__ import annotations

import unittest

from translator_app.gui import format_duration


class GuiTimeFormatTests(unittest.TestCase):
    def test_formats_duration_as_hours_minutes_seconds(self) -> None:
        self.assertEqual(format_duration(0), "00:00:00")
        self.assertEqual(format_duration(65.8), "00:01:05")
        self.assertEqual(format_duration(3661), "01:01:01")

    def test_clamps_negative_duration_to_zero(self) -> None:
        self.assertEqual(format_duration(-3), "00:00:00")


if __name__ == "__main__":
    unittest.main()
