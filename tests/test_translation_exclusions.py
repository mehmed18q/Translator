from __future__ import annotations

import unittest

from translator_app.config import (
    DEFAULT_EXCLUDED_TRANSLATION_TABLES,
    is_default_excluded_translation_table,
)
from translator_app.gui import TableSelectionItem, table_checked_by_default


class TranslationExclusionTests(unittest.TestCase):
    def test_gui_defaults_excluded_translation_tables_to_unchecked(self) -> None:
        for name in DEFAULT_EXCLUDED_TRANSLATION_TABLES:
            with self.subTest(table=name):
                item = TableSelectionItem(display_name=f"dbo.{name}")
                self.assertFalse(table_checked_by_default(item))
                self.assertTrue(table_checked_by_default(item, cleanup=True))

        self.assertTrue(table_checked_by_default(TableSelectionItem("dbo.SampleLocalize")))
        self.assertFalse(
            table_checked_by_default(
                TableSelectionItem("dbo.SampleLocalize", eligible=False)
            )
        )

    def test_exclusion_match_ignores_schema_and_case(self) -> None:
        self.assertTrue(is_default_excluded_translation_table("Other.SITEMENUSLOCALIZE"))
        self.assertFalse(is_default_excluded_translation_table("dbo.SiteMenuLocalize"))


if __name__ == "__main__":
    unittest.main()
