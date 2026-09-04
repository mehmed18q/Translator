from __future__ import annotations

import unittest

from translator_app.models import ColumnInfo, LocalizeTable, build_table_translation_plan
from translator_app.sqlserver.repository import SqlServerLocalizationRepository


class FakeConnection:
    def __init__(self, batches: list[list[tuple[object, ...]]]) -> None:
        self.batches = batches
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> "FakeCursor":
        rows = self.batches.pop(0) if self.batches else []
        cursor = FakeCursor(rows)
        self.cursors.append(cursor)
        return cursor


class FakeCursor:
    description = [("SampleId",), ("Title",)]

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.sql = ""
        self.params: tuple[object, ...] = ()
        self.closed = False

    def execute(self, sql: str, *params: object) -> "FakeCursor":
        self.sql = sql
        self.params = params
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class RepositoryTests(unittest.TestCase):
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
