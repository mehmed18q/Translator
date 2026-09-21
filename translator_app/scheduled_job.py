"""Non-interactive translation + cleanup job for cron/systemd.

The desktop application and the interactive CLI intentionally remain
unchanged.  This module is a small orchestration layer that can be run with
``python -m translator_app.scheduled_job`` on a server.  It owns one process
lock for the complete translation/cleanup sequence, so two scheduled runs
cannot overlap.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Iterable

from translator_app.cleanup_service import (
    CleanupConfig,
    CleanupSummary,
    DatabaseCleanupService,
)
from translator_app.config import (
    RetrySettings,
    RuntimeConfig,
    SqlServerConnectionSettings,
)
from translator_app.languages import LanguageOption, get_language
from translator_app.logging_config import configure_logging
from translator_app.runtime_paths import resolve_application_path
from translator_app.service import DatabaseTranslationService, TranslationSummary
from translator_app.sqlserver import (
    SqlServerLocalizationRepository,
    SqlServerSchemaReader,
    connect,
)
from translator_app.translators import create_translator


@dataclass(frozen=True)
class ScheduledJobConfig:
    """All settings required by one non-interactive job run."""

    connection: SqlServerConnectionSettings
    source_language: LanguageOption
    target_languages: tuple[LanguageOption, ...]
    cleanup_languages: tuple[LanguageOption, ...]
    schema_name: str | None
    table_name: str | None
    dry_run: bool
    batch_size: int
    progress_every: int
    translator_provider: str
    request_timeout_seconds: float
    request_delay_seconds: float
    libretranslate_url: str | None
    libretranslate_api_key: str | None
    log_dir: Path
    retry: RetrySettings
    lock_file: Path
    lock_timeout_seconds: float | None = None


@dataclass(frozen=True)
class ScheduledJobResult:
    translation: TranslationSummary
    cleanup: CleanupSummary


class JobAlreadyRunning(RuntimeError):
    """Raised when a finite lock timeout expires while another job runs."""


class JobFileLock:
    """A cross-process lock held for the complete job lifetime.

    ``fcntl.flock`` is used on Linux/Unix servers and ``msvcrt.locking`` is
    used on Windows.  The operating system releases either lock if a process
    is terminated, avoiding stale PID-file problems.
    """

    def __init__(self, path: Path, *, timeout_seconds: float | None = None) -> None:
        self.path = resolve_application_path(path)
        self.timeout_seconds = timeout_seconds
        self._handle: object | None = None
        self._windows_lock = False

    def __enter__(self) -> "JobFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        self._prepare_lock_byte()
        deadline = (
            None
            if self.timeout_seconds is None
            else time.monotonic() + max(self.timeout_seconds, 0)
        )

        while True:
            try:
                self._try_lock()
                self._write_owner_marker()
                return self
            except (BlockingIOError, OSError) as exc:
                if not self._is_lock_contention(exc):
                    self._close_handle()
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    self._close_handle()
                    raise JobAlreadyRunning(
                        f"Another scheduled job is still running ({self.path})."
                    ) from exc
                time.sleep(0.25)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        try:
            self._unlock()
        finally:
            self._close_handle()

    def _prepare_lock_byte(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        if self._handle.read(1) == "":
            self._handle.seek(0)
            self._handle.write(" ")
            self._handle.flush()

    def _try_lock(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            self._windows_lock = True
            return

        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        assert self._handle is not None
        if os.name == "nt":
            if not self._windows_lock:
                return
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            self._windows_lock = False
            return

        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)

    def _write_owner_marker(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n")
        self._handle.flush()

    def _close_handle(self) -> None:
        if self._handle is not None:
            self._handle.close()  # type: ignore[union-attr]
            self._handle = None

    @staticmethod
    def _is_lock_contention(exc: OSError) -> bool:
        if isinstance(exc, BlockingIOError):
            return True
        # EACCES/EAGAIN are the usual non-blocking lock errors on Unix and
        # Windows.  Keep the check narrow so permissions/disk failures are
        # reported immediately instead of being retried forever.
        return getattr(exc, "errno", None) in {11, 13, 35}


def run_scheduled_job(
    config: ScheduledJobConfig,
    *,
    logger: logging.Logger,
) -> ScheduledJobResult:
    """Run translation first and cleanup second under one process lock."""

    with JobFileLock(
        config.lock_file,
        timeout_seconds=config.lock_timeout_seconds,
    ):
        logger.info(
            "Scheduled job started: source=%s targets=%s table=%s mode=%s",
            config.source_language.code,
            ",".join(language.code for language in config.target_languages),
            config.table_name or "all",
            "dry-run" if config.dry_run else "execute",
        )
        translation_summary = _run_translation(config, logger)
        logger.info(
            "Translation phase finished: processed=%s inserted=%s updated=%s failed=%s",
            translation_summary.processed_rows,
            translation_summary.inserted_rows,
            translation_summary.updated_rows,
            translation_summary.failed_rows,
        )

        # Open fresh connections after translation.  This makes the phase
        # boundary explicit and guarantees cleanup sees committed writes.
        cleanup_summary = _run_cleanup(config, logger)
        logger.info(
            "Cleanup phase finished: matched=%s deleted=%s failed=%s",
            cleanup_summary.matched_rows,
            cleanup_summary.deleted_rows,
            cleanup_summary.failed_rows,
        )
        logger.info("Scheduled job finished.")
        return ScheduledJobResult(
            translation=translation_summary,
            cleanup=cleanup_summary,
        )


def _run_translation(
    config: ScheduledJobConfig,
    logger: logging.Logger,
) -> TranslationSummary:
    runtime_config = RuntimeConfig(
        connection_string=config.connection.build_connection_string(),
        source_language=config.source_language,
        target_language=config.target_languages[0],
        target_languages=config.target_languages,
        dry_run=config.dry_run,
        schema_name=config.schema_name,
        table_name=config.table_name,
        batch_size=config.batch_size,
        progress_every=config.progress_every,
        translator_provider=config.translator_provider,
        request_timeout_seconds=config.request_timeout_seconds,
        request_delay_seconds=config.request_delay_seconds,
        libretranslate_url=config.libretranslate_url,
        libretranslate_api_key=config.libretranslate_api_key,
        log_dir=config.log_dir,
        retry=config.retry,
    )
    read_connection = connect(runtime_config.connection_string, autocommit=False)
    write_connection: object = read_connection
    try:
        if not config.dry_run:
            write_connection = connect(runtime_config.connection_string, autocommit=True)
        service = DatabaseTranslationService(
            schema_reader=SqlServerSchemaReader(read_connection),
            repository=SqlServerLocalizationRepository(
                read_connection,
                write_connection,
            ),
            translator=create_translator(runtime_config, logger=logger),
            logger=logger,
        )
        return service.run(runtime_config)
    finally:
        if write_connection is not read_connection:
            write_connection.close()  # type: ignore[union-attr]
        read_connection.close()  # type: ignore[union-attr]


def _run_cleanup(
    config: ScheduledJobConfig,
    logger: logging.Logger,
) -> CleanupSummary:
    connection_string = config.connection.build_connection_string()
    read_connection = connect(connection_string, autocommit=False)
    write_connection: object = read_connection
    try:
        if not config.dry_run:
            write_connection = connect(connection_string, autocommit=True)
        service = DatabaseCleanupService(
            schema_reader=SqlServerSchemaReader(read_connection),
            repository=SqlServerLocalizationRepository(
                read_connection,
                write_connection,
            ),
            logger=logger,
        )
        return service.run(
            CleanupConfig(
                connection_string=connection_string,
                languages=config.cleanup_languages,
                dry_run=config.dry_run,
                schema_name=config.schema_name,
                table_name=config.table_name,
                batch_size=config.batch_size,
                log_dir=config.log_dir,
                retry=config.retry,
            )
        )
    finally:
        if write_connection is not read_connection:
            write_connection.close()  # type: ignore[union-attr]
        read_connection.close()  # type: ignore[union-attr]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the non-interactive translation then cleanup job.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(os.getenv("JOB_ENV_FILE", ".env")),
        help="Environment file loaded before parsing settings (default: .env).",
    )
    parser.add_argument("--connection-string", default=os.getenv("SQLSERVER_CONNECTION_STRING"))
    parser.add_argument(
        "--driver",
        default=os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server"),
    )
    parser.add_argument("--server", default=os.getenv("SQLSERVER_SERVER"))
    parser.add_argument("--database", default=os.getenv("SQLSERVER_DATABASE"))
    parser.add_argument("--username", default=os.getenv("SQLSERVER_USERNAME"))
    parser.add_argument("--password", default=os.getenv("SQLSERVER_PASSWORD"))
    parser.add_argument(
        "--trusted-connection",
        action="store_true",
        default=parse_env_bool("SQLSERVER_TRUSTED_CONNECTION", False),
    )
    parser.add_argument(
        "--no-encrypt",
        action="store_true",
        default=parse_env_bool("SQLSERVER_NO_ENCRYPT", False),
    )
    parser.add_argument(
        "--no-trust-server-certificate",
        action="store_true",
        default=not parse_env_bool("SQLSERVER_TRUST_SERVER_CERTIFICATE", True),
    )
    parser.add_argument("--source-language-id", type=int)
    parser.add_argument(
        "--target-language-ids",
        help="Comma-separated destination language IDs (for example: 2,3).",
    )
    parser.add_argument(
        "--cleanup-language-ids",
        help="Optional comma-separated cleanup language IDs; defaults to targets.",
    )
    parser.add_argument("--schema", dest="schema_name", default=os.getenv("JOB_SCHEMA"))
    parser.add_argument(
        "--table",
        dest="table_name",
        default=os.getenv("JOB_TABLE"),
        help="One table (TableName or SchemaName.TableName); omit to process all.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Do not write or delete rows.")
    mode.add_argument("--execute", action="store_true", help="Write translations and delete empty rows.")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("BATCH_SIZE", "100")))
    parser.add_argument(
        "--progress-every",
        type=int,
        default=int(os.getenv("PROGRESS_EVERY", "1")),
    )
    parser.add_argument("--provider", choices=("auto", "google-free", "libretranslate"), default=os.getenv("TRANSLATOR_PROVIDER", "auto"))
    parser.add_argument("--libretranslate-url", default=os.getenv("LIBRETRANSLATE_URL"))
    parser.add_argument("--libretranslate-api-key", default=os.getenv("LIBRETRANSLATE_API_KEY"))
    parser.add_argument("--request-timeout", type=float, default=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--request-delay", type=float, default=float(os.getenv("REQUEST_DELAY_SECONDS", "0.2")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("RETRY_ATTEMPTS", "3")))
    parser.add_argument("--retry-delay", type=float, default=float(os.getenv("RETRY_INITIAL_DELAY_SECONDS", "1")))
    parser.add_argument("--retry-backoff", type=float, default=float(os.getenv("RETRY_BACKOFF_FACTOR", "2")))
    parser.add_argument("--log-dir", type=Path, default=Path(os.getenv("LOG_DIR", "logs")))
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(os.getenv("JOB_LOCK_FILE", "var/translation_cleanup_job.lock")),
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=parse_optional_float(os.getenv("JOB_LOCK_TIMEOUT_SECONDS")),
        help="Seconds to wait for a previous run; omit for unlimited wait.",
    )
    return parser


def build_job_config(args: argparse.Namespace) -> ScheduledJobConfig:
    source_id = args.source_language_id
    if source_id is None:
        source_id = _required_env_int("SOURCE_LANGUAGE_ID")
    target_text = args.target_language_ids or os.getenv("TARGET_LANGUAGE_IDS")
    if not target_text:
        raise ValueError("Configure --target-language-ids or TARGET_LANGUAGE_IDS.")
    target_languages = tuple(get_language(item) for item in parse_id_list(target_text, "target"))
    if not target_languages:
        raise ValueError("At least one target language is required.")
    source_language = get_language(source_id)
    if source_language in target_languages:
        raise ValueError("The source language must not be one of the target languages.")

    cleanup_text = args.cleanup_language_ids or os.getenv("CLEANUP_LANGUAGE_IDS")
    cleanup_languages = (
        tuple(get_language(item) for item in parse_id_list(cleanup_text, "cleanup"))
        if cleanup_text
        else target_languages
    )
    if not cleanup_languages:
        raise ValueError("At least one cleanup language is required.")

    connection = SqlServerConnectionSettings(
        connection_string=args.connection_string,
        driver=args.driver,
        server=args.server or "",
        database=args.database or "",
        username=args.username,
        password=args.password,
        trusted_connection=args.trusted_connection,
        encrypt=not args.no_encrypt,
        trust_server_certificate=not args.no_trust_server_certificate,
    )
    dry_run = args.dry_run or (
        not args.execute and parse_env_bool("JOB_DRY_RUN", False)
    )
    schema_name, table_name = split_table_reference(args.table_name, args.schema_name)
    return ScheduledJobConfig(
        connection=connection,
        source_language=source_language,
        target_languages=target_languages,
        cleanup_languages=cleanup_languages,
        schema_name=schema_name,
        table_name=table_name,
        dry_run=dry_run,
        batch_size=max(args.batch_size, 1),
        progress_every=max(args.progress_every, 1),
        translator_provider=args.provider,
        request_timeout_seconds=max(args.request_timeout, 1),
        request_delay_seconds=max(args.request_delay, 0),
        libretranslate_url=args.libretranslate_url,
        libretranslate_api_key=args.libretranslate_api_key,
        log_dir=args.log_dir,
        retry=RetrySettings(
            attempts=max(args.retries, 1),
            initial_delay_seconds=max(args.retry_delay, 0),
            backoff_factor=max(args.retry_backoff, 1),
        ),
        lock_file=args.lock_file,
        lock_timeout_seconds=(
            None
            if args.lock_timeout is None
            else max(args.lock_timeout, 0)
        ),
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    env_file = _env_file_from_args(raw_argv)
    load_dotenv(resolve_application_path(env_file))
    logger: logging.Logger | None = None
    try:
        args = build_parser().parse_args(raw_argv)
        config = build_job_config(args)
        logger, log_file = configure_logging(config.log_dir)
        logger.info("Scheduled job log file: %s", log_file)
        result = run_scheduled_job(config, logger=logger)
        if result.translation.failed_rows or result.cleanup.failed_rows:
            logger.warning("Scheduled job completed with failed records.")
            return 1
        return 0
    except JobAlreadyRunning as exc:
        if logger:
            logger.warning("Scheduled job skipped: %s", exc)
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if logger:
            logger.warning("Scheduled job interrupted.")
        print("Scheduled job interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        if logger:
            logger.exception("Scheduled job failed: %s", exc)
        print(f"Scheduled job failed: {exc}", file=sys.stderr)
        return 1


def _env_file_from_args(argv: Iterable[str]) -> Path:
    values = list(argv)
    for index, value in enumerate(values):
        if value == "--env-file" and index + 1 < len(values):
            return Path(values[index + 1])
        if value.startswith("--env-file="):
            return Path(value.split("=", 1)[1])
    return Path(os.getenv("JOB_ENV_FILE", ".env"))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_id_list(value: str, name: str) -> tuple[int, ...]:
    items = [part.strip() for part in value.replace(";", ",").split(",")]
    if any(not item for item in items):
        raise ValueError(f"Invalid {name} language list: {value!r}")
    try:
        return tuple(dict.fromkeys(int(item) for item in items))
    except ValueError as exc:
        raise ValueError(f"Invalid {name} language list: {value!r}") from exc


def _required_env_int(name: str) -> int:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Configure --source-language-id or {name}.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def parse_optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Expected a number, got {value!r}.") from exc


def parse_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "y"}


def split_table_reference(
    table_name: str | None,
    schema_name: str | None,
) -> tuple[str | None, str | None]:
    schema_name = normalize_sql_name(schema_name)
    if not table_name:
        return schema_name, None
    parts = [normalize_sql_name(part) for part in table_name.split(".")]
    parts = [part for part in parts if part]
    if len(parts) == 1:
        return schema_name, parts[0]
    if len(parts) == 2:
        parsed_schema, parsed_table = parts
        if schema_name and schema_name.casefold() != parsed_schema.casefold():
            raise ValueError(
                "The schema filter does not match the fully qualified table name."
            )
        return parsed_schema, parsed_table
    raise ValueError("Invalid table name format. Use TableName or SchemaName.TableName.")


def normalize_sql_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().strip("[]").strip()
    return value or None


if __name__ == "__main__":
    raise SystemExit(main())
