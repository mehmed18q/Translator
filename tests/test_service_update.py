from __future__ import annotations

import logging
import unittest
from dataclasses import replace
from pathlib import Path

from translator_app.config import RetrySettings, RuntimeConfig
from translator_app.languages import get_language
from translator_app.models import ColumnInfo, LocalizeTable
from translator_app.service import DatabaseTranslationService
from translator_app.sqlserver.repository import (
    TARGET_EXISTS_COLUMN_NAME,
    TranslationFailureRecord,
    target_value_column_name,
)
from translator_app.translators.base import Translator


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
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.inserts: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []

    def count_pending_rows(
        self,
        plan: object,
        *,
        source_language_id: int,
        target_language_id: int,
    ) -> int:
        return len(self.rows)

    def iter_pending_source_rows(
        self,
        plan: object,
        *,
        source_language_id: int,
        target_language_id: int,
        batch_size: int,
    ) -> object:
        yield from self.rows

    def insert_translation(
        self,
        plan: object,
        *,
        source_row: dict[str, object],
        translated_values: dict[str, object],
        target_language_id: int,
    ) -> None:
        self.inserts.append(
            {
                "source_row": source_row,
                "translated_values": translated_values,
                "target_language_id": target_language_id,
            }
        )

    def update_translation_columns(
        self,
        plan: object,
        *,
        entity_key_value: object | None = None,
        entity_key_values: dict[str, object] | None = None,
        translated_values: dict[str, object],
        target_language_id: int,
    ) -> int:
        self.updates.append(
            {
                "entity_key_value": entity_key_value,
                "entity_key_values": entity_key_values,
                "translated_values": translated_values,
                "target_language_id": target_language_id,
            }
        )
        return len(translated_values)


class PersistedFailureRepository(FakeRepository):
    def __init__(
        self,
        rows: list[dict[str, object]],
        failures: list[TranslationFailureRecord],
    ) -> None:
        super().__init__(rows)
        self.failures = list(failures)
        self.recorded_failures: list[dict[str, object]] = []
        self.deleted_failures: list[int] = []

    def list_translation_failures(self, **_kwargs: object) -> list[TranslationFailureRecord]:
        return list(self.failures)

    def delete_translation_failure(self, failure_id: int) -> None:
        self.deleted_failures.append(failure_id)
        self.failures = [item for item in self.failures if item.failure_id != failure_id]

    def record_translation_failure(self, **kwargs: object) -> None:
        self.recorded_failures.append(kwargs)


class PrefixTranslator(Translator):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        self.calls.append((text, text_format))
        return f"{target_language}:{text}"


class FailingTranslator(Translator):
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        raise RuntimeError("provider unavailable")


class StrictFormattingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


class ServiceUpdateTests(unittest.TestCase):
    def test_persists_row_when_translation_fails(self) -> None:
        row = {
            "SampleId": 10,
            "Title": "خانه",
            "Description": "توضیح",
            TARGET_EXISTS_COLUMN_NAME: 0,
            target_value_column_name("Title"): None,
            target_value_column_name("Description"): None,
        }
        repository = PersistedFailureRepository([row], [])

        summary = build_service(repository, FailingTranslator()).run(
            replace(build_config(), retry=RetrySettings(attempts=1, initial_delay_seconds=0, backoff_factor=1))
        )

        self.assertEqual(summary.failed_rows, 1)
        self.assertEqual(len(repository.recorded_failures), 1)
        self.assertEqual(repository.recorded_failures[0]["entity_key_values"], {"SampleId": 10})
        self.assertFalse(repository.recorded_failures[0]["target_exists"])

    def test_retries_persisted_failure_before_new_pending_rows(self) -> None:
        source_row = {
            "SampleId": 9,
            "Title": "خانه",
            "Description": "توضیح",
            TARGET_EXISTS_COLUMN_NAME: 0,
            target_value_column_name("Title"): None,
            target_value_column_name("Description"): None,
        }
        failure = TranslationFailureRecord(
            failure_id=44,
            schema_name="dbo",
            table_name="SampleLocalize",
            source_language_id=1,
            target_language_id=2,
            entity_key_values={"SampleId": 9},
            source_row=source_row,
            target_exists=False,
            missing_columns=(),
            failure_reason="previous failure",
            attempt_count=2,
        )
        repository = PersistedFailureRepository([], [failure])
        translator = PrefixTranslator()

        summary = build_service(repository, translator).run(build_config())

        self.assertEqual(summary.inserted_rows, 1)
        self.assertEqual(repository.deleted_failures, [44])
        self.assertEqual(repository.failures, [])
        self.assertEqual(repository.inserts[0]["source_row"], source_row)

    def test_updates_only_empty_target_text_columns(self) -> None:
        row = {
            "SampleId": 1,
            "Title": "خانه",
            "Description": "توضیح",
            TARGET_EXISTS_COLUMN_NAME: 1,
            target_value_column_name("Title"): "Home",
            target_value_column_name("Description"): None,
        }
        repository = FakeRepository([row])
        translator = PrefixTranslator()

        summary = build_service(repository, translator).run(build_config())

        self.assertEqual(summary.inserted_rows, 0)
        self.assertEqual(summary.updated_rows, 1)
        self.assertEqual(summary.skipped_existing_rows, 0)
        self.assertEqual(repository.inserts, [])
        self.assertEqual(
            repository.updates,
            [
                {
                    "entity_key_value": None,
                    "entity_key_values": {"SampleId": 1},
                    "translated_values": {"Description": "en:توضیح"},
                    "target_language_id": 2,
                }
            ],
        )
        self.assertEqual(translator.calls, [("توضیح", "text")])

    def test_skips_update_when_source_text_is_empty(self) -> None:
        row = {
            "SampleId": 1,
            "Title": "خانه",
            "Description": "   ",
            TARGET_EXISTS_COLUMN_NAME: 1,
            target_value_column_name("Title"): "Home",
            target_value_column_name("Description"): None,
        }
        repository = FakeRepository([row])
        translator = PrefixTranslator()

        summary = build_service(repository, translator).run(build_config())

        self.assertEqual(summary.inserted_rows, 0)
        self.assertEqual(summary.updated_rows, 0)
        self.assertEqual(summary.skipped_existing_rows, 1)
        self.assertEqual(repository.updates, [])
        self.assertEqual(translator.calls, [])

    def test_inserts_when_target_row_does_not_exist(self) -> None:
        row = {
            "SampleId": 1,
            "Title": "خانه",
            "Description": "توضیح",
            TARGET_EXISTS_COLUMN_NAME: 0,
            target_value_column_name("Title"): None,
            target_value_column_name("Description"): None,
        }
        repository = FakeRepository([row])
        translator = PrefixTranslator()

        summary = build_service(repository, translator).run(build_config())

        self.assertEqual(summary.inserted_rows, 1)
        self.assertEqual(summary.updated_rows, 0)
        self.assertEqual(repository.updates, [])
        self.assertEqual(
            repository.inserts[0]["translated_values"],
            {"Title": "en:خانه", "Description": "en:توضیح"},
        )

    def test_progress_logging_formats_row_value(self) -> None:
        row = {
            "SampleId": 1,
            "Title": "خانه",
            "Description": "توضیح",
            TARGET_EXISTS_COLUMN_NAME: 1,
            target_value_column_name("Title"): "Home",
            target_value_column_name("Description"): None,
        }
        repository = FakeRepository([row])
        translator = PrefixTranslator()
        handler = StrictFormattingHandler()
        logger = logging.getLogger("test_service_update_progress_logging")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        service = DatabaseTranslationService(
            schema_reader=FakeSchemaReader([build_table()]),
            repository=repository,
            translator=translator,
            logger=logger,
        )

        service.run(build_config())

        self.assertTrue(
            any("row=SampleId=1" in message for message in handler.messages),
            handler.messages,
        )

    def test_processes_multiple_target_languages_in_order(self) -> None:
        row = {
            "SampleId": 1,
            "Title": "خانه",
            "Description": "توضیح",
            TARGET_EXISTS_COLUMN_NAME: 0,
            target_value_column_name("Title"): None,
            target_value_column_name("Description"): None,
        }
        repository = FakeRepository([row])
        translator = PrefixTranslator()
        snapshots = []
        service = DatabaseTranslationService(
            schema_reader=FakeSchemaReader([build_table()]),
            repository=repository,
            translator=translator,
            logger=logging.getLogger("test_service_update_queue"),
            progress_callback=snapshots.append,
        )
        config = replace(
            build_config(),
            target_languages=(get_language(2), get_language(3)),
        )

        summary = service.run(config)

        self.assertEqual(summary.inserted_rows, 2)
        self.assertEqual(
            [item["target_language_id"] for item in repository.inserts],
            [2, 3],
        )
        self.assertEqual(snapshots[-1].completed_target_language_codes, ("en", "ar"))
        self.assertEqual(snapshots[-1].remaining_target_language_codes, ())


def build_service(
    repository: FakeRepository,
    translator: PrefixTranslator,
) -> DatabaseTranslationService:
    return DatabaseTranslationService(
        schema_reader=FakeSchemaReader([build_table()]),
        repository=repository,
        translator=translator,
        logger=logging.getLogger("test_service_update"),
    )


def build_config() -> RuntimeConfig:
    return RuntimeConfig(
        connection_string="",
        source_language=get_language(1),
        target_language=get_language(2),
        dry_run=False,
        schema_name=None,
        table_name=None,
        batch_size=10,
        progress_every=1,
        translator_provider="libretranslate",
        request_timeout_seconds=1,
        request_delay_seconds=0,
        libretranslate_url="http://localhost:5000",
        libretranslate_api_key=None,
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
