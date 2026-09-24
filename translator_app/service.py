from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from translator_app.config import RuntimeConfig
from translator_app.html_content import (
    apply_html_direction,
    validate_or_repair_html_translation,
)
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
from translator_app.unfinished_report import format_unfinished_report


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
    unfinished_records: list[str] = field(default_factory=list)


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
    current_target_language: str | None = None
    target_language_index: int = 0
    total_target_languages: int = 1
    completed_target_languages: int = 0
    remaining_target_languages: int = 1
    language_percent: float = 0.0
    completed_target_language_codes: tuple[str, ...] = ()
    remaining_target_language_codes: tuple[str, ...] = ()


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
        self._translation_cache: dict[tuple[str, str, str, str], str] = {}
        self.progress_callback = progress_callback
        self.cancel_callback = cancel_callback
        self.pause_callback = pause_callback
        self._target_language_index = 1
        self._total_target_languages = 1
        self._completed_target_languages = 0
        self._current_target_language: str | None = None
        self._language_start_processed = 0
        self._language_start_pending = 0
        self._target_language_queue_codes: tuple[str, ...] = ()
        # Rows that failed during the retry pass are deliberately skipped by
        # the normal iterator in this run. They remain in the SQL log and are
        # retried on the next invocation.
        self._retry_skipped_keys: set[tuple[object, ...]] = set()

    def run(self, config: RuntimeConfig) -> TranslationSummary:
        summary = TranslationSummary()
        try:
            if not config.dry_run:
                ensure_failure_log = getattr(
                    self.repository,
                    "ensure_translation_failure_log_table",
                    None,
                )
                if ensure_failure_log is not None:
                    ensure_failure_log()
                    self.logger.info(
                        "Translation failure log table is ready: dbo.TranslatorTranslationFailureLog"
                    )
            target_languages = config.selected_target_languages()
            self._total_target_languages = len(target_languages)
            self._target_language_queue_codes = tuple(
                language.code for language in target_languages
            )
            self._completed_target_languages = 0
            for index, target_language in enumerate(target_languages, start=1):
                self._target_language_index = index
                self._current_target_language = target_language.code
                self._retry_skipped_keys.clear()
                self._language_start_processed = summary.processed_rows
                self._language_start_pending = summary.pending_rows
                target_config = replace(
                    config,
                    target_language=target_language,
                    target_languages=(target_language,),
                )
                self.logger.info(
                    "Translation queue item %s/%s: %s (%s)",
                    index,
                    len(target_languages),
                    target_language.title,
                    target_language.code,
                )
                self._run(target_config, summary)
                if self._is_cancelled():
                    self._emit_progress(
                        summary,
                        phase="stopped",
                        current_target_language=target_language.code,
                    )
                    break
                self._completed_target_languages = index
                self._emit_progress(
                    summary,
                    phase="queue-finished" if index == len(target_languages) else "language-finished",
                    current_target_language=target_language.code,
                    total_tables=0,
                )
            return summary
        except KeyboardInterrupt:
            self._record_unfinished(
                summary,
                "operation | status=stopped | reason=keyboard interrupt",
            )
            raise
        except Exception as exc:
            self._record_unfinished(
                summary,
                f"operation status={unfinished_status(exc)} | reason={exc}",
            )
            raise
        finally:
            self._log_unfinished_records(summary)

    def _run(
        self,
        config: RuntimeConfig,
        summary: TranslationSummary,
    ) -> TranslationSummary:
        self.logger.info(
            "Starting translation: source=%s target=%s | mode=%s",
            config.source_language.code,
            config.target_language.code,
            "dry-run" if config.dry_run else "execute",
        )

        if self._should_stop():
            self.logger.warning("Operation stopped before table discovery.")
            self._record_unfinished(
                summary,
                "operation | status=stopped | reason=operation stopped before table discovery",
            )
            return summary

        self._retry_logged_failures(config, summary)
        if self._should_stop():
            self.logger.warning("Operation stopped after the persisted failure retry pass.")
            return summary

        tables = self.schema_reader.get_localize_tables(
            schema_name=config.schema_name,
            table_name=config.table_name,
        )
        summary.discovered_tables += len(tables)
        self._emit_progress(summary, phase="discovered", total_tables=len(tables))

        if not tables:
            self.logger.warning("No translation tables were found.")
            return summary

        if config.excluded_table_names:
            excluded = {name.casefold() for name in config.excluded_table_names}
            excluded_tables = [
                table.display_name
                for table in tables
                if (
                    table.display_name.casefold() in excluded
                    or table.table_name.casefold() in excluded
                )
            ]
            summary.skipped_tables += len(excluded_tables)
            for excluded_table in excluded_tables:
                self._record_unfinished(
                    summary,
                    f"table={excluded_table} | status=excluded | reason=excluded by user selection",
                )
            tables = [
                table
                for table in tables
                if (
                    table.display_name.casefold() not in excluded
                    and table.table_name.casefold() not in excluded
                )
            ]
            self.logger.info(
                "Excluded tables: %s",
                ", ".join(excluded_tables) if excluded_tables else "none matched",
            )
            if not tables:
                self.logger.warning("All discovered tables were excluded.")
                self._emit_progress(summary, phase="finished", total_tables=0)
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
    ) -> list[tuple[TableTranslationPlan, int, int]]:
        planned_tables: list[tuple[TableTranslationPlan, int, int]] = []

        for table_index, table in enumerate(tables):
            if self._should_stop():
                self.logger.warning("Operation stopped during preparation.")
                for remaining_table in tables[table_index:]:
                    self._record_unfinished(
                        summary,
                        f"table={remaining_table.display_name} | status=stopped | reason=operation stopped before preparation",
                    )
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
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | status={unfinished_status(exc)} | reason={exc}",
                )
                self.logger.exception(
                    "Table skipped: %s | reason=%s",
                    table.display_name,
                    exc,
                )
                continue

            summary.eligible_tables += 1
            summary.pending_rows += pending_count
            pending_characters = (
                self._count_pending_text_characters(
                    plan,
                    config,
                    fallback=pending_count,
                )
                if pending_count
                else 0
            )
            planned_tables.append((plan, pending_count, pending_characters))
            self._emit_progress(
                summary,
                phase="prepare",
                current_table=table.display_name,
                current_table_index=len(planned_tables),
                total_tables=len(tables),
            )

            self.logger.info(
                "Table ready: %s | base=%s | fk=%s | translatable_columns=%s | pending=%s | pending_characters=%s",
                table.display_name,
                table.referenced_table_name or "-",
                ",".join(table.key_column_names),
                ", ".join(plan.text_column_names),
                pending_count,
                pending_characters,
            )

        # Shorter translation payloads are handled first.  The table name and
        # row count make ties deterministic and keep progress reproducible.
        planned_tables.sort(
            key=lambda item: (
                item[2],
                item[1],
                item[0].table.display_name.casefold(),
            )
        )
        if planned_tables:
            self.logger.info(
                "Translation table order (smallest text first): %s",
                ", ".join(
                    f"{plan.table.display_name} ({characters} chars)"
                    for plan, _pending, characters in planned_tables
                ),
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
        planned_tables: list[tuple[TableTranslationPlan, int, int]],
        config: RuntimeConfig,
        summary: TranslationSummary,
    ) -> None:
        for table_index, (plan, pending_count, _pending_characters) in enumerate(
            planned_tables,
            start=1,
        ):
            if self._should_stop():
                self.logger.warning("Operation stopped before the next table.")
                for remaining_plan, remaining_count, _characters in planned_tables[
                    table_index - 1 :
                ]:
                    self._record_unfinished(
                        summary,
                        f"table={remaining_plan.table.display_name} | rows={remaining_count} | status=stopped | reason=operation stopped before table",
                    )
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
                    entity_key_values = self._entity_key_values(plan, source_row)
                    entity_value = format_entity_key_values(entity_key_values)
                    if self._should_stop():
                        self.logger.warning("Operation stopped by user request.")
                        self._record_unfinished(
                            summary,
                            f"table={table.display_name} | row={entity_value} | status=stopped | reason=operation stopped before row",
                        )
                        remaining_rows = max(pending_count - table_processed - 1, 0)
                        if remaining_rows:
                            self._record_unfinished(
                                summary,
                                f"table={table.display_name} | rows={remaining_rows} | status=stopped | reason=operation stopped before row",
                            )
                        for remaining_plan, remaining_count, _characters in planned_tables[
                            table_index :
                        ]:
                            self._record_unfinished(
                                summary,
                                f"table={remaining_plan.table.display_name} | rows={remaining_count} | status=stopped | reason=operation stopped before table",
                            )
                        return

                    row_status = "failed"
                    target_exists = False
                    missing_columns: tuple[str, ...] = ()
                    try:
                        if self._failure_identity(
                            table,
                            config,
                            entity_key_values,
                        ) in self._retry_skipped_keys:
                            summary.failed_rows += 1
                            table_failed += 1
                            row_status = "skipped-retry-failure"
                            self._record_unfinished(
                                summary,
                                f"table={table.display_name} | row={entity_value} | "
                                "status=skipped | reason=persisted failure retry failed",
                            )
                        else:
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
                        self._record_unfinished(
                            summary,
                            f"table={table.display_name} | row={entity_value} | status={unfinished_status(exc)} | reason={exc}",
                        )
                        self._persist_translation_failure(
                            config=config,
                            plan=plan,
                            source_row=source_row,
                            entity_key_values=entity_key_values,
                            target_exists=target_exists,
                            missing_columns=missing_columns,
                            failure_reason=str(exc),
                        )
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
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | rows={remaining_table_rows} | status={unfinished_status(exc)} | reason={exc}",
                )
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

            unreported_rows = max(pending_count - table_processed, 0)
            if unreported_rows:
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | rows={unreported_rows} | status=not processed | reason=source row iterator ended early",
                )
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

    def _count_pending_text_characters(
        self,
        plan: TableTranslationPlan,
        config: RuntimeConfig,
        *,
        fallback: int,
    ) -> int:
        counter = getattr(self.repository, "count_pending_text_characters", None)
        if not callable(counter):
            # Lightweight repository fakes and older integrations may not
            # expose the optional size query. Preserve their behavior while
            # still using the exact character count in the SQL repository.
            return max(fallback, 0)

        try:
            value = run_with_retry(
                lambda: counter(
                    plan,
                    source_language_id=config.source_language.id,
                    target_language_id=config.target_language.id,
                ),
                operation_name=f"measure text in {plan.table.display_name}",
                attempts=config.retry.attempts,
                initial_delay_seconds=config.retry.initial_delay_seconds,
                backoff_factor=config.retry.backoff_factor,
                logger=self.logger,
            )
            return max(int(value), 0)
        except Exception as exc:
            # Ordering is an optimization; an unavailable size estimate must
            # not make an otherwise translatable table fail preparation.
            self.logger.warning(
                "Could not measure text size for %s; sorting by pending rows: %s",
                plan.table.display_name,
                exc,
            )
            return max(fallback, 0)

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
                self._translation_cache[cache_key] = self._translate_value(
                    original_text,
                    config,
                    text_format=text_format,
                    operation_name=f"translate column {column_name} ({text_format})",
                )

            if text_format == "html":
                row_identifier = format_entity_key_values(
                    self._entity_key_values(plan, source_row)
                )
                html_result = validate_or_repair_html_translation(
                    original_text,
                    self._translation_cache[cache_key],
                    translate_text=lambda text: self._translate_value(
                        text,
                        config,
                        text_format="text",
                        operation_name=(
                            f"repair HTML text in {plan.table.display_name}."
                            f"{column_name}"
                        ),
                    ),
                )
                self._translation_cache[cache_key] = html_result.value
                if html_result.repaired:
                    self.logger.warning(
                        "HTML response repaired | table=%s | column=%s | row=%s | reason=%s",
                        plan.table.display_name,
                        column_name,
                        row_identifier,
                        html_result.reason or "HTML normalized",
                    )
                else:
                    self.logger.info(
                        "HTML response validated | table=%s | column=%s | row=%s | structure=preserved",
                        plan.table.display_name,
                        column_name,
                        row_identifier,
                    )
                directionally_corrected = apply_html_direction(
                    html_result.value,
                    config.target_language,
                )
                if directionally_corrected != html_result.value:
                    self.logger.info(
                        "HTML direction normalized | table=%s | column=%s | row=%s | direction=%s | text-align=%s",
                        plan.table.display_name,
                        column_name,
                        row_identifier,
                        "rtl" if config.target_language.right_to_left else "ltr",
                        "right" if config.target_language.right_to_left else "left",
                    )
                self._translation_cache[cache_key] = directionally_corrected
            translated_values[column_name] = self._translation_cache[cache_key]

        return translated_values

    def _retry_logged_failures(
        self,
        config: RuntimeConfig,
        summary: TranslationSummary,
    ) -> None:
        """Retry persisted row failures before discovering new pending rows."""

        if config.dry_run:
            return
        list_failures = getattr(self.repository, "list_translation_failures", None)
        if not callable(list_failures):
            return
        try:
            failures = list_failures(
                source_language_id=config.source_language.id,
                target_language_id=config.target_language.id,
                schema_name=config.schema_name,
                table_name=config.table_name,
            )
        except Exception as exc:
            self.logger.warning(
                "Could not read persisted translation failures; continuing normally: %s",
                exc,
            )
            return
        if not failures:
            return

        try:
            tables = self.schema_reader.get_localize_tables(
                schema_name=config.schema_name,
                table_name=config.table_name,
            )
        except Exception as exc:
            self.logger.warning(
                "Could not discover tables for persisted failure retry: %s",
                exc,
            )
            return
        table_map = {
            (table.schema_name.casefold(), table.table_name.casefold()): table
            for table in tables
        }
        excluded_tables = {name.casefold() for name in config.excluded_table_names}
        self.logger.info(
            "Persisted translation failure retry started: count=%s target=%s",
            len(failures),
            config.target_language.code,
        )

        for failure in failures:
            if self._should_stop():
                self._record_unfinished(
                    summary,
                    "operation | status=stopped | reason=stopped during persisted failure retry",
                )
                return
            table = table_map.get(
                (failure.schema_name.casefold(), failure.table_name.casefold())
            )
            if table is None:
                summary.failed_rows += 1
                self._record_unfinished(
                    summary,
                    f"table={failure.schema_name}.{failure.table_name} | "
                    "status=skipped | reason=table no longer exists for persisted failure",
                )
                continue
            if (
                table.display_name.casefold() in excluded_tables
                or table.table_name.casefold() in excluded_tables
            ):
                summary.failed_rows += 1
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | status=excluded | "
                    "reason=excluded by user selection during persisted failure retry",
                )
                continue
            plan: TableTranslationPlan | None = None
            target_exists = failure.target_exists
            missing_columns = failure.missing_columns
            try:
                plan = build_table_translation_plan(table)
                source_row = dict(failure.source_row)
                target_refreshed = False
                get_target_state = getattr(self.repository, "get_target_state", None)
                if callable(get_target_state):
                    current_target = get_target_state(
                        plan,
                        entity_key_values=failure.entity_key_values,
                        target_language_id=config.target_language.id,
                    )
                    if current_target:
                        source_row.update(current_target)
                        target_refreshed = True
                target_exists = bool(
                    source_row.get(TARGET_EXISTS_COLUMN_NAME, target_exists)
                )
                if target_exists and (target_refreshed or not missing_columns):
                    missing_columns = self._missing_target_text_columns(plan, source_row)
                if target_exists:
                    if not missing_columns:
                        summary.skipped_existing_rows += 1
                    else:
                        translated_values = self._translate_row(
                            plan,
                            source_row,
                            config,
                            column_names=missing_columns,
                        )
                        updated_columns = run_with_retry(
                            lambda: self.repository.update_translation_columns(
                                plan,
                                entity_key_values=failure.entity_key_values,
                                translated_values=translated_values,
                                target_language_id=config.target_language.id,
                            ),
                            operation_name=(
                                f"retry update {table.display_name} "
                                f"{format_entity_key_values(failure.entity_key_values)}"
                            ),
                            attempts=config.retry.attempts,
                            initial_delay_seconds=config.retry.initial_delay_seconds,
                            backoff_factor=config.retry.backoff_factor,
                            logger=self.logger,
                        )
                        if updated_columns:
                            summary.updated_rows += 1
                        else:
                            summary.skipped_existing_rows += 1
                else:
                    translated_values = self._translate_row(plan, source_row, config)
                    run_with_retry(
                        lambda: self.repository.insert_translation(
                            plan,
                            source_row=source_row,
                            translated_values=translated_values,
                            target_language_id=config.target_language.id,
                        ),
                        operation_name=(
                            f"retry insert {table.display_name} "
                            f"{format_entity_key_values(failure.entity_key_values)}"
                        ),
                        attempts=config.retry.attempts,
                        initial_delay_seconds=config.retry.initial_delay_seconds,
                        backoff_factor=config.retry.backoff_factor,
                        logger=self.logger,
                    )
                    summary.inserted_rows += 1
                delete_failure = getattr(self.repository, "delete_translation_failure", None)
                if callable(delete_failure):
                    delete_failure(failure.failure_id)
                summary.processed_rows += 1
                self.logger.info(
                    "Persisted translation failure resolved: table=%s row=%s attempt=%s",
                    table.display_name,
                    format_entity_key_values(failure.entity_key_values),
                    failure.attempt_count,
                )
            except Exception as exc:
                summary.failed_rows += 1
                self._retry_skipped_keys.add(
                    self._failure_identity(
                        table,
                        config,
                        failure.entity_key_values,
                    )
                )
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | "
                    f"row={format_entity_key_values(failure.entity_key_values)} | "
                    f"status=skipped | reason=persisted failure retry failed: {exc}",
                )
                self._persist_translation_failure(
                    config=config,
                    plan=plan,
                    source_row=failure.source_row,
                    entity_key_values=failure.entity_key_values,
                    target_exists=target_exists,
                    missing_columns=missing_columns,
                    failure_reason=str(exc),
                    table=table,
                )
                self.logger.warning(
                    "Persisted translation failure remains unresolved: table=%s row=%s reason=%s",
                    table.display_name,
                    format_entity_key_values(failure.entity_key_values),
                    exc,
                )

    def _persist_translation_failure(
        self,
        *,
        config: RuntimeConfig,
        plan: TableTranslationPlan | None,
        source_row: dict[str, object],
        entity_key_values: dict[str, object],
        target_exists: bool,
        missing_columns: tuple[str, ...],
        failure_reason: str,
        table: LocalizeTable | None = None,
    ) -> None:
        if config.dry_run:
            return
        record_failure = getattr(self.repository, "record_translation_failure", None)
        if not callable(record_failure):
            return
        failure_table = table or (plan.table if plan is not None else None)
        if failure_table is None:
            return
        try:
            record_failure(
                table=failure_table,
                source_language_id=config.source_language.id,
                target_language_id=config.target_language.id,
                entity_key_values=entity_key_values,
                source_row=source_row,
                target_exists=target_exists,
                missing_columns=missing_columns,
                failure_reason=failure_reason,
            )
        except Exception as exc:
            self.logger.warning(
                "Could not persist translation failure for %s: %s",
                failure_table.display_name,
                exc,
            )

    def _failure_identity(
        self,
        table: LocalizeTable,
        config: RuntimeConfig,
        entity_key_values: dict[str, object],
    ) -> tuple[object, ...]:
        return (
            table.schema_name.casefold(),
            table.table_name.casefold(),
            config.source_language.id,
            config.target_language.id,
            tuple(
                (key, repr(entity_key_values.get(key)))
                for key in table.key_column_names
            ),
        )

    def _record_unfinished(self, summary: TranslationSummary, record: str) -> None:
        normalized = single_line(record)
        if self._current_target_language and "language=" not in normalized.casefold():
            normalized += f" | language={self._current_target_language}"
        summary.unfinished_records.append(normalized)

    def _log_unfinished_records(self, summary: TranslationSummary) -> None:
        self.logger.info(
            "\n%s",
            format_unfinished_report(
                summary.unfinished_records,
                operation_name="Database translation",
            ),
        )

    def _translate_value(
        self,
        text: str,
        config: RuntimeConfig,
        *,
        text_format: str,
        operation_name: str,
    ) -> str:
        cache_key = (
            config.source_language.code,
            config.target_language.code,
            text_format,
            text,
        )
        if cache_key not in self._translation_cache:
            self._translation_cache[cache_key] = run_with_retry(
                lambda: self.translator.translate(
                    text,
                    config.source_language.code,
                    config.target_language.code,
                    text_format=text_format,
                ),
                operation_name=operation_name,
                attempts=config.retry.attempts,
                initial_delay_seconds=config.retry.initial_delay_seconds,
                backoff_factor=config.retry.backoff_factor,
                logger=self.logger,
            )
        return self._translation_cache[cache_key]

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
        current_target_language: str | None = None,
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
            current_target_language=current_target_language
            or getattr(self, "_current_target_language", None),
            target_language_index=self._target_language_index,
            total_target_languages=self._total_target_languages,
            completed_target_languages=self._completed_target_languages,
            remaining_target_languages=max(
                self._total_target_languages - self._target_language_index,
                0,
            ),
            language_percent=calculate_percent(
                max(summary.processed_rows - self._language_start_processed, 0),
                max(summary.pending_rows - self._language_start_pending, 0),
            ),
            completed_target_language_codes=self._target_language_queue_codes[
                : self._completed_target_languages
            ],
            remaining_target_language_codes=self._target_language_queue_codes[
                self._target_language_index :
            ],
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


def unfinished_status(error: BaseException) -> str:
    """Return a useful stable status for the end-of-run unfinished list."""

    if isinstance(error, TimeoutError):
        return "timed out"
    error_name = type(error).__name__.casefold()
    error_text = str(error).casefold()
    if any(
        marker in error_name or marker in error_text
        for marker in ("timeout", "timed out", "timed-out", "hyt00", "hyt01")
    ):
        return "timed out"
    return "failed"


def single_line(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()
