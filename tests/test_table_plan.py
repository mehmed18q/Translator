from __future__ import annotations

import unittest

from translator_app.cli import split_table_reference
from translator_app.languages import get_language
from translator_app.models import (
    ColumnInfo,
    ForeignKeyInfo,
    LocalizeTable,
    build_table_translation_plan,
)
from translator_app.sqlserver.schema_reader import find_entity_key_column


def column(
    name: str,
    data_type: str,
    *,
    is_identity: bool = False,
    is_primary_key: bool = False,
) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        data_type=data_type,
        max_length=None,
        is_nullable=False,
        is_identity=is_identity,
        is_computed=False,
        has_default=False,
        is_primary_key=is_primary_key,
    )


class TablePlanTests(unittest.TestCase):
    def test_split_table_reference_accepts_schema_dot_table(self) -> None:
        self.assertEqual(
            split_table_reference("dbo.SiteMenuLocalize", None),
            ("dbo", "SiteMenuLocalize"),
        )

    def test_split_table_reference_accepts_separate_schema(self) -> None:
        self.assertEqual(
            split_table_reference("SiteMenuLocalize", "dbo"),
            ("dbo", "SiteMenuLocalize"),
        )

    def test_language_codes_include_chinese_and_russian(self) -> None:
        self.assertEqual(get_language(5).code, "zh")
        self.assertEqual(get_language(6).code, "ru")

    def test_infers_entity_key_from_foreign_key(self) -> None:
        columns = (
            column("Id", "int", is_identity=True, is_primary_key=True),
            column("SiteMenuId", "int"),
            column("LanguageId", "int"),
            column("Title", "nvarchar"),
        )
        foreign_keys = (
            ForeignKeyInfo("FK", "SiteMenuId", "dbo", "SiteMenu", "Id"),
        )

        entity_key, referenced_table = find_entity_key_column(
            table_name="SiteMenuLocalize",
            columns=columns,
            foreign_keys=foreign_keys,
        )

        self.assertEqual(entity_key, "SiteMenuId")
        self.assertEqual(referenced_table, "SiteMenu")

    def test_builds_insert_plan_for_common_identity_table(self) -> None:
        table = LocalizeTable(
            schema_name="dbo",
            table_name="SiteMenuLocalize",
            object_id=1,
            columns=(
                column("Id", "int", is_identity=True, is_primary_key=True),
                column("SiteMenuId", "int"),
                column("LanguageId", "int"),
                column("Title", "nvarchar"),
                column("Description", "nvarchar"),
                column("SortOrder", "int"),
            ),
            foreign_keys=(),
            language_column_name="LanguageId",
            entity_key_column_name="SiteMenuId",
            referenced_table_name="SiteMenu",
        )

        plan = build_table_translation_plan(table)

        self.assertEqual(plan.text_column_names, ("Title", "Description"))
        self.assertEqual(
            [item.column.name for item in plan.insert_columns],
            ["SiteMenuId", "LanguageId", "Title", "Description", "SortOrder"],
        )


if __name__ == "__main__":
    unittest.main()
