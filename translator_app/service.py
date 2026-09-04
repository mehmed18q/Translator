from __future__ import annotations

import logging
from dataclasses import dataclass

from translator_app.config import RuntimeConfig
from translator_app.models import (
    LocalizeTable,
    TableTranslationPlan,
    build_table_translation_plan,
)
from translator_app.retry import run_with_retry
from translator_app.sqlserver.repository import SqlServerLocalizationRepository
from translator_app.sqlserver.schema_reader import SqlServerSchemaReader
from translator_app.translators.base import Translator


@dataclass
class TranslationSummary:
    discovered_tables: int = 0
    eligible_tables: int = 0
    skipped_tables: int = 0
    pending_rows: int = 0
    processed_rows: int = 0
    inserted_rows: int = 0
    skipped_existing_rows: int = 0
    failed_rows: int = 0


class DatabaseTranslationService:
    def __init__(
        self,
        *,
        schema_reader: SqlServerSchemaReader,
        repository: SqlServerLocalizationRepository,
        translator: Translator,
        logger: logging.Logger,
    ) -> None:
        self.schema_reader = schema_reader
        self.repository = repository
        self.translator = translator
        self.logger = logger
        self._translation_cache: dict[tuple[str, str, str], str] = {}

    def run(self, config: RuntimeConfig) -> TranslationSummary:
        self.logger.info(
            "شروع: %s (%s) -> %s (%s) | mode=%s",
            config.source_language.name_fa,
            config.source_language.code,
            config.target_language.name_fa,
            config.target_language.code,
            "dry-run" if config.dry_run else "execute",
        )

        tables = self.schema_reader.get_localize_tables(
            schema_name=config.schema_name,
            table_name=config.table_name,
        )
        summary = TranslationSummary(discovered_tables=len(tables))

        if not tables:
            self.logger.warning("هیچ جدول Localize/Localizes پیدا نشد.")
            return summary

        planned_tables = self._prepare_tables(tables, config, summary)
        if not planned_tables:
            self.logger.warning("هیچ جدول قابل ترجمه‌ای باقی نماند.")
            return summary

        if config.dry_run:
            self.logger.info(
                "Dry-run تمام شد. برای insert واقعی برنامه را با --execute اجرا کنید."
            )
            return summary

        self._translate_and_insert(planned_tables, config, summary)
        self.logger.info(
            "پایان: pending=%s processed=%s inserted=%s skipped_existing=%s failed=%s skipped_tables=%s",
            summary.pending_rows,
            summary.processed_rows,
            summary.inserted_rows,
            summary.skipped_existing_rows,
            summary.failed_rows,
            summary.skipped_tables,
        )
        return summary

    def _prepare_tables(
        self,
        tables: list[LocalizeTable],
        config: RuntimeConfig,
        summary: TranslationSummary,
    ) -> list[tuple[TableTranslationPlan, int]]:
        planned_tables: list[tuple[TableTranslationPlan, int]] = []

        for table in tables:
            try:
                plan = build_table_translation_plan(table)
                pending_count = run_with_retry(
                    lambda table=table: self.repository.count_missing_rows(
                        table,
                        source_language_id=config.source_language.id,
                        target_language_id=config.target_language.id,
                    ),
                    operation_name=f"count {table.display_name}",
                    attempts=config.retry.attempts,
                    initial_delay_seconds=config.retry.initial_delay_seconds,
                    backoff_factor=config.retry.backoff_factor,
                    logger=self.logger,
                )
            except Exception as exc:
                summary.skipped_tables += 1
                self.logger.exception(
                    "جدول %s اسکیپ شد: %s",
                    table.display_name,
                    exc,
                )
                continue

            summary.eligible_tables += 1
            summary.pending_rows += pending_count
            planned_tables.append((plan, pending_count))

            self.logger.info(
                "جدول آماده: %s | base=%s | fk=%s | text_columns=%s | pending=%s",
                table.display_name,
                table.referenced_table_name or "-",
                table.entity_key_column_name,
                ", ".join(plan.text_column_names),
                pending_count,
            )

        self.logger.info(
            "خلاصه آماده‌سازی: discovered=%s eligible=%s skipped=%s pending_rows=%s",
            summary.discovered_tables,
            summary.eligible_tables,
            summary.skipped_tables,
            summary.pending_rows,
        )
        return planned_tables

    def _translate_and_insert(
        self,
        planned_tables: list[tuple[TableTranslationPlan, int]],
        config: RuntimeConfig,
        summary: TranslationSummary,
    ) -> None:
        for table_index, (plan, pending_count) in enumerate(planned_tables, start=1):
            if pending_count == 0:
                continue

            table_processed = 0
            table_failed = 0
            table = plan.table

            for source_row in self.repository.iter_missing_source_rows(
                plan,
                source_language_id=config.source_language.id,
                target_language_id=config.target_language.id,
                batch_size=config.batch_size,
            ):
                entity_value = source_row.get(table.entity_key_column_name or "")
                row_status = "failed"
                try:
                    if self.repository.destination_exists(
                        table,
                        entity_key_value=entity_value,
                        target_language_id=config.target_language.id,
                    ):
                        summary.skipped_existing_rows += 1
                        row_status = "skipped-existing"
                    else:
                        translated_values = self._translate_row(
                            plan,
                            source_row,
                            config,
                        )
                        run_with_retry(
                            lambda: self.repository.insert_translation(
                                plan,
                                source_row=source_row,
                                translated_values=translated_values,
                                target_language_id=config.target_language.id,
                            ),
                            operation_name=(
                                f"insert {table.display_name} "
                                f"{table.entity_key_column_name}={entity_value}"
                            ),
                            attempts=config.retry.attempts,
                            initial_delay_seconds=config.retry.initial_delay_seconds,
                            backoff_factor=config.retry.backoff_factor,
                            logger=self.logger,
                        )
                        summary.inserted_rows += 1
                        row_status = "inserted"
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    summary.failed_rows += 1
                    table_failed += 1
                    row_status = "failed"
                    self.logger.exception(
                        "خطا در رکورد %s.%s=%r: %s",
                        table.display_name,
                        table.entity_key_column_name,
                        entity_value,
                        exc,
                    )
                finally:
                    summary.processed_rows += 1
                    table_processed += 1

                if table_processed % config.progress_every == 0:
                    self.logger.info(
                        "%s | جدول %s/%s %s | %s.%s=%r | status=%s | inserted=%s skipped_existing=%s failed=%s",
                        format_progress(summary.processed_rows, summary.pending_rows),
                        table_index,
                        len(planned_tables),
                        format_progress(table_processed, pending_count),
                        table.display_name,
                        table.entity_key_column_name,
                        entity_value,
                        row_status,
                        summary.inserted_rows,
                        summary.skipped_existing_rows,
                        summary.failed_rows,
                    )

            self.logger.info(
                "اتمام جدول %s | processed=%s failed=%s",
                table.display_name,
                table_processed,
                table_failed,
            )

    def _translate_row(
        self,
        plan: TableTranslationPlan,
        source_row: dict[str, object],
        config: RuntimeConfig,
    ) -> dict[str, object]:
        translated_values: dict[str, object] = {}

        for column_name in plan.text_column_names:
            original_value = source_row.get(column_name)
            if original_value is None:
                translated_values[column_name] = None
                continue

            original_text = str(original_value)
            if not original_text.strip():
                translated_values[column_name] = original_value
                continue

            cache_key = (
                config.source_language.code,
                config.target_language.code,
                original_text,
            )
            if cache_key not in self._translation_cache:
                self._translation_cache[cache_key] = run_with_retry(
                    lambda text=original_text: self.translator.translate(
                        text,
                        config.source_language.code,
                        config.target_language.code,
                    ),
                    operation_name=f"translate column {column_name}",
                    attempts=config.retry.attempts,
                    initial_delay_seconds=config.retry.initial_delay_seconds,
                    backoff_factor=config.retry.backoff_factor,
                    logger=self.logger,
                )
            translated_values[column_name] = self._translation_cache[cache_key]

        return translated_values


def format_progress(done: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[------------------------] 0.00%"
    percent = min(max(done / total, 0), 1)
    filled = int(round(width * percent))
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {percent * 100:6.2f}%"
