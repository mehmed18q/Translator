from __future__ import annotations

import json
import logging
import queue
import threading
import traceback
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from tkinter import BooleanVar, StringVar, TclError, Tk, filedialog, messagebox
from tkinter import scrolledtext
from tkinter import ttk

from translator_app.config import (
    RetrySettings,
    RuntimeConfig,
    SqlServerConnectionSettings,
)
from translator_app.languages import LANGUAGES, get_language
from translator_app.logging_config import configure_logging
from translator_app.resx_service import (
    ResxProgressSnapshot,
    ResxTranslationConfig,
    ResxTranslationService,
)
from translator_app.service import DatabaseTranslationService, ProgressSnapshot
from translator_app.sqlserver import (
    SqlServerLocalizationRepository,
    SqlServerSchemaReader,
    connect,
)
from translator_app.translators import create_translator
from translator_app.translators.base import Translator


ENV_PATH = Path(".env")
BG_COLOR = "#f6f8fb"
CARD_COLOR = "#ffffff"
TEXT_COLOR = "#182230"
MUTED_TEXT_COLOR = "#667085"
PRIMARY_COLOR = "#2563eb"
DANGER_COLOR = "#dc2626"

GUI_LANGUAGE_NAMES = {
    1: "Persian",
    2: "English",
    3: "Arabic",
    4: "French",
    5: "Chinese",
    6: "Russian",
}


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue[tuple[str, object]]) -> None:
        super().__init__()
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.events.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


class ScanOnlyTranslator(Translator):
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        return text


class TranslatorGuiApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("SQL Server Localize Translator")
        self.root.geometry("1120x780")
        self.root.minsize(980, 700)
        self.root.configure(background=BG_COLOR)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.followup_config: RuntimeConfig | None = None
        self.running_job_name = ""

        self.env_values = read_env_values(ENV_PATH)
        self._configure_style()
        self._build_variables()
        self._build_layout()
        self._poll_events()

    def _configure_style(self) -> None:
        self.root.option_add("*Font", "TkDefaultFont")
        self.root.option_add("*Dialog.msg.font", "TkDefaultFont")

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", font=("TkDefaultFont", 10))
        style.configure("App.TFrame", background=BG_COLOR)
        style.configure("TFrame", background=BG_COLOR)
        style.configure("Card.TFrame", background=CARD_COLOR)
        style.configure("Metric.TFrame", background=CARD_COLOR)
        style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR)
        style.configure("Field.TLabel", background=CARD_COLOR, foreground=TEXT_COLOR)
        style.configure("Muted.TLabel", background=BG_COLOR, foreground=MUTED_TEXT_COLOR)
        style.configure("TCheckbutton", background=CARD_COLOR, foreground=TEXT_COLOR)
        style.configure("TEntry", padding=(6, 4))
        style.configure("TCombobox", padding=(6, 4))
        style.configure(
            "Header.TLabel",
            background=BG_COLOR,
            foreground="#101828",
            font=("TkDefaultFont", 18, "bold"),
        )
        style.configure(
            "SubHeader.TLabel",
            background=BG_COLOR,
            foreground=MUTED_TEXT_COLOR,
            font=("TkDefaultFont", 10),
        )
        style.configure(
            "MetricTitle.TLabel",
            background=CARD_COLOR,
            foreground=MUTED_TEXT_COLOR,
            font=("TkDefaultFont", 9),
        )
        style.configure(
            "MetricValue.TLabel",
            background=CARD_COLOR,
            foreground=TEXT_COLOR,
            font=("TkDefaultFont", 15, "bold"),
        )
        style.configure(
            "Card.TLabelframe",
            background=CARD_COLOR,
            bordercolor="#d0d5dd",
            relief="solid",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=BG_COLOR,
            foreground=TEXT_COLOR,
            font=("TkDefaultFont", 11, "bold"),
        )
        style.configure(
            "TNotebook",
            background=BG_COLOR,
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(18, 9),
            font=("TkDefaultFont", 10, "bold"),
        )
        style.configure("TButton", padding=(12, 7), font=("TkDefaultFont", 10))
        style.configure(
            "Accent.TButton",
            padding=(14, 8),
            background=PRIMARY_COLOR,
            foreground="#ffffff",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#1d4ed8"), ("disabled", "#93c5fd")],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure(
            "Danger.TButton",
            padding=(14, 8),
            background=DANGER_COLOR,
            foreground="#ffffff",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#b91c1c"), ("disabled", "#fca5a5")],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure("Horizontal.TProgressbar", thickness=12)

    def _build_variables(self) -> None:
        env = self.env_values

        self.connection_string_var = StringVar(
            value=env.get("SQLSERVER_CONNECTION_STRING", "")
        )
        self.driver_var = StringVar(
            value=env.get("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
        )
        self.server_var = StringVar(value=env.get("SQLSERVER_SERVER", ""))
        self.database_var = StringVar(value=env.get("SQLSERVER_DATABASE", ""))
        self.username_var = StringVar(value=env.get("SQLSERVER_USERNAME", ""))
        self.password_var = StringVar(value=env.get("SQLSERVER_PASSWORD", ""))
        self.trusted_connection_var = BooleanVar(
            value=parse_bool(env.get("SQLSERVER_TRUSTED_CONNECTION", "false"))
        )
        self.encrypt_var = BooleanVar(
            value=not parse_bool(env.get("SQLSERVER_NO_ENCRYPT", "false"))
        )
        self.trust_server_certificate_var = BooleanVar(
            value=parse_bool(env.get("SQLSERVER_TRUST_SERVER_CERTIFICATE", "true"))
        )

        self.provider_var = StringVar(
            value=env.get("TRANSLATOR_PROVIDER", "libretranslate")
        )
        self.libretranslate_url_var = StringVar(
            value=env.get("LIBRETRANSLATE_URL", "http://127.0.0.1:5000")
        )
        self.libretranslate_api_key_var = StringVar(
            value=env.get("LIBRETRANSLATE_API_KEY", "")
        )
        self.request_timeout_var = StringVar(
            value=env.get("REQUEST_TIMEOUT_SECONDS", "20")
        )
        self.request_delay_var = StringVar(value=env.get("REQUEST_DELAY_SECONDS", "0.2"))
        self.retries_var = StringVar(value=env.get("RETRY_ATTEMPTS", "3"))
        self.retry_delay_var = StringVar(
            value=env.get("RETRY_INITIAL_DELAY_SECONDS", "1")
        )
        self.retry_backoff_var = StringVar(
            value=env.get("RETRY_BACKOFF_FACTOR", "2")
        )
        self.batch_size_var = StringVar(value=env.get("BATCH_SIZE", "100"))
        self.progress_every_var = StringVar(value=env.get("PROGRESS_EVERY", "1"))
        self.log_dir_var = StringVar(value=env.get("LOG_DIR", "logs"))

        self.source_language_var = StringVar(value=language_label(1))
        self.target_language_var = StringVar(value=language_label(2))
        self.operation_schema_var = StringVar(value="")
        self.operation_table_var = StringVar(value="")
        self.test_table_var = StringVar(value="")
        self.dry_run_var = BooleanVar(value=True)

        self.resx_resource_dir_var = StringVar(
            value=env.get("RESX_RESOURCE_DIR", "")
        )
        self.resx_source_language_var = StringVar(
            value=language_label(parse_language_id(env.get("RESX_SOURCE_LANGUAGE_ID"), 1))
        )
        self.resx_target_language_var = StringVar(
            value=language_label(parse_language_id(env.get("RESX_TARGET_LANGUAGE_ID"), 2))
        )
        self.resx_resources_file_var = BooleanVar(
            value=parse_bool(env.get("RESX_INCLUDE_RESOURCES", "true"))
        )
        self.resx_messages_file_var = BooleanVar(
            value=parse_bool(env.get("RESX_INCLUDE_MESSAGES", "true"))
        )
        self.resx_message_file_var = BooleanVar(
            value=parse_bool(env.get("RESX_INCLUDE_MESSAGE", "false"))
        )
        self.resx_extra_files_var = StringVar(value=env.get("RESX_EXTRA_FILES", ""))
        self.resx_dry_run_var = BooleanVar(
            value=parse_bool(env.get("RESX_DRY_RUN", "true"))
        )

        self.status_var = StringVar(value="Ready")
        self.log_file_var = StringVar(value="-")
        self.current_table_var = StringVar(value="-")
        self.overall_percent_var = StringVar(value="0.00%")
        self.table_percent_var = StringVar(value="0.00%")
        self.discovered_tables_var = StringVar(value="0")
        self.eligible_tables_var = StringVar(value="0")
        self.skipped_tables_var = StringVar(value="0")
        self.total_rows_var = StringVar(value="0")
        self.processed_rows_var = StringVar(value="0")
        self.remaining_rows_var = StringVar(value="0")
        self.inserted_rows_var = StringVar(value="0")
        self.skipped_existing_rows_var = StringVar(value="0")
        self.failed_rows_var = StringVar(value="0")

        self.resx_status_var = StringVar(value="Ready")
        self.resx_log_file_var = StringVar(value="-")
        self.resx_current_file_var = StringVar(value="-")
        self.resx_current_key_var = StringVar(value="-")
        self.resx_overall_percent_var = StringVar(value="0.00%")
        self.resx_file_percent_var = StringVar(value="0.00%")
        self.resx_discovered_files_var = StringVar(value="0")
        self.resx_eligible_files_var = StringVar(value="0")
        self.resx_skipped_files_var = StringVar(value="0")
        self.resx_total_entries_var = StringVar(value="0")
        self.resx_pending_entries_var = StringVar(value="0")
        self.resx_processed_entries_var = StringVar(value="0")
        self.resx_remaining_entries_var = StringVar(value="0")
        self.resx_translated_entries_var = StringVar(value="0")
        self.resx_skipped_existing_entries_var = StringVar(value="0")
        self.resx_skipped_empty_entries_var = StringVar(value="0")
        self.resx_failed_entries_var = StringVar(value="0")
        self.resx_created_files_var = StringVar(value="0")
        self.resx_updated_files_var = StringVar(value="0")

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.root, padding=16, style="App.TFrame")
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Database Localization Translator",
            style="Header.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            textvariable=self.status_var,
            style="SubHeader.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        notebook = ttk.Notebook(shell)
        notebook.grid(row=1, column=0, sticky="nsew")

        self.settings_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")
        self.operation_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")
        self.resources_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")
        self.logs_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")

        notebook.add(self.settings_tab, text="Connection")
        notebook.add(self.operation_tab, text="Operation")
        notebook.add(self.resources_tab, text="Resources")
        notebook.add(self.logs_tab, text="Logs")

        self._build_settings_tab()
        self._build_operation_tab()
        self._build_resources_tab()
        self._build_logs_tab()

    def _build_settings_tab(self) -> None:
        self.settings_tab.columnconfigure(0, weight=1)
        self.settings_tab.columnconfigure(1, weight=1)

        db_frame = ttk.LabelFrame(
            self.settings_tab,
            text="SQL Server",
            padding=12,
            style="Card.TLabelframe",
        )
        db_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        db_frame.columnconfigure(1, weight=1)

        add_entry(db_frame, 0, "Connection string", self.connection_string_var)
        add_entry(db_frame, 1, "Driver", self.driver_var)
        add_entry(db_frame, 2, "Server", self.server_var)
        add_entry(db_frame, 3, "Database", self.database_var)
        add_entry(db_frame, 4, "Username", self.username_var)
        add_entry(db_frame, 5, "Password", self.password_var, show="*")

        ttk.Checkbutton(
            db_frame,
            text="Trusted Connection",
            variable=self.trusted_connection_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            db_frame,
            text="Encrypt",
            variable=self.encrypt_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            db_frame,
            text="Trust server certificate",
            variable=self.trust_server_certificate_var,
        ).grid(row=8, column=0, columnspan=2, sticky="w")

        translator_frame = ttk.LabelFrame(
            self.settings_tab,
            text="Translation Service",
            padding=12,
            style="Card.TLabelframe",
        )
        translator_frame.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(8, 0),
            pady=(0, 10),
        )
        translator_frame.columnconfigure(1, weight=1)

        ttk.Label(translator_frame, text="Provider", style="Field.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=4,
        )
        ttk.Combobox(
            translator_frame,
            textvariable=self.provider_var,
            values=("libretranslate", "google-free"),
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", pady=4)
        add_entry(translator_frame, 1, "Libre URL", self.libretranslate_url_var)
        add_entry(translator_frame, 2, "Libre API key", self.libretranslate_api_key_var)
        add_entry(translator_frame, 3, "Request timeout", self.request_timeout_var)
        add_entry(translator_frame, 4, "Request delay", self.request_delay_var)
        add_entry(translator_frame, 5, "Retries", self.retries_var)
        add_entry(translator_frame, 6, "Retry delay", self.retry_delay_var)
        add_entry(translator_frame, 7, "Retry backoff", self.retry_backoff_var)

        runtime_frame = ttk.LabelFrame(
            self.settings_tab,
            text="Runtime Defaults",
            padding=12,
            style="Card.TLabelframe",
        )
        runtime_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        runtime_frame.columnconfigure(1, weight=1)
        runtime_frame.columnconfigure(3, weight=1)

        add_entry(runtime_frame, 0, "Batch size", self.batch_size_var, column_offset=0)
        add_entry(
            runtime_frame,
            0,
            "Progress every",
            self.progress_every_var,
            column_offset=2,
        )
        add_entry(runtime_frame, 1, "Log dir", self.log_dir_var, column_offset=0)

        button_frame = ttk.Frame(self.settings_tab, style="App.TFrame")
        button_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))

        ttk.Button(
            button_frame,
            text="Test Database",
            command=self.test_database_connection,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            button_frame,
            text="Test LibreTranslate",
            command=self.test_libretranslate,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            button_frame,
            text="Save Settings",
            command=self.save_settings,
            style="Accent.TButton",
        ).grid(row=0, column=2)

    def _build_operation_tab(self) -> None:
        self.operation_tab.columnconfigure(0, weight=1)
        self.operation_tab.rowconfigure(2, weight=1)

        inputs = ttk.LabelFrame(
            self.operation_tab,
            text="Run Options",
            padding=12,
            style="Card.TLabelframe",
        )
        inputs.grid(row=0, column=0, sticky="ew")
        inputs.columnconfigure(1, weight=1)
        inputs.columnconfigure(3, weight=1)

        language_values = [language_label(language_id) for language_id in LANGUAGES]
        add_combo(inputs, 0, "Source", self.source_language_var, language_values, 0)
        add_combo(inputs, 0, "Target", self.target_language_var, language_values, 2)
        add_entry(inputs, 1, "Schema filter", self.operation_schema_var, 0)
        add_entry(inputs, 1, "Only table", self.operation_table_var, 2)
        add_entry(inputs, 2, "Test table", self.test_table_var, 0)
        ttk.Checkbutton(
            inputs,
            text="Dry-run: count only, do not insert rows",
            variable=self.dry_run_var,
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=4)

        buttons = ttk.Frame(self.operation_tab, style="App.TFrame")
        buttons.grid(row=1, column=0, sticky="ew", pady=(12, 12))
        buttons.columnconfigure(5, weight=1)

        self.run_test_button = ttk.Button(
            buttons,
            text="Test Table, Then Ask",
            command=self.start_test_then_prompt,
            style="Accent.TButton",
        )
        self.run_test_button.grid(row=0, column=0, padx=(0, 8))

        self.run_table_button = ttk.Button(
            buttons,
            text="Run Selected Table",
            command=self.start_single_table,
        )
        self.run_table_button.grid(row=0, column=1, padx=(0, 8))

        self.run_all_button = ttk.Button(
            buttons,
            text="Run All Tables",
            command=self.start_all_tables,
        )
        self.run_all_button.grid(row=0, column=2, padx=(0, 8))

        self.stop_button = ttk.Button(
            buttons,
            text="Stop",
            command=self.stop_operation,
            state="disabled",
            style="Danger.TButton",
        )
        self.stop_button.grid(row=0, column=3)

        progress = ttk.LabelFrame(
            self.operation_tab,
            text="Progress",
            padding=12,
            style="Card.TLabelframe",
        )
        progress.grid(row=2, column=0, sticky="nsew")
        progress.columnconfigure(1, weight=1)
        progress.columnconfigure(3, weight=1)

        ttk.Label(progress, text="Status", style="Field.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Label(progress, textvariable=self.status_var, style="Field.TLabel").grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=4,
        )
        ttk.Label(progress, text="Log file", style="Field.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Label(progress, textvariable=self.log_file_var, style="Field.TLabel").grid(
            row=1,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=4,
        )
        ttk.Label(progress, text="Current table", style="Field.TLabel").grid(
            row=2,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Label(
            progress,
            textvariable=self.current_table_var,
            style="Field.TLabel",
        ).grid(
            row=2,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=4,
        )

        self.overall_progress = ttk.Progressbar(progress, maximum=100)
        self.overall_progress.grid(row=3, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(progress, text="Overall", style="Field.TLabel").grid(
            row=4,
            column=0,
            sticky="w",
        )
        ttk.Label(
            progress,
            textvariable=self.overall_percent_var,
            style="Field.TLabel",
        ).grid(
            row=4,
            column=1,
            sticky="w",
        )

        self.table_progress = ttk.Progressbar(progress, maximum=100)
        self.table_progress.grid(row=5, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(progress, text="Table", style="Field.TLabel").grid(
            row=6,
            column=0,
            sticky="w",
        )
        ttk.Label(
            progress,
            textvariable=self.table_percent_var,
            style="Field.TLabel",
        ).grid(
            row=6,
            column=1,
            sticky="w",
        )

        metrics = ttk.Frame(progress, style="Card.TFrame")
        metrics.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        for column_index in range(4):
            metrics.columnconfigure(column_index, weight=1)

        self._add_metric(metrics, 0, 0, "Discovered tables", self.discovered_tables_var)
        self._add_metric(metrics, 0, 1, "Eligible tables", self.eligible_tables_var)
        self._add_metric(metrics, 0, 2, "Skipped tables", self.skipped_tables_var)
        self._add_metric(metrics, 0, 3, "Total pending rows", self.total_rows_var)
        self._add_metric(metrics, 1, 0, "Processed rows", self.processed_rows_var)
        self._add_metric(metrics, 1, 1, "Remaining rows", self.remaining_rows_var)
        self._add_metric(metrics, 1, 2, "Inserted rows", self.inserted_rows_var)
        self._add_metric(metrics, 1, 3, "Existing skipped", self.skipped_existing_rows_var)
        self._add_metric(metrics, 2, 0, "Failed rows", self.failed_rows_var)

    def _build_resources_tab(self) -> None:
        self.resources_tab.columnconfigure(0, weight=1)
        self.resources_tab.rowconfigure(2, weight=1)

        inputs = ttk.LabelFrame(
            self.resources_tab,
            text="RESX Options",
            padding=12,
            style="Card.TLabelframe",
        )
        inputs.grid(row=0, column=0, sticky="ew")
        inputs.columnconfigure(1, weight=1)
        inputs.columnconfigure(3, weight=1)

        ttk.Label(inputs, text="Resource folder", style="Field.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=4,
        )
        ttk.Entry(inputs, textvariable=self.resx_resource_dir_var).grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=4,
        )
        ttk.Button(
            inputs,
            text="Browse",
            command=self.browse_resx_directory,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0), pady=4)

        language_values = [language_label(language_id) for language_id in LANGUAGES]
        add_combo(inputs, 1, "Source", self.resx_source_language_var, language_values, 0)
        add_combo(inputs, 1, "Target", self.resx_target_language_var, language_values, 2)

        files = ttk.Frame(inputs, style="Card.TFrame")
        files.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(files, text="Resource files", style="Field.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 12),
        )
        ttk.Checkbutton(
            files,
            text="Resources.resx",
            variable=self.resx_resources_file_var,
        ).grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Checkbutton(
            files,
            text="Messages.resx",
            variable=self.resx_messages_file_var,
        ).grid(row=0, column=2, sticky="w", padx=(0, 12))
        ttk.Checkbutton(
            files,
            text="Message.resx",
            variable=self.resx_message_file_var,
        ).grid(row=0, column=3, sticky="w")

        add_entry(inputs, 3, "Extra files", self.resx_extra_files_var, 0)
        ttk.Checkbutton(
            inputs,
            text="Dry-run: scan only, do not write RESX files",
            variable=self.resx_dry_run_var,
        ).grid(row=3, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=4)

        buttons = ttk.Frame(self.resources_tab, style="App.TFrame")
        buttons.grid(row=1, column=0, sticky="ew", pady=(12, 12))
        buttons.columnconfigure(3, weight=1)

        self.resx_scan_button = ttk.Button(
            buttons,
            text="Scan RESX",
            command=self.scan_resx_files,
        )
        self.resx_scan_button.grid(row=0, column=0, padx=(0, 8))

        self.resx_run_button = ttk.Button(
            buttons,
            text="Translate RESX",
            command=self.start_resx_translation,
            style="Accent.TButton",
        )
        self.resx_run_button.grid(row=0, column=1, padx=(0, 8))

        self.resx_stop_button = ttk.Button(
            buttons,
            text="Stop",
            command=self.stop_operation,
            state="disabled",
            style="Danger.TButton",
        )
        self.resx_stop_button.grid(row=0, column=2)

        progress = ttk.LabelFrame(
            self.resources_tab,
            text="RESX Progress",
            padding=12,
            style="Card.TLabelframe",
        )
        progress.grid(row=2, column=0, sticky="nsew")
        progress.columnconfigure(1, weight=1)
        progress.columnconfigure(3, weight=1)

        ttk.Label(progress, text="Status", style="Field.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Label(progress, textvariable=self.resx_status_var, style="Field.TLabel").grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=4,
        )
        ttk.Label(progress, text="Log file", style="Field.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Label(
            progress,
            textvariable=self.resx_log_file_var,
            style="Field.TLabel",
        ).grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(progress, text="Current file", style="Field.TLabel").grid(
            row=2,
            column=0,
            sticky="w",
            pady=4,
        )
        ttk.Label(
            progress,
            textvariable=self.resx_current_file_var,
            style="Field.TLabel",
        ).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(progress, text="Current key", style="Field.TLabel").grid(
            row=2,
            column=2,
            sticky="w",
            padx=(12, 8),
            pady=4,
        )
        ttk.Label(
            progress,
            textvariable=self.resx_current_key_var,
            style="Field.TLabel",
        ).grid(row=2, column=3, sticky="ew", pady=4)

        self.resx_overall_progress = ttk.Progressbar(progress, maximum=100)
        self.resx_overall_progress.grid(
            row=3,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=8,
        )
        ttk.Label(progress, text="Overall", style="Field.TLabel").grid(
            row=4,
            column=0,
            sticky="w",
        )
        ttk.Label(
            progress,
            textvariable=self.resx_overall_percent_var,
            style="Field.TLabel",
        ).grid(row=4, column=1, sticky="w")

        self.resx_file_progress = ttk.Progressbar(progress, maximum=100)
        self.resx_file_progress.grid(
            row=5,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=8,
        )
        ttk.Label(progress, text="File", style="Field.TLabel").grid(
            row=6,
            column=0,
            sticky="w",
        )
        ttk.Label(
            progress,
            textvariable=self.resx_file_percent_var,
            style="Field.TLabel",
        ).grid(row=6, column=1, sticky="w")

        metrics = ttk.Frame(progress, style="Card.TFrame")
        metrics.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        for column_index in range(4):
            metrics.columnconfigure(column_index, weight=1)

        self._add_metric(metrics, 0, 0, "Selected files", self.resx_discovered_files_var)
        self._add_metric(metrics, 0, 1, "Eligible files", self.resx_eligible_files_var)
        self._add_metric(metrics, 0, 2, "Skipped files", self.resx_skipped_files_var)
        self._add_metric(metrics, 0, 3, "Total keys", self.resx_total_entries_var)
        self._add_metric(metrics, 1, 0, "Pending keys", self.resx_pending_entries_var)
        self._add_metric(metrics, 1, 1, "Processed keys", self.resx_processed_entries_var)
        self._add_metric(metrics, 1, 2, "Remaining keys", self.resx_remaining_entries_var)
        self._add_metric(metrics, 1, 3, "Translated keys", self.resx_translated_entries_var)
        self._add_metric(
            metrics,
            2,
            0,
            "Existing skipped",
            self.resx_skipped_existing_entries_var,
        )
        self._add_metric(metrics, 2, 1, "Empty skipped", self.resx_skipped_empty_entries_var)
        self._add_metric(metrics, 2, 2, "Failed keys", self.resx_failed_entries_var)
        self._add_metric(metrics, 2, 3, "Created files", self.resx_created_files_var)
        self._add_metric(metrics, 3, 0, "Updated files", self.resx_updated_files_var)

    def _build_logs_tab(self) -> None:
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            self.logs_tab,
            height=24,
            wrap="word",
            state="disabled",
            bg="#101828",
            fg="#e4e7ec",
            insertbackground="#e4e7ec",
            selectbackground="#344054",
            relief="flat",
            padx=12,
            pady=12,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(self.logs_tab, style="App.TFrame")
        controls.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(controls, text="Clear Log View", command=self.clear_log_view).grid(
            row=0,
            column=0,
        )

    def _add_metric(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        title: str,
        variable: StringVar,
    ) -> None:
        frame = ttk.Frame(parent, padding=8, style="Metric.TFrame")
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 8), pady=(0, 8))
        ttk.Label(frame, text=title, style="MetricTitle.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(frame, textvariable=variable, style="MetricValue.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
        )

    def save_settings(self) -> None:
        values = self._settings_env_values()
        write_env_values(ENV_PATH, values)
        self.env_values = values
        messagebox.showinfo("Settings", f"Settings saved to {ENV_PATH.resolve()}.")

    def test_database_connection(self) -> None:
        try:
            connection_string = self._build_connection_settings().build_connection_string()
        except Exception as exc:
            messagebox.showerror("Settings Error", str(exc))
            return

        self._append_log("Testing database connection...")
        thread = threading.Thread(
            target=self._database_test_worker,
            args=(connection_string,),
            daemon=True,
        )
        thread.start()

    def _database_test_worker(self, connection_string: str) -> None:
        connection = None
        try:
            connection = connect(connection_string, autocommit=False)
            tables = SqlServerSchemaReader(connection).get_localize_tables()
            self.events.put(
                (
                    "message",
                    (
                        "Database Connection",
                        f"Connection succeeded. Translation tables found: {len(tables)}",
                    ),
                )
            )
        except Exception as exc:
            self.events.put(("error-message", ("Database Connection Error", str(exc))))
        finally:
            if connection is not None:
                connection.close()

    def test_libretranslate(self) -> None:
        url = self.libretranslate_url_var.get().strip().rstrip("/")
        if not url:
            messagebox.showerror("LibreTranslate", "LibreTranslate URL is required.")
            return

        self._append_log("Testing LibreTranslate...")
        thread = threading.Thread(
            target=self._libretranslate_test_worker,
            args=(url,),
            daemon=True,
        )
        thread.start()

    def _libretranslate_test_worker(self, url: str) -> None:
        try:
            languages = http_json(f"{url}/languages", timeout=8)
            translated = http_json(
                f"{url}/translate",
                timeout=12,
                method="POST",
                payload={
                    "q": "Hello",
                    "source": "en",
                    "target": "fr",
                    "format": "text",
                },
            )
            self.events.put(
                (
                    "message",
                    (
                        "LibreTranslate",
                        "Connection succeeded.\n"
                        f"Languages returned: {len(languages)}\n"
                        f"Translation test: {translated.get('translatedText', '-')}",
                    ),
                )
            )
        except Exception as exc:
            self.events.put(("error-message", ("LibreTranslate Error", str(exc))))

    def browse_resx_directory(self) -> None:
        initial_dir = self.resx_resource_dir_var.get().strip() or "."
        selected_dir = filedialog.askdirectory(
            title="Select RESX resource folder",
            initialdir=initial_dir,
        )
        if selected_dir:
            self.resx_resource_dir_var.set(selected_dir)

    def scan_resx_files(self) -> None:
        try:
            resx_config, translator_config = self._build_resx_runtime_config(
                force_dry_run=True
            )
        except Exception as exc:
            messagebox.showerror("RESX Settings Error", str(exc))
            return

        self._start_resx_job("resx-scan", resx_config, translator_config)

    def start_resx_translation(self) -> None:
        try:
            resx_config, translator_config = self._build_resx_runtime_config()
        except Exception as exc:
            messagebox.showerror("RESX Settings Error", str(exc))
            return

        if not resx_config.dry_run:
            confirmed = messagebox.askyesno(
                "Confirm RESX Write Mode",
                "Dry-run is off. The app will create or update RESX files. Continue?",
            )
            if not confirmed:
                return

        self._start_resx_job("resx-translate", resx_config, translator_config)

    def _start_resx_job(
        self,
        job_name: str,
        resx_config: ResxTranslationConfig,
        translator_config: RuntimeConfig,
    ) -> None:
        if self._is_worker_running():
            messagebox.showwarning("Already Running", "Another operation is already running.")
            return

        self.running_job_name = job_name
        self.cancel_event.clear()
        self._reset_resx_progress()
        self._set_running(True)
        self._append_log(f"Job started: {job_name}")

        self.worker_thread = threading.Thread(
            target=self._resx_worker,
            args=(job_name, resx_config, translator_config),
            daemon=True,
        )
        self.worker_thread.start()

    def _resx_worker(
        self,
        job_name: str,
        resx_config: ResxTranslationConfig,
        translator_config: RuntimeConfig,
    ) -> None:
        log_file: Path | None = None

        try:
            queue_handler = QueueLogHandler(self.events)
            logger, log_file = configure_logging(
                translator_config.log_dir,
                extra_handlers=(queue_handler,),
            )
            self.events.put(("log-file", str(log_file)))

            translator = (
                ScanOnlyTranslator()
                if resx_config.dry_run
                else create_translator(translator_config)
            )
            service = ResxTranslationService(
                translator=translator,
                logger=logger,
                progress_callback=lambda snapshot: self.events.put(
                    ("resx-progress", snapshot)
                ),
                cancel_callback=self.cancel_event.is_set,
            )
            summary = service.run(resx_config)
            self.events.put(("job-done", (job_name, summary, str(log_file))))
        except Exception as exc:
            self.events.put(
                (
                    "job-error",
                    (
                        job_name,
                        str(exc),
                        traceback.format_exc(),
                        str(log_file) if log_file else "-",
                    ),
                )
            )

    def start_test_then_prompt(self) -> None:
        test_table = self.test_table_var.get().strip()
        if not test_table:
            messagebox.showerror("Test Table", "Enter a test table name.")
            return

        try:
            full_config = self._build_runtime_config(
                schema_name=self.operation_schema_var.get(),
                table_name=None,
            )
            test_schema, test_table_name = split_table_reference(
                test_table,
                self.operation_schema_var.get(),
            )
            test_config = replace(
                full_config,
                schema_name=test_schema or full_config.schema_name,
                table_name=test_table_name,
            )
        except Exception as exc:
            messagebox.showerror("Settings Error", str(exc))
            return

        self.followup_config = full_config
        self._start_job("test-then-prompt", test_config)

    def start_single_table(self) -> None:
        table_name = self.operation_table_var.get().strip() or self.test_table_var.get().strip()
        if not table_name:
            messagebox.showerror(
                "Table",
                "Enter a table name in Only table or Test table.",
            )
            return

        try:
            schema_name, parsed_table_name = split_table_reference(
                table_name,
                self.operation_schema_var.get(),
            )
            config = self._build_runtime_config(
                schema_name=schema_name,
                table_name=parsed_table_name,
            )
        except Exception as exc:
            messagebox.showerror("Settings Error", str(exc))
            return

        self.followup_config = None
        self._start_job("single-table", config)

    def start_all_tables(self) -> None:
        try:
            config = self._build_runtime_config(
                schema_name=self.operation_schema_var.get(),
                table_name=None,
            )
        except Exception as exc:
            messagebox.showerror("Settings Error", str(exc))
            return

        self.followup_config = None
        self._start_job("all-tables", config)

    def _start_job(self, job_name: str, config: RuntimeConfig) -> None:
        if self._is_worker_running():
            messagebox.showwarning("Already Running", "Another operation is already running.")
            return

        if not config.dry_run:
            confirmed = messagebox.askyesno(
                "Confirm Execute Mode",
                "Dry-run is off. The app will insert new rows. Continue?",
            )
            if not confirmed:
                return

        self.running_job_name = job_name
        self.cancel_event.clear()
        self._reset_progress()
        self._set_running(True)
        self._append_log(f"Job started: {job_name}")

        self.worker_thread = threading.Thread(
            target=self._translation_worker,
            args=(job_name, config),
            daemon=True,
        )
        self.worker_thread.start()

    def _translation_worker(self, job_name: str, config: RuntimeConfig) -> None:
        read_connection = None
        write_connection = None
        log_file: Path | None = None

        try:
            queue_handler = QueueLogHandler(self.events)
            logger, log_file = configure_logging(
                config.log_dir,
                extra_handlers=(queue_handler,),
            )
            self.events.put(("log-file", str(log_file)))

            read_connection = connect(config.connection_string, autocommit=False)
            write_connection = (
                connect(config.connection_string, autocommit=True)
                if not config.dry_run
                else read_connection
            )

            service = DatabaseTranslationService(
                schema_reader=SqlServerSchemaReader(read_connection),
                repository=SqlServerLocalizationRepository(
                    read_connection,
                    write_connection,
                ),
                translator=create_translator(config),
                logger=logger,
                progress_callback=lambda snapshot: self.events.put(
                    ("progress", snapshot)
                ),
                cancel_callback=self.cancel_event.is_set,
            )
            summary = service.run(config)
            self.events.put(("job-done", (job_name, summary, str(log_file))))
        except Exception as exc:
            self.events.put(
                (
                    "job-error",
                    (
                        job_name,
                        str(exc),
                        traceback.format_exc(),
                        str(log_file) if log_file else "-",
                    ),
                )
            )
        finally:
            try:
                if write_connection is not None and write_connection is not read_connection:
                    write_connection.close()
                if read_connection is not None:
                    read_connection.close()
            except Exception:
                pass

    def stop_operation(self) -> None:
        if not self._is_worker_running():
            return
        self.cancel_event.set()
        self.status_var.set("Stop requested...")
        self.resx_status_var.set("Stop requested...")
        self._append_log("Stop requested. The current row will finish before the job stops.")

    def _build_runtime_config(
        self,
        *,
        schema_name: str | None,
        table_name: str | None,
    ) -> RuntimeConfig:
        source_language_id = language_id_from_label(self.source_language_var.get())
        target_language_id = language_id_from_label(self.target_language_var.get())
        if source_language_id == target_language_id:
            raise ValueError("Source and target languages must be different.")

        connection_settings = self._build_connection_settings()

        return RuntimeConfig(
            connection_string=connection_settings.build_connection_string(),
            source_language=get_language(source_language_id),
            target_language=get_language(target_language_id),
            dry_run=self.dry_run_var.get(),
            schema_name=normalize_sql_name(schema_name),
            table_name=normalize_sql_name(table_name),
            batch_size=max(parse_int(self.batch_size_var.get(), "Batch size"), 1),
            progress_every=max(
                parse_int(self.progress_every_var.get(), "Progress every"),
                1,
            ),
            translator_provider=self.provider_var.get(),
            request_timeout_seconds=max(
                parse_float(self.request_timeout_var.get(), "Request timeout"),
                1,
            ),
            request_delay_seconds=max(
                parse_float(self.request_delay_var.get(), "Request delay"),
                0,
            ),
            libretranslate_url=normalize_url(self.libretranslate_url_var.get()),
            libretranslate_api_key=self.libretranslate_api_key_var.get().strip() or None,
            log_dir=Path(self.log_dir_var.get().strip() or "logs"),
            retry=RetrySettings(
                attempts=max(parse_int(self.retries_var.get(), "Retries"), 1),
                initial_delay_seconds=max(
                    parse_float(self.retry_delay_var.get(), "Retry delay"),
                    0,
                ),
                backoff_factor=max(
                    parse_float(self.retry_backoff_var.get(), "Retry backoff"),
                    1,
                ),
            ),
        )

    def _build_resx_runtime_config(
        self,
        *,
        force_dry_run: bool = False,
    ) -> tuple[ResxTranslationConfig, RuntimeConfig]:
        source_language_id = language_id_from_label(self.resx_source_language_var.get())
        target_language_id = language_id_from_label(self.resx_target_language_var.get())
        if source_language_id == target_language_id:
            raise ValueError("Source and target languages must be different.")

        resource_dir_text = self.resx_resource_dir_var.get().strip()
        if not resource_dir_text:
            raise ValueError("Resource folder is required.")

        resource_dir = Path(resource_dir_text).expanduser()
        if not resource_dir.exists():
            raise ValueError(f"Resource folder does not exist: {resource_dir}")
        if not resource_dir.is_dir():
            raise ValueError(f"Resource folder is not a directory: {resource_dir}")

        base_file_names = self._selected_resx_file_names()
        if not base_file_names:
            raise ValueError("Select at least one RESX file.")

        retry = RetrySettings(
            attempts=max(parse_int(self.retries_var.get(), "Retries"), 1),
            initial_delay_seconds=max(
                parse_float(self.retry_delay_var.get(), "Retry delay"),
                0,
            ),
            backoff_factor=max(
                parse_float(self.retry_backoff_var.get(), "Retry backoff"),
                1,
            ),
        )
        source_language = get_language(source_language_id)
        target_language = get_language(target_language_id)
        dry_run = True if force_dry_run else self.resx_dry_run_var.get()

        resx_config = ResxTranslationConfig(
            resource_dir=resource_dir,
            base_file_names=base_file_names,
            source_language=source_language,
            target_language=target_language,
            dry_run=dry_run,
            progress_every=max(
                parse_int(self.progress_every_var.get(), "Progress every"),
                1,
            ),
            retry=retry,
        )
        translator_config = RuntimeConfig(
            connection_string="",
            source_language=source_language,
            target_language=target_language,
            dry_run=dry_run,
            schema_name=None,
            table_name=None,
            batch_size=1,
            progress_every=resx_config.progress_every,
            translator_provider=self.provider_var.get(),
            request_timeout_seconds=max(
                parse_float(self.request_timeout_var.get(), "Request timeout"),
                1,
            ),
            request_delay_seconds=max(
                parse_float(self.request_delay_var.get(), "Request delay"),
                0,
            ),
            libretranslate_url=normalize_url(self.libretranslate_url_var.get()),
            libretranslate_api_key=self.libretranslate_api_key_var.get().strip() or None,
            log_dir=Path(self.log_dir_var.get().strip() or "logs"),
            retry=retry,
        )
        return resx_config, translator_config

    def _selected_resx_file_names(self) -> tuple[str, ...]:
        file_names: list[str] = []
        if self.resx_resources_file_var.get():
            file_names.append("Resources.resx")
        if self.resx_messages_file_var.get():
            file_names.append("Messages.resx")
        if self.resx_message_file_var.get():
            file_names.append("Message.resx")

        for raw_name in self.resx_extra_files_var.get().split(","):
            clean_name = raw_name.strip()
            if clean_name:
                file_names.append(clean_name)

        deduped_names: list[str] = []
        seen_names: set[str] = set()
        for file_name in file_names:
            normalized_name = file_name.casefold()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            deduped_names.append(file_name)
        return tuple(deduped_names)

    def _build_connection_settings(self) -> SqlServerConnectionSettings:
        return SqlServerConnectionSettings(
            connection_string=self.connection_string_var.get().strip() or None,
            driver=self.driver_var.get().strip() or "ODBC Driver 18 for SQL Server",
            server=self.server_var.get().strip(),
            database=self.database_var.get().strip(),
            username=self.username_var.get().strip() or None,
            password=self.password_var.get(),
            trusted_connection=self.trusted_connection_var.get(),
            encrypt=self.encrypt_var.get(),
            trust_server_certificate=self.trust_server_certificate_var.get(),
        )

    def _settings_env_values(self) -> dict[str, str]:
        return {
            "SQLSERVER_CONNECTION_STRING": self.connection_string_var.get().strip(),
            "SQLSERVER_DRIVER": self.driver_var.get().strip(),
            "SQLSERVER_SERVER": self.server_var.get().strip(),
            "SQLSERVER_DATABASE": self.database_var.get().strip(),
            "SQLSERVER_USERNAME": self.username_var.get().strip(),
            "SQLSERVER_PASSWORD": self.password_var.get(),
            "SQLSERVER_TRUSTED_CONNECTION": bool_to_env(
                self.trusted_connection_var.get()
            ),
            "SQLSERVER_NO_ENCRYPT": bool_to_env(not self.encrypt_var.get()),
            "SQLSERVER_TRUST_SERVER_CERTIFICATE": bool_to_env(
                self.trust_server_certificate_var.get()
            ),
            "TRANSLATOR_PROVIDER": self.provider_var.get(),
            "LIBRETRANSLATE_URL": self.libretranslate_url_var.get().strip(),
            "LIBRETRANSLATE_API_KEY": self.libretranslate_api_key_var.get().strip(),
            "REQUEST_TIMEOUT_SECONDS": self.request_timeout_var.get().strip(),
            "REQUEST_DELAY_SECONDS": self.request_delay_var.get().strip(),
            "RETRY_ATTEMPTS": self.retries_var.get().strip(),
            "RETRY_INITIAL_DELAY_SECONDS": self.retry_delay_var.get().strip(),
            "RETRY_BACKOFF_FACTOR": self.retry_backoff_var.get().strip(),
            "BATCH_SIZE": self.batch_size_var.get().strip(),
            "PROGRESS_EVERY": self.progress_every_var.get().strip(),
            "LOG_DIR": self.log_dir_var.get().strip(),
            "RESX_RESOURCE_DIR": self.resx_resource_dir_var.get().strip(),
            "RESX_SOURCE_LANGUAGE_ID": str(
                language_id_from_label(self.resx_source_language_var.get())
            ),
            "RESX_TARGET_LANGUAGE_ID": str(
                language_id_from_label(self.resx_target_language_var.get())
            ),
            "RESX_INCLUDE_RESOURCES": bool_to_env(self.resx_resources_file_var.get()),
            "RESX_INCLUDE_MESSAGES": bool_to_env(self.resx_messages_file_var.get()),
            "RESX_INCLUDE_MESSAGE": bool_to_env(self.resx_message_file_var.get()),
            "RESX_EXTRA_FILES": self.resx_extra_files_var.get().strip(),
            "RESX_DRY_RUN": bool_to_env(self.resx_dry_run_var.get()),
        }

    def _poll_events(self) -> None:
        while True:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event_type == "log":
                self._append_log(str(payload))
            elif event_type == "progress":
                self._apply_progress(payload)
            elif event_type == "resx-progress":
                self._apply_resx_progress(payload)
            elif event_type == "log-file":
                self.log_file_var.set(str(payload))
                self.resx_log_file_var.set(str(payload))
            elif event_type == "job-done":
                job_name, summary, log_file = payload
                if str(job_name).startswith("resx-"):
                    self._handle_resx_job_done(str(job_name), summary, str(log_file))
                else:
                    self._handle_job_done(str(job_name), summary, str(log_file))
            elif event_type == "job-error":
                job_name, message, details, log_file = payload
                if str(job_name).startswith("resx-"):
                    self._handle_resx_job_error(
                        str(job_name),
                        str(message),
                        str(details),
                        str(log_file),
                    )
                else:
                    self._handle_job_error(
                        str(job_name),
                        str(message),
                        str(details),
                        str(log_file),
                    )
            elif event_type == "message":
                title, message = payload
                self._append_log(str(message))
                messagebox.showinfo(str(title), str(message))
            elif event_type == "error-message":
                title, message = payload
                self._append_log(str(message))
                messagebox.showerror(str(title), str(message))

        self.root.after(200, self._poll_events)

    def _apply_progress(self, snapshot: object) -> None:
        if not isinstance(snapshot, ProgressSnapshot):
            return

        self.status_var.set(phase_label(snapshot.phase))
        self.current_table_var.set(snapshot.current_table or "-")
        self.overall_progress["value"] = snapshot.percent
        self.table_progress["value"] = snapshot.table_percent
        self.overall_percent_var.set(f"{snapshot.percent:.2f}%")
        self.table_percent_var.set(f"{snapshot.table_percent:.2f}%")
        self.discovered_tables_var.set(str(snapshot.discovered_tables))
        self.eligible_tables_var.set(str(snapshot.eligible_tables))
        self.skipped_tables_var.set(str(snapshot.skipped_tables))
        self.total_rows_var.set(str(snapshot.pending_rows))
        self.processed_rows_var.set(str(snapshot.processed_rows))
        self.remaining_rows_var.set(str(snapshot.remaining_rows))
        self.inserted_rows_var.set(str(snapshot.inserted_rows))
        self.skipped_existing_rows_var.set(str(snapshot.skipped_existing_rows))
        self.failed_rows_var.set(str(snapshot.failed_rows))

    def _apply_resx_progress(self, snapshot: object) -> None:
        if not isinstance(snapshot, ResxProgressSnapshot):
            return

        self.resx_status_var.set(resx_phase_label(snapshot.phase))
        self.resx_current_file_var.set(snapshot.current_file or "-")
        self.resx_current_key_var.set(snapshot.current_key or "-")
        self.resx_overall_progress["value"] = snapshot.percent
        self.resx_file_progress["value"] = snapshot.file_percent
        self.resx_overall_percent_var.set(f"{snapshot.percent:.2f}%")
        self.resx_file_percent_var.set(f"{snapshot.file_percent:.2f}%")
        self.resx_discovered_files_var.set(str(snapshot.discovered_files))
        self.resx_eligible_files_var.set(str(snapshot.eligible_files))
        self.resx_skipped_files_var.set(str(snapshot.skipped_files))
        self.resx_total_entries_var.set(str(snapshot.total_entries))
        self.resx_pending_entries_var.set(str(snapshot.pending_entries))
        self.resx_processed_entries_var.set(str(snapshot.processed_entries))
        self.resx_remaining_entries_var.set(str(snapshot.remaining_entries))
        self.resx_translated_entries_var.set(str(snapshot.translated_entries))
        self.resx_skipped_existing_entries_var.set(
            str(snapshot.skipped_existing_entries)
        )
        self.resx_skipped_empty_entries_var.set(str(snapshot.skipped_empty_entries))
        self.resx_failed_entries_var.set(str(snapshot.failed_entries))
        self.resx_created_files_var.set(str(snapshot.created_files))
        self.resx_updated_files_var.set(str(snapshot.updated_files))

    def _handle_resx_job_done(
        self,
        job_name: str,
        summary: object,
        log_file: str,
    ) -> None:
        self._set_running(False)
        self.resx_status_var.set("Finished")
        self.resx_log_file_var.set(log_file)
        self._append_log(f"Job finished: {job_name}")

        if self.cancel_event.is_set():
            self._append_log("Job ended after a stop request.")
            return

        translated = getattr(summary, "translated_entries", 0)
        skipped_existing = getattr(summary, "skipped_existing_entries", 0)
        failed = getattr(summary, "failed_entries", 0)
        created = getattr(summary, "created_files", 0)
        updated = getattr(summary, "updated_files", 0)
        messagebox.showinfo(
            "RESX Finished",
            "RESX operation finished.\n"
            f"Translated: {translated}\n"
            f"Existing skipped: {skipped_existing}\n"
            f"Failed: {failed}\n"
            f"Created files: {created}\n"
            f"Updated files: {updated}",
        )

    def _handle_job_done(
        self,
        job_name: str,
        summary: object,
        log_file: str,
    ) -> None:
        self._set_running(False)
        self.status_var.set("Finished")
        self.log_file_var.set(log_file)
        self._append_log(f"Job finished: {job_name}")

        if self.cancel_event.is_set():
            self._append_log("Job ended after a stop request.")
            return

        if job_name != "test-then-prompt" or self.followup_config is None:
            return

        inserted_rows = getattr(summary, "inserted_rows", 0)
        failed_rows = getattr(summary, "failed_rows", 0)
        skipped_tables = getattr(summary, "skipped_tables", 0)
        should_continue = messagebox.askyesno(
            "Continue Operation",
            "Test table finished.\n"
            f"Inserted: {inserted_rows}\n"
            f"Failed: {failed_rows}\n"
            f"Skipped tables: {skipped_tables}\n\n"
            "Continue with all tables?",
        )
        if should_continue:
            config = self.followup_config
            self.followup_config = None
            self._start_job("all-tables", config)

    def _handle_job_error(
        self,
        job_name: str,
        message: str,
        details: str,
        log_file: str,
    ) -> None:
        self._set_running(False)
        self.status_var.set("Error")
        self.log_file_var.set(log_file)
        self._append_log(f"Job failed: {job_name}: {message}")
        self._append_log(details)
        messagebox.showerror("Job Error", message)

    def _handle_resx_job_error(
        self,
        job_name: str,
        message: str,
        details: str,
        log_file: str,
    ) -> None:
        self._set_running(False)
        self.resx_status_var.set("Error")
        self.resx_log_file_var.set(log_file)
        self._append_log(f"Job failed: {job_name}: {message}")
        self._append_log(details)
        messagebox.showerror("RESX Job Error", message)

    def _reset_progress(self) -> None:
        self.status_var.set("Starting...")
        self.current_table_var.set("-")
        self.overall_progress["value"] = 0
        self.table_progress["value"] = 0
        self.overall_percent_var.set("0.00%")
        self.table_percent_var.set("0.00%")
        self.discovered_tables_var.set("0")
        self.eligible_tables_var.set("0")
        self.skipped_tables_var.set("0")
        self.total_rows_var.set("0")
        self.processed_rows_var.set("0")
        self.remaining_rows_var.set("0")
        self.inserted_rows_var.set("0")
        self.skipped_existing_rows_var.set("0")
        self.failed_rows_var.set("0")

    def _reset_resx_progress(self) -> None:
        self.resx_status_var.set("Starting...")
        self.resx_current_file_var.set("-")
        self.resx_current_key_var.set("-")
        self.resx_overall_progress["value"] = 0
        self.resx_file_progress["value"] = 0
        self.resx_overall_percent_var.set("0.00%")
        self.resx_file_percent_var.set("0.00%")
        self.resx_discovered_files_var.set("0")
        self.resx_eligible_files_var.set("0")
        self.resx_skipped_files_var.set("0")
        self.resx_total_entries_var.set("0")
        self.resx_pending_entries_var.set("0")
        self.resx_processed_entries_var.set("0")
        self.resx_remaining_entries_var.set("0")
        self.resx_translated_entries_var.set("0")
        self.resx_skipped_existing_entries_var.set("0")
        self.resx_skipped_empty_entries_var.set("0")
        self.resx_failed_entries_var.set("0")
        self.resx_created_files_var.set("0")
        self.resx_updated_files_var.set("0")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button_name in (
            "run_test_button",
            "run_table_button",
            "run_all_button",
            "resx_scan_button",
            "resx_run_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state=state)

        stop_state = "normal" if running else "disabled"
        for button_name in ("stop_button", "resx_stop_button"):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state=stop_state)

    def _is_worker_running(self) -> bool:
        return bool(self.worker_thread and self.worker_thread.is_alive())

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log_view(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


def add_entry(
    parent: ttk.Frame,
    row: int,
    label: str,
    variable: StringVar,
    column_offset: int = 0,
    show: str | None = None,
) -> None:
    ttk.Label(parent, text=label, style="Field.TLabel").grid(
        row=row,
        column=column_offset,
        sticky="w",
        padx=(0, 8),
        pady=4,
    )
    ttk.Entry(parent, textvariable=variable, show=show or "").grid(
        row=row,
        column=column_offset + 1,
        sticky="ew",
        pady=4,
    )


def add_combo(
    parent: ttk.Frame,
    row: int,
    label: str,
    variable: StringVar,
    values: list[str],
    column_offset: int,
) -> None:
    ttk.Label(parent, text=label, style="Field.TLabel").grid(
        row=row,
        column=column_offset,
        sticky="w",
        padx=(0, 8),
        pady=4,
    )
    ttk.Combobox(
        parent,
        textvariable=variable,
        values=values,
        state="readonly",
    ).grid(row=row, column=column_offset + 1, sticky="ew", pady=4)


def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_values(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# Generated by Translator GUI",
        *[f"{key}={escape_env_value(value)}" for key, value in values.items()],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def escape_env_value(value: str) -> str:
    if not value:
        return ""
    if any(char.isspace() for char in value) or "#" in value or ";" in value:
        return json.dumps(value, ensure_ascii=False)
    return value


def parse_bool(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def bool_to_env(value: bool) -> str:
    return "true" if value else "false"


def parse_int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def parse_float(value: str, name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc


def parse_language_id(value: str | None, default: int) -> int:
    try:
        language_id = int(str(value or "").strip())
        if language_id in LANGUAGES:
            return language_id
    except ValueError:
        pass
    return default


def normalize_sql_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip("[]").strip()
    return normalized or None


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

    raise ValueError("Invalid table name format. Use TableName or SchemaName.TableName.")


def normalize_url(value: str) -> str | None:
    normalized = value.strip().rstrip("/")
    return normalized or None


def language_label(language_id: int) -> str:
    language = get_language(language_id)
    name = GUI_LANGUAGE_NAMES.get(language.id, language.code.upper())
    return f"{language.id} - {name} ({language.code})"


def language_id_from_label(label: str) -> int:
    raw_id = label.split("-", 1)[0].strip()
    return get_language(int(raw_id)).id


def phase_label(phase: str) -> str:
    labels = {
        "discovered": "Discovering tables",
        "prepare": "Preparing tables",
        "prepared": "Ready",
        "running": "Running",
        "table-finished": "Table finished",
        "finished": "Finished",
    }
    return labels.get(phase, phase)


def resx_phase_label(phase: str) -> str:
    labels = {
        "discovered": "Discovering files",
        "prepare": "Preparing files",
        "prepared": "Ready",
        "running": "Running",
        "file-finished": "File finished",
        "finished": "Finished",
    }
    return labels.get(phase, phase)


def http_json(
    url: str,
    *,
    timeout: float,
    method: str = "GET",
    payload: dict[str, str] | None = None,
) -> object:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {message}") from exc


def main() -> int:
    try:
        root = Tk()
    except TclError as exc:
        print(f"Unable to start the GUI: {exc}")
        return 1
    TranslatorGuiApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
