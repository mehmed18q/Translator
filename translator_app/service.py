from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from translator_app.config import RuntimeConfig
from translator_app.models import (
    LocalizeTable,
    TableTranslationPlan,
    build_table_translation_plan,
)
from translator_app.pause_control import (
    CancelCallback,
    PauseCallback,
    wait_while_paused,
)
from translator_app.retry import run_with_retry
from translator_app.sqlserver.repository import (
    TARGET_EXISTS_COLUMN_NAME,
    SqlServerLocalizationRepository,
    target_value_column_name,
)
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
    updated_rows: int = 0
    skipped_existing_rows: int = 0
    failed_rows: int = 0


@dataclass(frozen=True)
class ProgressSnapshot:
    phase: str
    discovered_tables: int
    eligible_tables: int
    skipped_tables: int
    pending_rows: int
    processed_rows: int
    remaining_rows: int
    inserted_rows: int
    updated_rows: int
    skipped_existing_rows: int
    failed_rows: int
    percent: float
    current_table: str | None = None
    current_table_index: int = 0
    total_tables: int = 0
    table_pending_rows: int = 0
    table_processed_rows: int = 0
    table_remaining_rows: int = 0
    table_percent: float = 0.0


ProgressCallback = Callable[[ProgressSnapshot], None]
HTML_COLUMN_NAMES = {"htmlcontent"}
HTML_PATTERN = re.compile(
    r"</?[a-zA-Z][a-zA-Z0-9:-]*(?:\s+[^<>]*)?>|&(?:[a-zA-Z]+|#[0-9]+|#x[0-9a-fA-F]+);"
)


class DatabaseTranslationService:
    def __init__(
        self,
        *,
        schema_reader: SqlServerSchemaReader,
        repository: SqlServerLocalizationRepository,
        translator: Translator,
        logger: logging.Logger,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
        pause_callback: PauseCallback | None = None,
    ) -> None:
        self.schema_reader = schema_reader
        self.repository = repository
        self.translator = translator
        self.logger = logger
        self._translation_cache: dict[tuple[str, str, str], str] = {}
        self.progress_callback = progress_callback
        self.cancel_callback = cancel_callback
        self.pause_callback = pause_callback

    def run(self, config: RuntimeConfig) -> TranslationSummary:
        self.logger.info(
            "Starting translation: source=%s target=%s | mode=%s",
            config.source_language.code,
            config.target_language.code,
            "dry-run" if config.dry_run else "execute",
        )

        summary = TranslationSummary()
        if self._should_stop():
            self.logger.warning("Operation stopped before table discovery.")
            return summary

        tables = self.schema_reader.get_localize_tables(
            schema_name=config.schema_name,
            table_name=config.table_name,
        )
        summary.discovered_tables = len(tables)
        self._emit_progress(summary, phase="discovered", total_tables=len(tables))

        if not tables:
            self.logger.warning("No translation tables were found.")
            return summary

        planned_tables = self._prepare_tables(tables, config, summary)
        self._emit_progress(summary, phase="prepared", total_tables=len(tables))
        if not planned_tables:
            self.logger.warning("No eligible translatable tables remained.")
            return summary

        if config.dry_run:
            self.logger.info(
                "Dry-run finished. Run with --execute to insert or update translated rows."
            )
            self._emit_progress(summary, phase="finished", total_tables=len(tables))
            return summary

        self._translate_and_insert(planned_tables, config, summary)
        self.logger.info(
            "Finished: pending=%s processed=%s inserted=%s updated=%s skipped_existing=%s failed=%s skipped_tables=%s",
            summary.pending_rows,
            summary.processed_rows,
            summary.inserted_rows,
            summary.updated_rows,
            summary.skipped_existing_rows,
            summary.failed_rows,
            summary.skipped_tables,
        )
        self._emit_progress(summary, phase="finished", total_tables=len(planned_tables))
        return summary

    def _prepare_tables(
        self,
        tables: list[LocalizeTable],
        config: RuntimeConfig,
        summary: TranslationSummary,
    ) -> list[tuple[TableTranslationPlan, int]]:
        planned_tables: list[tuple[TableTranslationPlan, int]] = []

        for table in tables:
            if self._should_stop():
                self.logger.warning("Operation stopped during preparation.")
                break

            try:
                plan = build_table_translation_plan(table)
                pending_count = run_with_retry(
                    lambda plan=plan: self.repository.count_pending_rows(
                        plan,
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
                    "Table skipped: %s | reason=%s",
                    table.display_name,
                    exc,
                )
                continue

            summary.eligible_tables += 1
            summary.pending_rows += pending_count
            planned_tables.append((plan, pending_count))
            self._emit_progress(
                summary,
                phase="prepare",
                current_table=table.display_name,
                current_table_index=len(planned_tables),
                total_tables=len(tables),
            )

            self.logger.info(
                "Table ready: %s | base=%s | fk=%s | translatable_columns=%s | pending=%s",
                table.display_name,
                table.referenced_table_name or "-",
                ",".join(table.key_column_names),
                ", ".join(plan.text_column_names),
                pending_count,
            )

        self.logger.info(
            "Preparation summary: discovered=%s eligible=%s skipped=%s pending_rows=%s",
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
            if self._should_stop():
                self.logger.warning("Operation stopped before the next table.")
                return

            if pending_count == 0:
                self._emit_progress(
                    summary,
                    phase="table-finished",
                    current_table=plan.table.display_name,
                    current_table_index=table_index,
                    total_tables=len(planned_tables),
                    table_pending_rows=0,
                    table_processed_rows=0,
                )
                continue

            table_processed = 0
            table_failed = 0
            table = plan.table

            self.logger.info(
                "Table started: %s | table=%s/%s | pending=%s | key=%s",
                table.display_name,
                table_index,
                len(planned_tables),
                pending_count,
                ",".join(table.key_column_names),
            )
            try:
                for source_row in self.repository.iter_pending_source_rows(
                    plan,
                    source_language_id=config.source_language.id,
                    target_language_id=config.target_language.id,
                    batch_size=config.batch_size,
                ):
                    if self._should_stop():
                        self.logger.warning("Operation stopped by user request.")
                        return

                    entity_key_values = self._entity_key_values(plan, source_row)
                    entity_value = format_entity_key_values(entity_key_values)
                    row_status = "failed"
                    try:
                        target_exists = bool(source_row.get(TARGET_EXISTS_COLUMN_NAME))
                        if not target_exists:
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
                                    f"{entity_value}"
                                ),
                                attempts=config.retry.attempts,
                                initial_delay_seconds=config.retry.initial_delay_seconds,
                                backoff_factor=config.retry.backoff_factor,
                                logger=self.logger,
                            )
                            summary.inserted_rows += 1
                            row_status = "inserted"
                        else:
                            missing_columns = self._missing_target_text_columns(
                                plan,
                                source_row,
                            )
                            if missing_columns:
                                translated_values = self._translate_row(
                                    plan,
                                    source_row,
                                    config,
                                    column_names=missing_columns,
                                )
                                updated_columns = run_with_retry(
                                    lambda: self.repository.update_translation_columns(
                                        plan,
                                        entity_key_values=entity_key_values,
                                        translated_values=translated_values,
                                        target_language_id=config.target_language.id,
                                    ),
                                    operation_name=(
                                        f"update {table.display_name} "
                                        f"{entity_value}"
                                    ),
                                    attempts=config.retry.attempts,
                                    initial_delay_seconds=config.retry.initial_delay_seconds,
                                    backoff_factor=config.retry.backoff_factor,
                                    logger=self.logger,
                                )
                                if updated_columns:
                                    summary.updated_rows += 1
                                    row_status = (
                                        "updated:"
                                        + ",".join(translated_values.keys())
                                    )
                                else:
                                    summary.skipped_existing_rows += 1
                                    row_status = "skipped-existing"
                            else:
                                summary.skipped_existing_rows += 1
                                row_status = "skipped-existing"
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        summary.failed_rows += 1
                        table_failed += 1
                        row_status = "failed"
                        self.logger.exception(
                            "Row failed: %s | %s | reason=%s",
                            table.display_name,
                            entity_value,
                            exc,
                        )
                    finally:
                        summary.processed_rows += 1
                        table_processed += 1

                    if table_processed % config.progress_every == 0:
                        self._emit_progress(
                            summary,
                            phase="running",
                            current_table=table.display_name,
                            current_table_index=table_index,
                            total_tables=len(planned_tables),
                            table_pending_rows=pending_count,
                            table_processed_rows=table_processed,
                        )
                        self.logger.info(
                            "%s | table %s/%s %s | %s | key=%s | row=%s | status=%s | inserted=%s updated=%s skipped_existing=%s failed=%s",
                            format_progress(summary.processed_rows, summary.pending_rows),
                            table_index,
                            len(planned_tables),
                            format_progress(table_processed, pending_count),
                            table.display_name,
                            ",".join(table.key_column_names),
                            entity_value,
                            row_status,
                            summary.inserted_rows,
                            summary.updated_rows,
                            summary.skipped_existing_rows,
                            summary.failed_rows,
                        )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                remaining_table_rows = max(pending_count - table_processed, 0)
                summary.skipped_tables += 1
                summary.failed_rows += remaining_table_rows
                summary.processed_rows += remaining_table_rows
                table_failed += remaining_table_rows
                self.logger.exception(
                    "Table failed during row iteration: %s | processed=%s remaining_marked_failed=%s | reason=%s",
                    table.display_name,
                    table_processed,
                    remaining_table_rows,
                    exc,
                )
                self._emit_progress(
                    summary,
                    phase="table-failed",
                    current_table=table.display_name,
                    current_table_index=table_index,
                    total_tables=len(planned_tables),
                    table_pending_rows=pending_count,
                    table_processed_rows=pending_count,
                )
                continue

            self.logger.info(
                "Table finished: %s | processed=%s failed=%s",
                table.display_name,
                table_processed,
                table_failed,
            )
            self._emit_progress(
                summary,
                phase="table-finished",
                current_table=table.display_name,
                current_table_index=table_index,
                total_tables=len(planned_tables),
                table_pending_rows=pending_count,
                table_processed_rows=table_processed,
            )

    def _translate_row(
        self,
        plan: TableTranslationPlan,
        source_row: dict[str, object],
        config: RuntimeConfig,
        *,
        column_names: tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        translated_values: dict[str, object] = {}

        for column_name in column_names or plan.text_column_names:
            original_value = source_row.get(column_name)
            if original_value is None:
                translated_values[column_name] = None
                continue

            original_text = str(original_value)
            if not original_text.strip():
                translated_values[column_name] = original_value
                continue

            text_format = detect_text_format(column_name, original_text)
            cache_key = (
                config.source_language.code,
                config.target_language.code,
                text_format,
                original_text,
            )
            if cache_key not in self._translation_cache:
                self._translation_cache[cache_key] = run_with_retry(
                    lambda text=original_text: self.translator.translate(
                        text,
                        config.source_language.code,
                        config.target_language.code,
                        text_format=text_format,
                    ),
                    operation_name=f"translate column {column_name} ({text_format})",
                    attempts=config.retry.attempts,
                    initial_delay_seconds=config.retry.initial_delay_seconds,
                    backoff_factor=config.retry.backoff_factor,
                    logger=self.logger,
                )
            translated_values[column_name] = self._translation_cache[cache_key]

        return translated_values

    def _missing_target_text_columns(
        self,
        plan: TableTranslationPlan,
        source_row: dict[str, object],
    ) -> tuple[str, ...]:
        missing_columns: list[str] = []
        for column_name in plan.text_column_names:
            source_value = source_row.get(column_name)
            target_value = source_row.get(target_value_column_name(column_name))
            if has_text_value(source_value) and not has_text_value(target_value):
                missing_columns.append(column_name)
        return tuple(missing_columns)

    def _entity_key_values(
        self,
        plan: TableTranslationPlan,
        source_row: dict[str, object],
    ) -> dict[str, object]:
        return {
            column_name: source_row.get(column_name)
            for column_name in plan.table.key_column_names
        }

    def _is_cancelled(self) -> bool:
        return bool(self.cancel_callback and self.cancel_callback())

    def _should_stop(self) -> bool:
        return wait_while_paused(
            pause_callback=self.pause_callback,
            cancel_callback=self.cancel_callback,
            logger=self.logger,
            operation_name="Database translation",
        ) or self._is_cancelled()

    def _emit_progress(
        self,
        summary: TranslationSummary,
        *,
        phase: str,
        current_table: str | None = None,
        current_table_index: int = 0,
        total_tables: int = 0,
        table_pending_rows: int = 0,
        table_processed_rows: int = 0,
    ) -> None:
        if not self.progress_callback:
            return

        remaining_rows = max(summary.pending_rows - summary.processed_rows, 0)
        table_remaining_rows = max(table_pending_rows - table_processed_rows, 0)
        snapshot = ProgressSnapshot(
            phase=phase,
            discovered_tables=summary.discovered_tables,
            eligible_tables=summary.eligible_tables,
            skipped_tables=summary.skipped_tables,
            pending_rows=summary.pending_rows,
            processed_rows=summary.processed_rows,
            remaining_rows=remaining_rows,
            inserted_rows=summary.inserted_rows,
            updated_rows=summary.updated_rows,
            skipped_existing_rows=summary.skipped_existing_rows,
            failed_rows=summary.failed_rows,
            percent=calculate_percent(summary.processed_rows, summary.pending_rows),
            current_table=current_table,
            current_table_index=current_table_index,
            total_tables=total_tables,
            table_pending_rows=table_pending_rows,
            table_processed_rows=table_processed_rows,
            table_remaining_rows=table_remaining_rows,
            table_percent=calculate_percent(table_processed_rows, table_pending_rows),
        )

        try:
            self.progress_callback(snapshot)
        except Exception:
            self.logger.debug("Progress callback failed", exc_info=True)


def format_progress(done: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[------------------------] 0.00%"
    percent = min(max(done / total, 0), 1)
    filled = int(round(width * percent))
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {percent * 100:6.2f}%"


def calculate_percent(done: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return min(max(done / total, 0.0), 1.0) * 100


def detect_text_format(column_name: str, text: str) -> str:
    if column_name.casefold() in HTML_COLUMN_NAMES:
        return "html"
    if HTML_PATTERN.search(text):
        return "html"
    return "text"


def has_text_value(value: object) -> bool:
    return value is not None and bool(str(value).strip())


def format_entity_key_values(values: dict[str, object]) -> str:
    return ", ".join(
        f"{column_name}={value!r}"
        for column_name, value in values.items()
    )
