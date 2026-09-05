from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from translator_app.runtime_paths import application_dir, resolve_application_path


class RuntimePathTests(unittest.TestCase):
    def test_source_run_uses_current_working_directory(self) -> None:
        with patch("translator_app.runtime_paths.Path.cwd", return_value=Path("/work")):
            self.assertEqual(application_dir(), Path("/work"))

    def test_frozen_run_uses_executable_directory(self) -> None:
        app_dir = Path.cwd() / "apps" / "Translator"
        with (
            patch("translator_app.runtime_paths.sys.frozen", True, create=True),
            patch(
                "translator_app.runtime_paths.sys.executable",
                str(app_dir / "Translator.exe"),
            ),
        ):
            self.assertEqual(application_dir(), app_dir)

    def test_relative_path_is_placed_under_application_directory(self) -> None:
        with patch(
            "translator_app.runtime_paths.application_dir",
            return_value=Path("/apps/Translator"),
        ):
            self.assertEqual(
                resolve_application_path(Path("logs")),
                Path("/apps/Translator/logs"),
            )

    def test_absolute_path_is_unchanged(self) -> None:
        path = Path.cwd() / "var" / "log" / "translator"
        self.assertEqual(resolve_application_path(path), path)


if __name__ == "__main__":
    unittest.main()
