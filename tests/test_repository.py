from __future__ import annotations

import unittest

from translator_app.models import ColumnInfo, LocalizeTable, build_table_translation_plan
from translator_app.sqlserver.repository import (
    TARGET_EXISTS_COLUMN_NAME,
    SqlServerLocalizationRepository,
    TranslationFailureRecord,
    target_value_column_name,
)


class FakeConnection:
    def __init__(
        self,
        batches: list[list[tuple[object, ...]]],
        description: list[tuple[str]] | None = None,
    ) -> None:
        self.batches = batches
        self.cursors: list[FakeCursor] = []
        self.description = description or [("SampleId",), ("Title",)]

    def cursor(self) -> "FakeCursor":
        rows = self.batches.pop(0) if self.batches else []
        cursor = FakeCursor(rows, self.description)
        self.cursors.append(cursor)
        return cursor


class FakeCursor:
    def __init__(
        self,
        rows: list[tuple[object, ...]],
        description: list[tuple[str]],
    ) -> None:
        self.rows = rows
        self.description = description
        self.sql = ""
        self.params: tuple[object, ...] = ()
        self.closed = False
        self.rowcount = 1

    def execute(self, sql: str, *params: object) -> "FakeCursor":
        self.sql = sql
        self.params = params
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        self.closed = True


class RepositoryTests(unittest.TestCase):
    def test_warmup_creates_failure_log_table_and_indexes_idempotently(self) -> None:
        connection = FakeConnection(batches=[])
        repository = SqlServerLocalizationRepository(connection)

        repository.ensure_translation_failure_log_table()

        self.assertEqual(len(connection.cursors), 1)
        sql = connection.cursors[0].sql
        self.assertIn("CREATE TABLE [dbo].[TranslatorTranslationFailureLog]", sql)
        self.assertIn("UX_TranslatorTranslationFailureLog_Fingerprint", sql)
        self.assertIn("IX_TranslatorTranslationFailureLog_Queue", sql)

    def test_reads_persisted_translation_failures(self) -> None:
        connection = FakeConnection(
            batches=[
                [
                    (
                        7,
                        "dbo",
                        "SampleLocalize",
                        1,
                        2,
                        '{"SampleId":7}',
                        '{"SampleId":7,"Title":"خانه"}',
                        0,
                        '["Title"]',
                        "translation provider failed",
                        3,
                    )
                ]
            ]
        )
        repository = SqlServerLocalizationRepository(connection)

        failures = repository.list_translation_failures(
            source_language_id=1,
            target_language_id=2,
        )

        self.assertEqual(
            failures,
            [
                TranslationFailureRecord(
                    failure_id=7,
                    schema_name="dbo",
                    table_name="SampleLocalize",
                    source_language_id=1,
                    target_language_id=2,
                    entity_key_values={"SampleId": 7},
                    source_row={"SampleId": 7, "Title": "خانه"},
                    target_exists=False,
                    missing_columns=("Title",),
                    failure_reason="translation provider failed",
                    attempt_count=3,
                )
            ],
        )
        self.assertIn("TranslatorTranslationFailureLog", connection.cursors[0].sql)
        self.assertEqual(connection.cursors[0].params, (1, 2))

    def test_records_failure_with_stable_fingerprint_and_deletes_by_id(self) -> None:
        connection = FakeConnection(batches=[[], []])
        repository = SqlServerLocalizationRepository(connection)
        table = build_table()

        repository.record_translation_failure(
            table=table,
            source_language_id=1,
            target_language_id=2,
            entity_key_values={"SampleId": 7},
            source_row={"SampleId": 7, "Title": "خانه"},
            target_exists=False,
            missing_columns=("Title",),
            failure_reason="failed",
        )
        repository.delete_translation_failure(12)

        self.assertIn("UPDATE [dbo].[TranslatorTranslationFailureLog]", connection.cursors[0].sql)
        self.assertIn("INSERT INTO [dbo].[TranslatorTranslationFailureLog]", connection.cursors[0].sql)
        self.assertEqual(connection.cursors[1].params, (12,))

    def test_reads_current_target_state_for_retry_after_unknown_commit_result(self) -> None:
        connection = FakeConnection(
            batches=[[("Home", None)]],
            description=[("Title",), ("Description",)],
        )
        repository = SqlServerLocalizationRepository(connection)
        plan = build_table_translation_plan(build_table_with_description())

        state = repository.get_target_state(
            plan,
            entity_key_values={"SampleId": 7},
            target_language_id=2,
        )

        self.assertEqual(
            state,
            {
                TARGET_EXISTS_COLUMN_NAME: 1,
                target_value_column_name("Title"): "Home",
                target_value_column_name("Description"): None,
            },
        )
        self.assertIn("WHERE [SampleId] = ? AND [LanguageId] = ?", connection.cursors[0].sql)
        self.assertEqual(connection.cursors[0].params, (7, 2))

    def test_counts_rows_when_all_textual_content_columns_are_blank(self) -> None:
        connection = FakeConnection(batches=[[(4,)]])
        repository = SqlServerLocalizationRepository(connection)
        table = build_table_with_description_and_internal_memo()

        count = repository.count_empty_localized_rows(table, language_id=2)

        self.assertEqual(count, 4)
        cursor = connection.cursors[0]
        self.assertEqual(cursor.params, (2,))
        self.assertIn("FROM [dbo].[SampleLocalize]", cursor.sql)
        self.assertIn("[Title] AS NVARCHAR(MAX)", cursor.sql)
        self.assertIn("[Description] AS NVARCHAR(MAX)", cursor.sql)
        self.assertIn("[InternalMemo] AS NVARCHAR(MAX)", cursor.sql)
        self.assertNotIn("CAST([SampleId]", cursor.sql)

    def test_deletes_empty_rows_in_a_bounded_batch_for_one_language(self) -> None:
        connection = FakeConnection(batches=[[(25,)]])
        repository = SqlServerLocalizationRepository(connection)

        deleted = repository.delete_empty_localized_rows(
            build_table_with_description(),
            language_id=3,
            batch_size=25,
        )

        self.assertEqual(deleted, 25)
        cursor = connection.cursors[0]
        self.assertIn("DELETE TOP (25)", cursor.sql)
        self.assertIn("[LanguageId] = ?", cursor.sql)
        self.assertIn("SELECT CAST(@@ROWCOUNT AS BIGINT)", cursor.sql)
        self.assertEqual(cursor.params, (3,))

    def test_iter_missing_source_rows_reads_batches_by_entity_key(self) -> None:
        connection = FakeConnection(
            batches=[
                [(1, "One"), (2, "Two")],
                [(5, "Five")],
                [],
            ]
        )
        repository = SqlServerLocalizationRepository(connection)
        plan = build_table_translation_plan(build_table())

        rows = list(
            repository.iter_missing_source_rows(
                plan,
                source_language_id=1,
                target_language_id=2,
                batch_size=2,
            )
        )

        self.assertEqual(
            rows,
            [
                {"SampleId": 1, "Title": "One"},
                {"SampleId": 2, "Title": "Two"},
                {"SampleId": 5, "Title": "Five"},
            ],
        )
        self.assertIn("TOP (2)", connection.cursors[0].sql)
        self.assertNotIn("src.[SampleId] > ?", connection.cursors[0].sql)
        self.assertIn("src.[SampleId] > ?", connection.cursors[1].sql)
        self.assertEqual(connection.cursors[1].params, (1, 2, 2))
        self.assertEqual(connection.cursors[2].params, (1, 2, 5))
        self.assertTrue(all(cursor.closed for cursor in connection.cursors))

    def test_iter_pending_source_rows_includes_existing_target_text_values(self) -> None:
        connection = FakeConnection(
            batches=[
                [(1, "One", "Desc", 1, "One target", None)],
                [],
            ],
            description=[
                ("SampleId",),
                ("Title",),
                ("Description",),
                (TARGET_EXISTS_COLUMN_NAME,),
                (target_value_column_name("Title"),),
                (target_value_column_name("Description"),),
            ],
        )
        repository = SqlServerLocalizationRepository(connection)
        plan = build_table_translation_plan(build_table_with_description())

        rows = list(
            repository.iter_pending_source_rows(
                plan,
                source_language_id=1,
                target_language_id=2,
                batch_size=10,
            )
        )

        self.assertEqual(
            rows,
            [
                {
                    "SampleId": 1,
                    "Title": "One",
                    "Description": "Desc",
                    TARGET_EXISTS_COLUMN_NAME: 1,
                    target_value_column_name("Title"): "One target",
                    target_value_column_name("Description"): None,
                }
            ],
        )
        self.assertIn(
            "LEFT JOIN [dbo].[SampleLocalize] AS dst",
            connection.cursors[0].sql,
        )
        self.assertIn("dst.[LanguageId] IS NULL", connection.cursors[0].sql)
        self.assertIn(
            "CAST(dst.[Description] AS NVARCHAR(MAX))",
            connection.cursors[0].sql,
        )
        self.assertIn(
            "CAST(src.[Description] AS NVARCHAR(MAX))",
            connection.cursors[0].sql,
        )
        self.assertIn("IS NOT NULL", connection.cursors[0].sql)
        self.assertEqual(connection.cursors[0].params, (2, 1))

    def test_update_translation_columns_only_updates_empty_target_columns(self) -> None:
        connection = FakeConnection(batches=[[]])
        repository = SqlServerLocalizationRepository(connection)
        plan = build_table_translation_plan(build_table_with_description())

        updated_columns = repository.update_translation_columns(
            plan,
            entity_key_value=7,
            translated_values={"Description": "Translated"},
            target_language_id=2,
        )

        self.assertEqual(updated_columns, 1)
        self.assertIn("UPDATE [dbo].[SampleLocalize]", connection.cursors[0].sql)
        self.assertIn("SET [Description] = ?", connection.cursors[0].sql)
        self.assertIn(
            "NULLIF(LTRIM(RTRIM(CAST([Description] AS NVARCHAR(MAX)))), N'') IS NULL",
            connection.cursors[0].sql,
        )
        self.assertEqual(connection.cursors[0].params, ("Translated", 7, 2))

    def test_system_messages_pending_rows_match_state_and_message_key(self) -> None:
        connection = FakeConnection(
            batches=[
                [(3, "Welcome", "خوش آمدید", 1, None)],
                [],
            ],
            description=[
                ("SystemMessageStateId",),
                ("MessageKey",),
                ("Value",),
                (TARGET_EXISTS_COLUMN_NAME,),
                (target_value_column_name("Value"),),
            ],
        )
        repository = SqlServerLocalizationRepository(connection)
        plan = build_table_translation_plan(build_system_messages_table())

        rows = list(
            repository.iter_pending_source_rows(
                plan,
                source_language_id=1,
                target_language_id=2,
                batch_size=10,
            )
        )

        self.assertEqual(rows[0]["SystemMessageStateId"], 3)
        self.assertEqual(rows[0]["MessageKey"], "Welcome")
        self.assertIn(
            "dst.[SystemMessageStateId] = src.[SystemMessageStateId]",
            connection.cursors[0].sql,
        )
        self.assertIn(
            "dst.[MessageKey] = src.[MessageKey]",
            connection.cursors[0].sql,
        )
        self.assertIn(
            "ORDER BY src.[SystemMessageStateId], src.[MessageKey]",
            connection.cursors[0].sql,
        )

    def test_system_messages_update_matches_state_and_message_key(self) -> None:
        connection = FakeConnection(batches=[[]])
        repository = SqlServerLocalizationRepository(connection)
        plan = build_table_translation_plan(build_system_messages_table())

        repository.update_translation_columns(
            plan,
            entity_key_values={
                "SystemMessageStateId": 3,
                "MessageKey": "Welcome",
            },
            translated_values={"Value": "Welcome"},
            target_language_id=2,
        )

        self.assertIn("UPDATE [dbo].[SystemMessages]", connection.cursors[0].sql)
        self.assertIn("[SystemMessageStateId] = ?", connection.cursors[0].sql)
        self.assertIn("[MessageKey] = ?", connection.cursors[0].sql)
        self.assertEqual(connection.cursors[0].params, ("Welcome", 3, "Welcome", 2))


def build_table() -> LocalizeTable:
    return LocalizeTable(
        schema_name="dbo",
        table_name="SampleLocalize",
        object_id=1,
        columns=(
            column("Id", "int", is_identity=True, is_primary_key=True),
            column("SampleId", "int"),
            column("LanguageId", "int"),
            column("Title", "nvarchar"),
        ),
        foreign_keys=(),
        language_column_name="LanguageId",
        entity_key_column_name="SampleId",
        referenced_table_name="Sample",
    )


def build_table_with_description() -> LocalizeTable:
    return LocalizeTable(
        schema_name="dbo",
        table_name="SampleLocalize",
        object_id=1,
        columns=(
            column("Id", "int", is_identity=True, is_primary_key=True),
            column("SampleId", "int"),
            column("LanguageId", "int"),
            column("Title", "nvarchar"),
            column("Description", "nvarchar"),
        ),
        foreign_keys=(),
        language_column_name="LanguageId",
        entity_key_column_name="SampleId",
        referenced_table_name="Sample",
    )


def build_table_with_description_and_internal_memo() -> LocalizeTable:
    table = build_table_with_description()
    return LocalizeTable(
        schema_name=table.schema_name,
        table_name=table.table_name,
        object_id=table.object_id,
        columns=(*table.columns, column("InternalMemo", "nvarchar")),
        foreign_keys=table.foreign_keys,
        language_column_name=table.language_column_name,
        entity_key_column_name=table.entity_key_column_name,
        referenced_table_name=table.referenced_table_name,
    )


def build_system_messages_table() -> LocalizeTable:
    return LocalizeTable(
        schema_name="dbo",
        table_name="SystemMessages",
        object_id=2,
        columns=(
            column("SystemMessageStateId", "int"),
            column("LanguageId", "int"),
            column("MessageKey", "nvarchar"),
            column("Value", "nvarchar"),
        ),
        foreign_keys=(),
        language_column_name="LanguageId",
        entity_key_column_name="MessageKey",
        referenced_table_name="SystemMessages",
        entity_key_column_names=("SystemMessageStateId", "MessageKey"),
    )


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


if __name__ == "__main__":
    unittest.main()
