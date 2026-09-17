from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from translator_app.config import RetrySettings
from translator_app.languages import LanguageOption
from translator_app.models import LocalizeTable
from translator_app.pause_control import CancelCallback, PauseCallback, wait_while_paused
from translator_app.retry import run_with_retry
from translator_app.service import single_line, unfinished_status
from translator_app.sqlserver.repository import SqlServerLocalizationRepository
from translator_app.sqlserver.schema_reader import SqlServerSchemaReader
from translator_app.unfinished_report import format_unfinished_report


@dataclass(frozen=True)
class CleanupConfig:
    connection_string: str
    languages: tuple[LanguageOption, ...]
    dry_run: bool
    schema_name: str | None
    table_name: str | None
    batch_size: int
    log_dir: Path
    retry: RetrySettings
    excluded_table_names: tuple[str, ...] = ()


@dataclass
class CleanupSummary:
    discovered_tables: int = 0
    eligible_tables: int = 0
    skipped_tables: int = 0
    matched_rows: int = 0
    processed_rows: int = 0
    deleted_rows: int = 0
    failed_rows: int = 0
    unfinished_records: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CleanupProgressSnapshot:
    phase: str
    discovered_tables: int
    eligible_tables: int
    skipped_tables: int
    matched_rows: int
    processed_rows: int
    remaining_rows: int
    deleted_rows: int
    failed_rows: int
    percent: float
    current_table: str | None = None
    current_table_index: int = 0
    total_tables: int = 0
    table_matched_rows: int = 0
    table_processed_rows: int = 0
    table_percent: float = 0.0
    current_target_language: str | None = None
    target_language_index: int = 0
    total_target_languages: int = 0
    completed_target_languages: int = 0
    remaining_target_languages: int = 0
    language_percent: float = 0.0
    completed_target_language_codes: tuple[str, ...] = ()
    remaining_target_language_codes: tuple[str, ...] = ()


ProgressCallback = Callable[[CleanupProgressSnapshot], None]


class DatabaseCleanupService:
    """Remove localization rows that contain no textual content."""

    def __init__(
        self,
        *,
        schema_reader: SqlServerSchemaReader,
        repository: SqlServerLocalizationRepository,
        logger: logging.Logger,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
        pause_callback: PauseCallback | None = None,
    ) -> None:
        self.schema_reader = schema_reader
        self.repository = repository
        self.logger = logger
        self.progress_callback = progress_callback
        self.cancel_callback = cancel_callback
        self.pause_callback = pause_callback
        self._language_codes: tuple[str, ...] = ()
        self._language_index = 0
        self._completed_languages = 0
        self._language_total = 0
        self._language_processed = 0
        self._current_language: str | None = None
        self._current_table: str | None = None
        self._current_table_remaining = 0

    def run(self, config: CleanupConfig) -> CleanupSummary:
        summary = CleanupSummary()
        try:
            return self._run(config, summary)
        except KeyboardInterrupt:
            self._record_unfinished(
                summary,
                "operation | status=stopped | reason=keyboard interrupt",
            )
            raise
        except Exception as exc:
            if self._current_table:
                count_field = (
                    f" | rows={self._current_table_remaining}"
                    if self._current_table_remaining
                    else ""
                )
                self._record_unfinished(
                    summary,
                    f"table={self._current_table}{count_field} | "
                    f"status={unfinished_status(exc)} | reason={exc}",
                )
            else:
                self._record_unfinished(
                    summary,
                    f"operation | status={unfinished_status(exc)} | reason={exc}",
                )
            raise
        finally:
            self._log_unfinished_records(summary)

    def _run(
        self,
        config: CleanupConfig,
        summary: CleanupSummary,
    ) -> CleanupSummary:
        self._language_codes = tuple(language.code for language in config.languages)
        tables = self.schema_reader.get_localize_tables(
            schema_name=config.schema_name,
            table_name=config.table_name,
        )
        summary.discovered_tables = len(tables)
        self._emit(summary, phase="discovered")

        excluded = {name.casefold() for name in config.excluded_table_names}
        eligible_tables: list[LocalizeTable] = []
        for table in tables:
            if (
                table.display_name.casefold() in excluded
                or table.table_name.casefold() in excluded
            ):
                summary.skipped_tables += 1
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | status=excluded | "
                    "reason=excluded by user selection",
                    language=",".join(self._language_codes),
                )
                continue
            if not table.language_column_name:
                summary.skipped_tables += 1
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | status=skipped | "
                    "reason=LanguageId/LangId column was not found",
                    language=",".join(self._language_codes),
                )
                self.logger.warning(
                    "Cleanup skipped %s: LanguageId/LangId column was not found.",
                    table.display_name,
                )
                continue
            if not table.cleanup_text_columns():
                summary.skipped_tables += 1
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | status=skipped | "
                    "reason=no textual content column was found",
                    language=",".join(self._language_codes),
                )
                self.logger.warning(
                    "Cleanup skipped %s: no textual content column was found.",
                    table.display_name,
                )
                continue
            eligible_tables.append(table)

        summary.eligible_tables = len(eligible_tables)
        counts: dict[tuple[int, int], int] = {}
        for language in config.languages:
            for table in eligible_tables:
                self._current_language = language.code
                self._current_table = table.display_name
                self._current_table_remaining = 0
                if self._should_stop():
                    self._record_unfinished(
                        summary,
                        f"table={table.display_name} | status=stopped | "
                        "reason=cleanup stopped before counting rows",
                    )
                    self._emit(summary, phase="stopped")
                    return summary
                count = self._retry(
                    lambda table=table, language=language: self.repository.count_empty_localized_rows(
                        table,
                        language_id=language.id,
                    ),
                    config,
                    f"count empty rows in {table.display_name} ({language.code})",
                )
                count = max(int(count), 0)
                counts[(language.id, table.object_id)] = count
                summary.matched_rows += count

        self._emit(summary, phase="prepared")
        if not eligible_tables or not config.languages:
            self._emit(summary, phase="finished")
            return summary

        for language_index, language in enumerate(config.languages, start=1):
            self._language_index = language_index
            self._current_language = language.code
            self._language_total = sum(
                counts[(language.id, table.object_id)] for table in eligible_tables
            )
            self._language_processed = 0
            self.logger.info(
                "Cleanup language %s/%s: %s (%s) | matched=%s | mode=%s",
                language_index,
                len(config.languages),
                language.title,
                language.code,
                self._language_total,
                "dry-run" if config.dry_run else "execute",
            )

            for table_index, table in enumerate(eligible_tables, start=1):
                matched = counts[(language.id, table.object_id)]
                table_processed = 0
                self._current_table = table.display_name
                self._current_table_remaining = matched
                self._emit(
                    summary,
                    phase="table-started",
                    table=table,
                    table_index=table_index,
                    total_tables=len(eligible_tables),
                    table_matched=matched,
                    table_processed=0,
                )
                if config.dry_run:
                    table_processed = matched
                    summary.processed_rows += matched
                    self._language_processed += matched
                else:
                    while table_processed < matched:
                        if self._should_stop():
                            self._record_cleanup_remainder(
                                summary,
                                config=config,
                                tables=eligible_tables,
                                counts=counts,
                                language_index=language_index - 1,
                                table_index=table_index - 1,
                                current_table_processed=table_processed,
                            )
                            self._emit(
                                summary,
                                phase="stopped",
                                table=table,
                                table_index=table_index,
                                total_tables=len(eligible_tables),
                                table_matched=matched,
                                table_processed=table_processed,
                            )
                            return summary
                        deleted = int(
                            self._retry(
                                lambda table=table,
                                language=language,
                                remaining=matched - table_processed: (
                                    self.repository.delete_empty_localized_rows(
                                        table,
                                        language_id=language.id,
                                        batch_size=min(config.batch_size, remaining),
                                    )
                                ),
                                config,
                                f"delete empty rows from {table.display_name} ({language.code})",
                            )
                        )
                        if deleted <= 0:
                            # Rows may have changed after the preview count. Do
                            # not loop forever when nothing still matches.
                            remainder = matched - table_processed
                            table_processed += remainder
                            summary.processed_rows += remainder
                            self._language_processed += remainder
                            break
                        table_processed += deleted
                        self._current_table_remaining = max(
                            matched - table_processed,
                            0,
                        )
                        summary.processed_rows += deleted
                        summary.deleted_rows += deleted
                        self._language_processed += deleted
                        self._emit(
                            summary,
                            phase="running",
                            table=table,
                            table_index=table_index,
                            total_tables=len(eligible_tables),
                            table_matched=matched,
                            table_processed=table_processed,
                        )

                self.logger.info(
                    "Cleanup table finished: %s | language=%s | matched=%s | deleted=%s",
                    table.display_name,
                    language.code,
                    matched,
                    0 if config.dry_run else table_processed,
                )
                self._emit(
                    summary,
                    phase="table-finished",
                    table=table,
                    table_index=table_index,
                    total_tables=len(eligible_tables),
                    table_matched=matched,
                    table_processed=table_processed,
                )
                self._current_table_remaining = 0

            self._completed_languages = language_index
            self._emit(summary, phase="language-finished")

        self._emit(summary, phase="finished")
        self._current_table = None
        return summary

    def _record_cleanup_remainder(
        self,
        summary: CleanupSummary,
        *,
        config: CleanupConfig,
        tables: list[LocalizeTable],
        counts: dict[tuple[int, int], int],
        language_index: int,
        table_index: int,
        current_table_processed: int,
    ) -> None:
        for queued_language_index in range(language_index, len(config.languages)):
            language = config.languages[queued_language_index]
            first_table_index = table_index if queued_language_index == language_index else 0
            for queued_table_index in range(first_table_index, len(tables)):
                table = tables[queued_table_index]
                remaining = counts[(language.id, table.object_id)]
                if (
                    queued_language_index == language_index
                    and queued_table_index == table_index
                ):
                    remaining = max(remaining - current_table_processed, 0)
                if not remaining:
                    continue
                self._record_unfinished(
                    summary,
                    f"table={table.display_name} | rows={remaining} | "
                    "status=stopped | reason=cleanup stopped before deletion",
                    language=language.code,
                )

    def _record_unfinished(
        self,
        summary: CleanupSummary,
        record: str,
        *,
        language: str | None = None,
    ) -> None:
        normalized = single_line(record)
        record_language = language or self._current_language
        if record_language and "language=" not in normalized.casefold():
            normalized += f" | language={record_language}"
        summary.unfinished_records.append(normalized)

    def _log_unfinished_records(self, summary: CleanupSummary) -> None:
        self.logger.info(
            "\n%s",
            format_unfinished_report(
                summary.unfinished_records,
                operation_name="Database cleanup",
            ),
        )

    def _retry(
        self,
        operation: Callable[[], object],
        config: CleanupConfig,
        operation_name: str,
    ) -> object:
        return run_with_retry(
            operation,
            operation_name=operation_name,
            attempts=config.retry.attempts,
            initial_delay_seconds=config.retry.initial_delay_seconds,
            backoff_factor=config.retry.backoff_factor,
            logger=self.logger,
        )

    def _should_stop(self) -> bool:
        return wait_while_paused(
            pause_callback=self.pause_callback,
            cancel_callback=self.cancel_callback,
            logger=self.logger,
            operation_name="Database cleanup",
        ) or bool(self.cancel_callback and self.cancel_callback())

    def _emit(
        self,
        summary: CleanupSummary,
        *,
        phase: str,
        table: LocalizeTable | None = None,
        table_index: int = 0,
        total_tables: int = 0,
        table_matched: int = 0,
        table_processed: int = 0,
    ) -> None:
        if self.progress_callback is None:
            return
        snapshot = CleanupProgressSnapshot(
            phase=phase,
            discovered_tables=summary.discovered_tables,
            eligible_tables=summary.eligible_tables,
            skipped_tables=summary.skipped_tables,
            matched_rows=summary.matched_rows,
            processed_rows=summary.processed_rows,
            remaining_rows=max(summary.matched_rows - summary.processed_rows, 0),
            deleted_rows=summary.deleted_rows,
            failed_rows=summary.failed_rows,
            percent=_percent(summary.processed_rows, summary.matched_rows),
            current_table=table.display_name if table else None,
            current_table_index=table_index,
            total_tables=total_tables,
            table_matched_rows=table_matched,
            table_processed_rows=table_processed,
            table_percent=_percent(table_processed, table_matched),
            current_target_language=self._current_language,
            target_language_index=self._language_index,
            total_target_languages=len(self._language_codes),
            completed_target_languages=self._completed_languages,
            remaining_target_languages=max(
                len(self._language_codes) - self._language_index, 0
            ),
            language_percent=_percent(
                self._language_processed,
                self._language_total,
            ),
            completed_target_language_codes=self._language_codes[
                : self._completed_languages
            ],
            remaining_target_language_codes=self._language_codes[
                self._language_index :
            ],
        )
        try:
            self.progress_callback(snapshot)
        except Exception:
            self.logger.debug("Cleanup progress callback failed", exc_info=True)


def _percent(done: int, total: int) -> float:
    if total <= 0:
        return 100.0 if done else 0.0
    return min(max(done / total * 100.0, 0.0), 100.0)
