from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

from translator_app.config import (
    RetrySettings,
    RuntimeConfig,
    SqlServerConnectionSettings,
)
from translator_app.languages import format_language_options, get_language
from translator_app.logging_config import configure_logging
from translator_app.runtime_paths import application_dir
from translator_app.service import DatabaseTranslationService
from translator_app.sqlserver import (
    SqlServerLocalizationRepository,
    SqlServerSchemaReader,
    connect,
)
from translator_app.translators import create_translator
from translator_app.unfinished_report import format_unfinished_report


def main(argv: list[str] | None = None) -> int:
    load_dotenv(application_dir() / ".env")
    args = build_parser().parse_args(argv)
    logger: logging.Logger | None = None
    service_started = False

    try:
        config = build_runtime_config(args)
        logger, log_file = configure_logging(config.log_dir)
        logger.info("Log file: %s", log_file)

        read_connection = connect(config.connection_string, autocommit=False)
        write_connection = (
            connect(config.connection_string, autocommit=True)
            if not config.dry_run
            else read_connection
        )
        try:
            service = DatabaseTranslationService(
                schema_reader=SqlServerSchemaReader(read_connection),
                repository=SqlServerLocalizationRepository(
                    read_connection,
                    write_connection,
                ),
                translator=create_translator(config, logger=logger),
                logger=logger,
            )
            service_started = True
            run_requested_mode(service, config, args, logger)
        finally:
            if write_connection is not read_connection:
                write_connection.close()
            read_connection.close()
        return 0
    except KeyboardInterrupt:
        if logger is not None and not service_started:
            logger.warning("Operation stopped before processing started.")
            logger.info(
                "\n%s",
                format_unfinished_report(
                    ["operation | status=stopped | reason=keyboard interrupt"],
                    operation_name="Database translation",
                ),
            )
        print("\nOperation stopped by the user.")
        return 130
    except Exception as exc:
        if logger is not None and not service_started:
            logger.exception("Execution failed before processing started: %s", exc)
            logger.info(
                "\n%s",
                format_unfinished_report(
                    [f"operation | status=failed | reason={exc}"],
                    operation_name="Database translation",
                ),
            )
        print(f"Execution error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate SQL Server Localize/Localizes tables.",
    )
    parser.add_argument(
        "--connection-string",
        default=os.getenv("SQLSERVER_CONNECTION_STRING"),
    )
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
        default=os.getenv("SQLSERVER_TRUSTED_CONNECTION", "").lower()
        in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--no-encrypt",
        action="store_true",
        default=parse_env_bool("SQLSERVER_NO_ENCRYPT", False),
        help="Set Encrypt=no in the SQL Server connection string.",
    )
    parser.add_argument(
        "--no-trust-server-certificate",
        action="store_true",
        default=not parse_env_bool("SQLSERVER_TRUST_SERVER_CERTIFICATE", True),
        help="Set TrustServerCertificate=no in the SQL Server connection string.",
    )
    parser.add_argument("--source-language-id", type=int)
    parser.add_argument("--target-language-id", type=int)
    parser.add_argument("--schema", dest="schema_name")
    parser.add_argument("--table", dest="table_name")
    parser.add_argument(
        "--test-table",
        dest="test_table_name",
        help=(
            "Translate this table first, then ask before continuing all tables. "
            "Accepts TableName or SchemaName.TableName."
        ),
    )
    parser.add_argument(
        "--test-schema",
        dest="test_schema_name",
        help="Schema for --test-table when it is not passed as SchemaName.TableName.",
    )
    parser.add_argument(
        "--continue-after-test",
        action="store_true",
        help="Continue all tables after --test-table without an interactive prompt.",
    )
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("BATCH_SIZE", "100")))
    parser.add_argument(
        "--progress-every",
        type=int,
        default=int(os.getenv("PROGRESS_EVERY", "1")),
    )
    parser.add_argument("--execute", action="store_true", help="Insert translated rows.")
    parser.add_argument(
        "--provider",
        choices=("auto", "google-free", "libretranslate"),
        default="auto",
        help=(
            "Legacy override. The default uses Google first and switches to "
            "LibreTranslate after a Google rate-limit/quota response."
        ),
    )
    parser.add_argument("--libretranslate-url", default=os.getenv("LIBRETRANSLATE_URL"))
    parser.add_argument(
        "--libretranslate-api-key",
        default=os.getenv("LIBRETRANSLATE_API_KEY"),
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=float(os.getenv("REQUEST_DELAY_SECONDS", "0.2")),
    )
    parser.add_argument("--retries", type=int, default=int(os.getenv("RETRY_ATTEMPTS", "3")))
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=float(os.getenv("RETRY_INITIAL_DELAY_SECONDS", "1")),
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=float(os.getenv("RETRY_BACKOFF_FACTOR", "2")),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(os.getenv("LOG_DIR", "logs")),
    )
    return parser


def build_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    if args.table_name and args.test_table_name:
        raise ValueError("--table and --test-table cannot be used together.")

    source_language_id = args.source_language_id or prompt_language_id(
        "Select the source language:"
    )
    target_language_id = args.target_language_id or prompt_language_id(
        "Select the target language:"
    )

    if source_language_id == target_language_id:
        raise ValueError("The source and target languages must be different.")

    if args.execute:
        dry_run = False
    elif sys.stdin.isatty():
        dry_run = not prompt_yes_no(
            "Run in execute mode and insert translated rows? Otherwise, run a dry-run.",
            default=False,
        )
    else:
        dry_run = True

    connection_settings = prompt_connection_settings(args)
    schema_name, table_name = split_table_reference(
        args.table_name,
        args.schema_name,
    )

    return RuntimeConfig(
        connection_string=connection_settings.build_connection_string(),
        source_language=get_language(source_language_id),
        target_language=get_language(target_language_id),
        dry_run=dry_run,
        schema_name=schema_name,
        table_name=table_name,
        batch_size=max(args.batch_size, 1),
        progress_every=max(args.progress_every, 1),
        translator_provider=getattr(args, "provider", "auto"),
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
    )


def run_requested_mode(
    service: DatabaseTranslationService,
    config: RuntimeConfig,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> None:
    if not args.test_table_name:
        service.run(config)
        return

    test_schema_name, test_table_name = split_table_reference(
        args.test_table_name,
        args.test_schema_name,
    )
    test_config = replace(
        config,
        schema_name=test_schema_name or config.schema_name,
        table_name=test_table_name,
    )

    logger.info(
        "Test phase started: table=%s.%s",
        test_config.schema_name or "*",
        test_config.table_name,
    )
    test_summary = service.run(test_config)

    if not should_continue_after_test(args, test_summary):
        logger.info("Stopped after the test phase. Run all tables again to continue.")
        logger.info(
            "\n%s",
            format_unfinished_report(
                getattr(test_summary, "unfinished_records", ()),
                operation_name="Database translation (test phase)",
            ),
        )
        return

    logger.info("Test phase approved. Starting all Localize/Localizes tables.")
    service.run(config)


def should_continue_after_test(
    args: argparse.Namespace,
    test_summary: object,
) -> bool:
    if args.continue_after_test:
        return True
    if not sys.stdin.isatty():
        return False

    inserted_rows = getattr(test_summary, "inserted_rows", 0)
    updated_rows = getattr(test_summary, "updated_rows", 0)
    failed_rows = getattr(test_summary, "failed_rows", 0)
    skipped_tables = getattr(test_summary, "skipped_tables", 0)
    return prompt_yes_no(
        "Test phase finished "
        f"(inserted={inserted_rows}, updated={updated_rows}, "
        f"failed={failed_rows}, skipped_tables={skipped_tables}). "
        "Continue with all tables?",
        default=False,
    )


def split_table_reference(
    table_name: str | None,
    schema_name: str | None,
) -> tuple[str | None, str | None]:
    if not table_name:
        return normalize_sql_name(schema_name), None

    parts = [normalize_sql_name(part) for part in table_name.split(".")]
    parts = [part for part in parts if part]
    if len(parts) == 1:
        return normalize_sql_name(schema_name), parts[0]
    if len(parts) == 2:
        parsed_schema_name, parsed_table_name = parts
        normalized_schema_name = normalize_sql_name(schema_name)
        if normalized_schema_name and normalized_schema_name != parsed_schema_name:
            raise ValueError(
                "The schema filter does not match the fully qualified table name: "
                f"{normalized_schema_name} != {parsed_schema_name}"
            )
        return parsed_schema_name, parsed_table_name

    raise ValueError(
        "Invalid table name format. Use TableName or SchemaName.TableName."
    )


def normalize_sql_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip("[]").strip()
    return normalized or None


def prompt_connection_settings(args: argparse.Namespace) -> SqlServerConnectionSettings:
    if args.connection_string:
        return SqlServerConnectionSettings(
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

    server = args.server or prompt_required("SQL Server host/name")
    database = args.database or prompt_required("Database name")
    trusted_connection = args.trusted_connection

    if not trusted_connection and sys.stdin.isatty():
        trusted_connection = prompt_yes_no(
            "Use Trusted_Connection?",
            default=False,
        )

    username = args.username
    password = args.password
    if not trusted_connection:
        username = username or prompt_required("SQL username")
        password = (
            password if password is not None else getpass.getpass("SQL password: ")
        )

    return SqlServerConnectionSettings(
        connection_string=None,
        driver=args.driver,
        server=server,
        database=database,
        username=username,
        password=password,
        trusted_connection=trusted_connection,
        encrypt=not args.no_encrypt,
        trust_server_certificate=not args.no_trust_server_certificate,
    )


def prompt_language_id(prompt: str) -> int:
    print(prompt)
    print(format_language_options())
    while True:
        raw_value = input("Language ID: ").strip()
        try:
            return get_language(int(raw_value)).id
        except (ValueError, TypeError):
            print("Invalid language ID. Try again.")


def prompt_required(prompt: str) -> str:
    while True:
        value = input(f"{prompt}: ").strip()
        if value:
            return value
        print("This value is required.")


def prompt_yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter y or n.")


def parse_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "y"}


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
