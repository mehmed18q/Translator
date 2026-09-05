from __future__ import annotations

import unittest
from unittest.mock import patch

from translator_app.gui import PROJECT_REPOSITORY_URL, open_project_repository


class GuiProjectLinkTests(unittest.TestCase):
    @patch("translator_app.gui.webbrowser.open_new_tab", return_value=True)
    def test_opens_project_repository_in_a_new_browser_tab(self, open_new_tab) -> None:
        self.assertTrue(open_project_repository())
        open_new_tab.assert_called_once_with(PROJECT_REPOSITORY_URL)

    @patch("translator_app.gui.webbrowser.open_new_tab", return_value=False)
    def test_reports_when_browser_cannot_open_repository(self, _open_new_tab) -> None:
        self.assertFalse(open_project_repository())


if __name__ == "__main__":
    unittest.main()
