from __future__ import annotations

import unittest

from translator_app.cli import split_table_reference
from translator_app.config import SqlServerConnectionSettings
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
    def test_connection_string_prefers_explicit_credentials(self) -> None:
        settings = SqlServerConnectionSettings(
            connection_string=None,
            driver="ODBC Driver 18 for SQL Server",
            server="localhost",
            database="AppDb",
            username="sa",
            password="Password",
            trusted_connection=True,
            encrypt=True,
            trust_server_certificate=True,
        )

        connection_string = settings.build_connection_string()

        self.assertIn("UID=sa", connection_string)
        self.assertIn("PWD=Password", connection_string)
        self.assertNotIn("Trusted_Connection=yes", connection_string)

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

    def test_infers_resource_key_for_resource_table(self) -> None:
        columns = (
            column("Id", "int", is_identity=True, is_primary_key=True),
            column("LanguageId", "int"),
            column("Key", "nvarchar"),
            column("Value", "nvarchar"),
        )

        entity_key, referenced_table = find_entity_key_column(
            schema_name="dbo",
            table_name="Resource",
            columns=columns,
            foreign_keys=(),
        )

        self.assertEqual(entity_key, "Key")
        self.assertEqual(referenced_table, "Resource")

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

    def test_only_allow_listed_text_columns_are_translated(self) -> None:
        table = LocalizeTable(
            schema_name="dbo",
            table_name="SampleLocalize",
            object_id=2,
            columns=(
                column("Id", "int", is_identity=True, is_primary_key=True),
                column("SampleId", "int"),
                column("LanguageId", "int"),
                column("Title", "nvarchar"),
                column("InternalMemo", "nvarchar"),
            ),
            foreign_keys=(),
            language_column_name="LanguageId",
            entity_key_column_name="SampleId",
            referenced_table_name="Sample",
        )

        plan = build_table_translation_plan(table)
        insert_modes = {
            item.column.name: item.mode
            for item in plan.insert_columns
        }

        self.assertEqual(plan.text_column_names, ("Title",))
        self.assertEqual(insert_modes["Title"], "translated_text")
        self.assertEqual(insert_modes["InternalMemo"], "copy_from_source")

    def test_resource_table_translates_value_by_key(self) -> None:
        table = LocalizeTable(
            schema_name="dbo",
            table_name="Resource",
            object_id=3,
            columns=(
                column("Id", "int", is_identity=True, is_primary_key=True),
                column("LanguageId", "int"),
                column("Key", "nvarchar"),
                column("Value", "nvarchar"),
            ),
            foreign_keys=(),
            language_column_name="LanguageId",
            entity_key_column_name="Key",
            referenced_table_name="Resource",
        )

        plan = build_table_translation_plan(table)
        insert_modes = {
            item.column.name: item.mode
            for item in plan.insert_columns
        }

        self.assertEqual(plan.text_column_names, ("Value",))
        self.assertEqual(insert_modes["LanguageId"], "target_language")
        self.assertEqual(insert_modes["Key"], "copy_from_source")
        self.assertEqual(insert_modes["Value"], "translated_text")

    def test_value_is_not_translated_in_regular_localize_tables(self) -> None:
        table = LocalizeTable(
            schema_name="dbo",
            table_name="SampleLocalize",
            object_id=4,
            columns=(
                column("Id", "int", is_identity=True, is_primary_key=True),
                column("SampleId", "int"),
                column("LanguageId", "int"),
                column("Title", "nvarchar"),
                column("Value", "nvarchar"),
            ),
            foreign_keys=(),
            language_column_name="LanguageId",
            entity_key_column_name="SampleId",
            referenced_table_name="Sample",
        )

        plan = build_table_translation_plan(table)
        insert_modes = {
            item.column.name: item.mode
            for item in plan.insert_columns
        }

        self.assertEqual(plan.text_column_names, ("Title",))
        self.assertEqual(insert_modes["Value"], "copy_from_source")


if __name__ == "__main__":
    unittest.main()
