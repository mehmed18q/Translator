from __future__ import annotations

import logging
import unittest
from pathlib import Path

from translator_app.cleanup_service import CleanupConfig, DatabaseCleanupService
from translator_app.config import RetrySettings
from translator_app.languages import get_language
from translator_app.models import ColumnInfo, LocalizeTable


class FakeSchemaReader:
    def __init__(self, tables: list[LocalizeTable]) -> None:
        self.tables = tables

    def get_localize_tables(
        self,
        *,
        schema_name: str | None = None,
        table_name: str | None = None,
    ) -> list[LocalizeTable]:
        return self.tables


class FakeRepository:
    def __init__(self, counts: dict[tuple[int, int], int]) -> None:
        self.remaining = dict(counts)
        self.deleted: list[tuple[str, int, int]] = []

    def count_empty_localized_rows(
        self,
        table: LocalizeTable,
        *,
        language_id: int,
    ) -> int:
        return self.remaining.get((language_id, table.object_id), 0)

    def delete_empty_localized_rows(
        self,
        table: LocalizeTable,
        *,
        language_id: int,
        batch_size: int,
    ) -> int:
        key = (language_id, table.object_id)
        deleted = min(self.remaining.get(key, 0), batch_size)
        self.remaining[key] = self.remaining.get(key, 0) - deleted
        self.deleted.append((table.display_name, language_id, deleted))
        return deleted


class CleanupServiceTests(unittest.TestCase):
    def test_deletes_matching_rows_in_language_queue_and_batches(self) -> None:
        table = build_table()
        repository = FakeRepository({(2, 1): 5, (3, 1): 2})
        snapshots = []
        service = DatabaseCleanupService(
            schema_reader=FakeSchemaReader([table]),
            repository=repository,
            logger=logging.getLogger("cleanup-service-test"),
            progress_callback=snapshots.append,
        )

        summary = service.run(build_config(dry_run=False))

        self.assertEqual(summary.matched_rows, 7)
        self.assertEqual(summary.processed_rows, 7)
        self.assertEqual(summary.deleted_rows, 7)
        self.assertEqual(
            repository.deleted,
            [
                ("dbo.SampleLocalize", 2, 2),
                ("dbo.SampleLocalize", 2, 2),
                ("dbo.SampleLocalize", 2, 1),
                ("dbo.SampleLocalize", 3, 2),
            ],
        )
        self.assertEqual(snapshots[-1].completed_target_language_codes, ("en", "ar"))
        self.assertEqual(snapshots[-1].percent, 100.0)

    def test_dry_run_only_counts_and_does_not_delete(self) -> None:
        repository = FakeRepository({(2, 1): 3, (3, 1): 1})
        service = DatabaseCleanupService(
            schema_reader=FakeSchemaReader([build_table()]),
            repository=repository,
            logger=logging.getLogger("cleanup-service-dry-run-test"),
        )

        summary = service.run(build_config(dry_run=True))

        self.assertEqual(summary.matched_rows, 4)
        self.assertEqual(summary.deleted_rows, 0)
        self.assertEqual(repository.deleted, [])


def build_config(*, dry_run: bool) -> CleanupConfig:
    return CleanupConfig(
        connection_string="unused",
        languages=(get_language(2), get_language(3)),
        dry_run=dry_run,
        schema_name=None,
        table_name=None,
        batch_size=2,
        log_dir=Path("logs"),
        retry=RetrySettings(
            attempts=1,
            initial_delay_seconds=0,
            backoff_factor=1,
        ),
    )


def build_table() -> LocalizeTable:
    return LocalizeTable(
        schema_name="dbo",
        table_name="SampleLocalize",
        object_id=1,
        columns=(
            column("Id", "int", is_primary_key=True),
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


def column(
    name: str,
    data_type: str,
    *,
    is_primary_key: bool = False,
) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        data_type=data_type,
        max_length=None,
        is_nullable=True,
        is_identity=False,
        is_computed=False,
        has_default=False,
        is_primary_key=is_primary_key,
    )


if __name__ == "__main__":
    unittest.main()
