from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import (
    BooleanVar,
    Canvas,
    Menu,
    PhotoImage,
    StringVar,
    TclError,
    Tk,
    Toplevel,
    filedialog,
)
from tkinter import scrolledtext
from tkinter import ttk

from translator_app import __version__
from translator_app.cleanup_service import (
    CleanupConfig,
    CleanupProgressSnapshot,
    DatabaseCleanupService,
)
from translator_app.config import (
    RetrySettings,
    RuntimeConfig,
    SqlServerConnectionSettings,
)
from translator_app.database_targets import (
    BOTH_DATABASE_TARGET,
    GUEREH_DATABASE_TARGET,
    RUGSTRUST_DATABASE_TARGET,
    RUGSTRUST_CERTIFICATION_TABLE,
    RUGSTRUST_DEFAULT_SCHEMA,
    normalize_database_target,
)
from translator_app.languages import LANGUAGES, get_language, rtl_display_text
from translator_app.logging_config import configure_logging
from translator_app.models import build_table_translation_plan
from translator_app.resx_service import (
    ResxProgressSnapshot,
    ResxTranslationConfig,
    ResxTranslationService,
)
from translator_app.retry import run_with_retry
from translator_app.runtime_paths import application_dir
from translator_app.service import (
    DatabaseTranslationService,
    ProgressSnapshot,
    TranslationSummary,
)
from translator_app.sqlserver import (
    REQUIRED_ODBC_DRIVER,
    SqlServerLocalizationRepository,
    SqlServerSchemaReader,
    connect,
    installed_odbc_drivers,
    is_odbc_driver_available,
)
from translator_app.translators import create_translator
from translator_app.translators.base import Translator
from translator_app.unfinished_report import format_unfinished_report


ENV_PATH = application_dir() / ".env"
APP_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "app_logo.png"
BG_COLOR = "#f6f8fb"
CARD_COLOR = "#ffffff"
TEXT_COLOR = "#182230"
MUTED_TEXT_COLOR = "#667085"
PRIMARY_COLOR = "#2563eb"
DANGER_COLOR = "#dc2626"

APP_AUTHOR = "Sadeq Kiumarsi"
APP_COPYRIGHT_YEAR = 2026
PROJECT_REPOSITORY_URL = "https://github.com/mehmed18q/Translator"
GITHUB_ICON_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAACXBIWXMAAAABAAAAAQBPJcTW"
    "AAAAAXNSR0IB2cksfwAAACBjSFJNAAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3Cc"
    "ulE8AAAABGdBTUEAALGPC/xhBQAAAgpJREFUeJx9lL1LHVEQxee9tyLBJCLaaCUiRogYkJjK"
    "RrAUP1DwL9AIgYB/QrBRrC3yUQRSaCobwUL0BUEUtFCwEUUjWiTEhKSw0Og+z3DP6Oy6OvDj"
    "7tt757y5587dnDwcOZLn7xKIOWZG9IBQAVw6kXTeVZZwlmCeApecfwHq+SdHYBtcpNbeK1jgP"
    "2uMgVHQlFqzDz6CKYr5nIRgnhOVYB508L1tW1hlI5gEA6Ab/PKikVtopS+CdnAAasGjVIXn4B"
    "i8AkXQRgtUo2SCdgDvKKbig+APqzhgBQ1gick74DmrHTONiJMq9gS84fZ0+2USDmFa7kYL53X"
    "tMJgAP1Urct6pZ9VM2JVwmsJEs8Os0eo2wUtQATrBrFZpFWo8cxUU6ZXO/09VZ3+wSEGNZpv0"
    "p1zunh9zNG992K15mpXrBU/ccy+oAafcgW8brVz97uf7nM+N5NafNY5/OX4DfeBQkh5qH34Gd"
    "RK8112scj42QX2prbEgoRV6wCewB16DD0zQE33PZ7uaK2CLVsS2ZdvSiIRWGZfQj3ry686KZQ"
    "lNnJfbD8RbV/2NhzGf1YsuCSc4B75K8PIH1/0GZ6CKOUMS2itixYlDsS0Uue0vYAa0OsGY1W1"
    "IuAQbXiwt6EW1sfWu1ku4txb/aMU+bUqIZQmaaIHVfM+Y3+OY1aP3frHt+3bnA8p3JbcmEdeM"
    "CYWOVQQ6ywAAAABJRU5ErkJggg=="
)
README_PATH = Path(__file__).resolve().parent.parent / "README.md"


@dataclass(frozen=True)
class TableSelectionItem:
    """One table shown in the all-table review dialog."""

    display_name: str
    text_characters: int = 0
    pending_rows: int = 0
    eligible: bool = True
    reason: str | None = None


class ScrollableFrame(ttk.Frame):
    _active_frame: ScrollableFrame | None = None
    _global_bindings_installed = False

    def __init__(self, parent: ttk.Notebook, *, padding: int = 14) -> None:
        super().__init__(parent, style="App.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = Canvas(
            self,
            background=BG_COLOR,
            borderwidth=0,
            highlightthickness=0,
        )
        self.vertical_scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.horizontal_scrollbar = ttk.Scrollbar(
            self,
            orient="horizontal",
            command=self.canvas.xview,
        )
        self.canvas.configure(
            yscrollcommand=self.vertical_scrollbar.set,
            xscrollcommand=self.horizontal_scrollbar.set,
        )

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        self.horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.content = ttk.Frame(self.canvas, padding=padding, style="App.TFrame")
        self.content_window = self.canvas.create_window(
            (0, 0),
            window=self.content,
            anchor="nw",
        )

        self.bind("<Enter>", self._activate)
        self.bind("<Leave>", self._deactivate_if_outside)
        self.canvas.bind("<Enter>", self._activate)
        self.canvas.bind("<Leave>", self._deactivate_if_outside)
        self.content.bind("<Enter>", self._activate)
        self.content.bind("<Leave>", self._deactivate_if_outside)
        self.canvas.bind("<Configure>", self._update_scroll_region)
        self.content.bind("<Configure>", self._update_scroll_region)
        self._install_global_mousewheel_bindings()

    def _activate(self, _event: object) -> None:
        ScrollableFrame._active_frame = self

    @classmethod
    def clear_active(cls, _event: object | None = None) -> None:
        cls._active_frame = None

    def _deactivate_if_outside(self, _event: object) -> None:
        pointer_x = self.winfo_pointerx()
        pointer_y = self.winfo_pointery()
        left = self.winfo_rootx()
        top = self.winfo_rooty()
        right = left + self.winfo_width()
        bottom = top + self.winfo_height()
        if left <= pointer_x < right and top <= pointer_y < bottom:
            return

        if ScrollableFrame._active_frame is self:
            ScrollableFrame.clear_active()

    def _update_scroll_region(self, _event: object | None = None) -> None:
        requested_width = self.content.winfo_reqwidth()
        requested_height = self.content.winfo_reqheight()
        canvas_width = max(self.canvas.winfo_width(), 1)
        canvas_height = max(self.canvas.winfo_height(), 1)
        needs_vertical_scroll = requested_height > canvas_height
        needs_horizontal_scroll = requested_width > canvas_width

        if needs_vertical_scroll:
            self.vertical_scrollbar.grid()
        else:
            self.vertical_scrollbar.grid_remove()
        if needs_horizontal_scroll:
            self.horizontal_scrollbar.grid()
        else:
            self.horizontal_scrollbar.grid_remove()

        content_width = max(requested_width, canvas_width)
        content_height = max(requested_height, canvas_height)
        self.canvas.itemconfigure(
            self.content_window,
            width=content_width,
            height=content_height,
        )
        self.canvas.configure(scrollregion=(0, 0, content_width, content_height))

    def _install_global_mousewheel_bindings(self) -> None:
        if ScrollableFrame._global_bindings_installed:
            return

        ScrollableFrame._global_bindings_installed = True
        self.bind_all("<MouseWheel>", self._on_global_mousewheel)
        self.bind_all("<Shift-MouseWheel>", self._on_global_shift_mousewheel)
        self.bind_all("<Button-4>", self._on_global_mousewheel)
        self.bind_all("<Button-5>", self._on_global_mousewheel)
        self.bind_all("<Shift-Button-4>", self._on_global_shift_mousewheel)
        self.bind_all("<Shift-Button-5>", self._on_global_shift_mousewheel)

    def _on_global_mousewheel(self, event: object) -> str | None:
        active_frame = ScrollableFrame._active_frame
        if active_frame is None:
            return None

        if getattr(event, "state", 0) & 0x0001:
            scrolled = active_frame._scroll_horizontal(event)
        else:
            scrolled = active_frame._scroll_vertical(event)
        return "break" if scrolled else None

    def _on_global_shift_mousewheel(self, event: object) -> str | None:
        active_frame = ScrollableFrame._active_frame
        if active_frame is None:
            return None

        scrolled = active_frame._scroll_horizontal(event)
        return "break" if scrolled else None

    def _scroll_vertical(self, event: object) -> bool:
        units = mousewheel_units(event)
        if not can_scroll(self.canvas.yview(), units):
            return False
        self.canvas.yview_scroll(units, "units")
        return True

    def _scroll_horizontal(self, event: object) -> bool:
        units = mousewheel_units(event)
        if not can_scroll(self.canvas.xview(), units):
            return False
        self.canvas.xview_scroll(units, "units")
        return True


class MultiLanguageDropdown(ttk.Frame):
    """A compact dropdown containing checkboxes for destination languages."""

    def __init__(
        self,
        parent: ttk.Frame,
        *,
        languages: dict[int, object],
        selected_ids: tuple[int, ...] = (),
        command: object | None = None,
    ) -> None:
        super().__init__(parent, style="Card.TFrame")
        self._languages = languages
        self._command = command
        self._variables: dict[int, BooleanVar] = {}
        self._labels: dict[int, str] = {}
        self._selection_text = StringVar()
        self.menu = Menu(self, tearoff=False)
        for language_id, language in languages.items():
            self._variables[language_id] = BooleanVar(
                self,
                value=language_id in selected_ids,
            )
            label = language_label(language_id)
            self._labels[language_id] = label
            self.menu.add_checkbutton(
                label=label,
                variable=self._variables[language_id],
                command=self._changed,
            )
        self.button = ttk.Menubutton(
            self,
            textvariable=self._selection_text,
            direction="below",
        )
        self.button.configure(menu=self.menu)
        self.button.grid(row=0, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)
        self._refresh_text()

    def selected_ids(self) -> tuple[int, ...]:
        return tuple(
            language_id
            for language_id in self._languages
            if self._variables[language_id].get()
        )

    def set_selected_ids(self, language_ids: tuple[int, ...] | list[int]) -> None:
        selected = set(language_ids)
        for language_id, variable in self._variables.items():
            variable.set(language_id in selected)
        self._refresh_text()

    def _changed(self) -> None:
        self._refresh_text()
        if callable(self._command):
            self._command()

    def _refresh_text(self) -> None:
        selected = self.selected_ids()
        if not selected:
            self._selection_text.set("Select target languages")
            return
        titles = [self._labels[language_id] for language_id in selected]
        if len(titles) <= 2:
            self._selection_text.set(", ".join(titles))
        else:
            self._selection_text.set(f"{len(titles)} languages selected")

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
        self.root.title(f"SQL Server Localize Translator v{__version__}")
        self.logo_image: PhotoImage | None = None
        self.github_icon_image: PhotoImage | None = None
        self._set_window_icon()
        self._set_github_icon()
        self.root.geometry("1120x780")
        self.root.minsize(720, 460)
        self.root.configure(background=BG_COLOR)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.followup_config: RuntimeConfig | None = None
        self.running_job_name = ""
        self.operation_started_monotonic: float | None = None
        self.resx_started_monotonic: float | None = None
        self.cleanup_started_monotonic: float | None = None
        self.operation_paused_started_monotonic: float | None = None
        self.resx_paused_started_monotonic: float | None = None
        self.cleanup_paused_started_monotonic: float | None = None
        self.operation_paused_total_seconds = 0.0
        self.resx_paused_total_seconds = 0.0
        self.cleanup_paused_total_seconds = 0.0
        self.operation_last_processed_rows = 0
        self.operation_last_remaining_rows = 0
        self.resx_last_processed_entries = 0
        self.resx_last_remaining_entries = 0
        self.cleanup_last_processed_rows = 0
        self.cleanup_last_remaining_rows = 0

        self.env_values = read_env_values(ENV_PATH)
        self._configure_style()
        self._build_variables()
        self._build_layout()
        maximize_window(self.root)
        self._poll_events()
        self.root.after(350, self._warn_if_odbc_driver_missing)

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
        style.configure(
            "Pause.TButton",
            padding=(14, 8),
            background="#f59e0b",
            foreground="#ffffff",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map(
            "Pause.TButton",
            background=[("active", "#d97706"), ("disabled", "#fcd34d")],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure(
            "Resume.TButton",
            padding=(14, 8),
            background="#16a34a",
            foreground="#ffffff",
            font=("TkDefaultFont", 10, "bold"),
        )
        style.map(
            "Resume.TButton",
            background=[("active", "#15803d"), ("disabled", "#86efac")],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure("Horizontal.TProgressbar", thickness=12)
        style.configure(
            "FooterLink.TButton",
            background=BG_COLOR,
            foreground=MUTED_TEXT_COLOR,
            borderwidth=0,
            relief="flat",
            padding=(4, 2),
            font=("TkDefaultFont", 9),
        )
        style.map(
            "FooterLink.TButton",
            background=[("active", BG_COLOR)],
            foreground=[("active", PRIMARY_COLOR)],
        )

    def _set_window_icon(self) -> None:
        if not APP_LOGO_PATH.exists():
            return

        try:
            self.logo_image = PhotoImage(file=str(APP_LOGO_PATH))
            self.root.iconphoto(True, self.logo_image)
        except TclError:
            self.logo_image = None

    def _set_github_icon(self) -> None:
        try:
            self.github_icon_image = PhotoImage(
                master=self.root,
                data=GITHUB_ICON_PNG_BASE64,
            )
        except TclError:
            self.github_icon_image = None

    def _build_variables(self) -> None:
        env = self.env_values

        self.driver_var = StringVar(
            value=env.get("GUEREH_SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
        )
        self.server_var = StringVar(value=env.get("GUEREH_SQLSERVER_SERVER", ""))
        self.database_var = StringVar(value=env.get("GUEREH_SQLSERVER_DATABASE", ""))
        self.username_var = StringVar(value=env.get("GUEREH_SQLSERVER_USERNAME", ""))
        self.password_var = StringVar(value=env.get("GUEREH_SQLSERVER_PASSWORD", ""))
        self.trusted_connection_var = BooleanVar(
            value=parse_bool(env.get("GUEREH_SQLSERVER_TRUSTED_CONNECTION", "false"))
        )
        self.encrypt_var = BooleanVar(
            value=not parse_bool(env.get("GUEREH_SQLSERVER_NO_ENCRYPT", "false"))
        )
        self.trust_server_certificate_var = BooleanVar(
            value=parse_bool(env.get("GUEREH_SQLSERVER_TRUST_SERVER_CERTIFICATE", "true"))
        )
        self.rugstrust_driver_var = StringVar(
            value=env.get("RUGSTRUST_SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
        )
        self.rugstrust_server_var = StringVar(value=env.get("RUGSTRUST_SQLSERVER_SERVER", ""))
        self.rugstrust_database_var = StringVar(value=env.get("RUGSTRUST_SQLSERVER_DATABASE", ""))
        self.rugstrust_username_var = StringVar(value=env.get("RUGSTRUST_SQLSERVER_USERNAME", ""))
        self.rugstrust_password_var = StringVar(value=env.get("RUGSTRUST_SQLSERVER_PASSWORD", ""))
        self.rugstrust_trusted_connection_var = BooleanVar(
            value=parse_bool(env.get("RUGSTRUST_SQLSERVER_TRUSTED_CONNECTION", "false"))
        )
        self.rugstrust_encrypt_var = BooleanVar(
            value=not parse_bool(env.get("RUGSTRUST_SQLSERVER_NO_ENCRYPT", "false"))
        )
        self.rugstrust_trust_server_certificate_var = BooleanVar(
            value=parse_bool(env.get("RUGSTRUST_SQLSERVER_TRUST_SERVER_CERTIFICATE", "true"))
        )
        self.rugstrust_schema_var = StringVar(
            value=env.get("RUGSTRUST_SQLSERVER_SCHEMA", RUGSTRUST_DEFAULT_SCHEMA)
        )
        self.database_target_var = StringVar(
            value=database_target_label(
                normalize_database_target(
                    env.get("DATABASE_TARGET", GUEREH_DATABASE_TARGET),
                    rugstrust_available=bool(env.get("RUGSTRUST_SQLSERVER_SERVER") and env.get("RUGSTRUST_SQLSERVER_DATABASE")),
                )
            )
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
        target_ids = parse_language_ids(
            env.get("TARGET_LANGUAGE_IDS") or env.get("TARGET_LANGUAGE_ID"),
            default=(2,),
        )
        self.target_language_var = StringVar(value=language_label(target_ids[0]))
        self.target_language_ids = target_ids
        self.operation_schema_var = StringVar(value="")
        self.operation_table_var = StringVar(value="")
        self.test_table_var = StringVar(value="")
        self.dry_run_var = BooleanVar(value=True)

        cleanup_ids = parse_language_ids(
            env.get("CLEANUP_LANGUAGE_IDS"),
            default=target_ids,
        )
        self.cleanup_language_var = StringVar(value=language_label(cleanup_ids[0]))
        self.cleanup_language_ids = cleanup_ids
        self.cleanup_schema_var = StringVar(value="")
        self.cleanup_table_var = StringVar(value="")
        self.cleanup_dry_run_var = BooleanVar(
            value=parse_bool(env.get("CLEANUP_DRY_RUN", "true"))
        )

        self.resx_resource_dir_var = StringVar(
            value=env.get("RESX_RESOURCE_DIR", "")
        )
        self.resx_source_language_var = StringVar(
            value=language_label(parse_language_id(env.get("RESX_SOURCE_LANGUAGE_ID"), 1))
        )
        resx_target_ids = parse_language_ids(
            env.get("RESX_TARGET_LANGUAGE_IDS")
            or env.get("RESX_TARGET_LANGUAGE_ID"),
            default=(2,),
        )
        self.resx_target_language_var = StringVar(value=language_label(resx_target_ids[0]))
        self.resx_target_language_ids = resx_target_ids
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
        self.updated_rows_var = StringVar(value="0")
        self.skipped_existing_rows_var = StringVar(value="0")
        self.failed_rows_var = StringVar(value="0")
        self.operation_started_var = StringVar(value="-")
        self.operation_finished_var = StringVar(value="-")
        self.operation_duration_var = StringVar(value="-")
        self.operation_average_rate_var = StringVar(value="-")
        self.operation_estimated_finish_var = StringVar(value="-")
        self.target_language_progress_var = StringVar(value="Languages: 0/0 completed · 0 remaining")

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
        self.resx_started_var = StringVar(value="-")
        self.resx_finished_var = StringVar(value="-")
        self.resx_duration_var = StringVar(value="-")
        self.resx_average_rate_var = StringVar(value="-")
        self.resx_estimated_finish_var = StringVar(value="-")
        self.resx_target_language_progress_var = StringVar(value="Languages: 0/0 completed · 0 remaining")

        self.cleanup_status_var = StringVar(value="Ready")
        self.cleanup_log_file_var = StringVar(value="-")
        self.cleanup_current_table_var = StringVar(value="-")
        self.cleanup_overall_percent_var = StringVar(value="0.00%")
        self.cleanup_table_percent_var = StringVar(value="0.00%")
        self.cleanup_discovered_tables_var = StringVar(value="0")
        self.cleanup_eligible_tables_var = StringVar(value="0")
        self.cleanup_skipped_tables_var = StringVar(value="0")
        self.cleanup_matched_rows_var = StringVar(value="0")
        self.cleanup_processed_rows_var = StringVar(value="0")
        self.cleanup_remaining_rows_var = StringVar(value="0")
        self.cleanup_deleted_rows_var = StringVar(value="0")
        self.cleanup_failed_rows_var = StringVar(value="0")
        self.cleanup_started_var = StringVar(value="-")
        self.cleanup_finished_var = StringVar(value="-")
        self.cleanup_duration_var = StringVar(value="-")
        self.cleanup_average_rate_var = StringVar(value="-")
        self.cleanup_estimated_finish_var = StringVar(value="-")
        self.cleanup_language_progress_var = StringVar(
            value="Languages: 0/0 completed · 0 remaining"
        )

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

        self.settings_scroll = ScrollableFrame(notebook)
        self.operation_scroll = ScrollableFrame(notebook)
        self.resources_scroll = ScrollableFrame(notebook)
        self.cleanup_scroll = ScrollableFrame(notebook)
        self.readme_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")
        self.settings_tab = self.settings_scroll.content
        self.operation_tab = self.operation_scroll.content
        self.resources_tab = self.resources_scroll.content
        self.cleanup_tab = self.cleanup_scroll.content
        self.logs_tab = ttk.Frame(notebook, padding=14, style="App.TFrame")

        notebook.add(self.settings_scroll, text="Connection")
        notebook.add(self.operation_scroll, text="Operation")
        notebook.add(self.resources_scroll, text="Resources")
        notebook.add(self.cleanup_scroll, text="Cleanup")
        notebook.add(self.logs_tab, text="Logs")
        notebook.add(self.readme_tab, text="ReadMe")

        self._build_settings_tab()
        self._build_operation_tab()
        self._build_resources_tab()
        self._build_logs_tab()
        self._build_cleanup_tab()
        self._build_readme_tab()

        footer = ttk.Frame(shell, style="App.TFrame")
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Separator(footer, orient="horizontal").grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 8),
        )
        footer_content = ttk.Frame(footer, style="App.TFrame")
        footer_content.grid(row=1, column=0)
        ttk.Label(
            footer_content,
            text=(
                f"Created by {APP_AUTHOR}  |  {APP_COPYRIGHT_YEAR}  |  "
                f"Version {__version__}"
            ),
            style="Muted.TLabel",
            anchor="center",
        ).grid(row=0, column=0)
        ttk.Label(
            footer_content,
            text="  |  ",
            style="Muted.TLabel",
        ).grid(row=0, column=1)

        github_button_options: dict[str, object] = {}
        if self.github_icon_image is not None:
            github_button_options.update(
                image=self.github_icon_image,
                compound="left",
            )
        self.github_button = ttk.Button(
            footer_content,
            text="GitHub",
            command=self._open_project_repository,
            style="FooterLink.TButton",
            cursor="hand2",
            **github_button_options,
        )
        self.github_button.grid(row=0, column=2)

    def _open_project_repository(self) -> None:
        try:
            opened = open_project_repository()
        except Exception as exc:
            self._show_warning(
                "GitHub",
                f"Could not open the project repository.\n\n"
                f"{PROJECT_REPOSITORY_URL}\n\nDetails: {exc}",
            )
            return

        if not opened:
            self._show_warning(
                "GitHub",
                f"Could not open the project repository.\n\n"
                f"Open this address manually:\n{PROJECT_REPOSITORY_URL}",
            )

    def _warn_if_odbc_driver_missing(self) -> None:
        try:
            drivers = installed_odbc_drivers()
        except RuntimeError as exc:
            self._show_warning("ODBC Driver", str(exc))
            return

        if is_odbc_driver_available(drivers):
            return

        detected = ", ".join(drivers) if drivers else "None"
        self._show_warning(
            "ODBC Driver Required",
            (
                f"{REQUIRED_ODBC_DRIVER} is not installed.\n\n"
                "Install it before connecting to SQL Server, then restart this "
                "application.\n\n"
                "Install command:\n"
                "winget install Microsoft.msodbcsql.18\n\n"
                f"Detected ODBC drivers: {detected}"
            ),
        )

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

        ttk.Label(db_frame, text="GUEREH database", style="Field.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
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
        ttk.Separator(db_frame, orient="horizontal").grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=(10, 8)
        )
        ttk.Label(db_frame, text="RUGSTRUST database", style="Field.TLabel").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        add_entry(db_frame, 11, "Driver", self.rugstrust_driver_var)
        add_entry(db_frame, 12, "Server", self.rugstrust_server_var)
        add_entry(db_frame, 13, "Database", self.rugstrust_database_var)
        add_entry(db_frame, 14, "Username", self.rugstrust_username_var)
        add_entry(db_frame, 15, "Password", self.rugstrust_password_var, show="*")
        ttk.Checkbutton(
            db_frame,
            text="Trusted Connection",
            variable=self.rugstrust_trusted_connection_var,
        ).grid(row=16, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            db_frame,
            text="Encrypt",
            variable=self.rugstrust_encrypt_var,
        ).grid(row=17, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(
            db_frame,
            text="Trust server certificate",
            variable=self.rugstrust_trust_server_certificate_var,
        ).grid(row=18, column=0, columnspan=2, sticky="w")
        add_entry(db_frame, 19, "RugsTrust schema", self.rugstrust_schema_var)

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

        ttk.Label(
            translator_frame,
            text="Provider: Google → LibreTranslate (automatic fallback)",
            style="Field.TLabel",
        ).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=4,
        )
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
            text="Test RugsTrust Database",
            command=self.test_rugstrust_database_connection,
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            button_frame,
            text="Test LibreTranslate",
            command=self.test_libretranslate,
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(
            button_frame,
            text="Save Settings",
            command=self.save_settings,
            style="Accent.TButton",
        ).grid(row=0, column=3)

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
        ttk.Label(inputs, text="Target", style="Field.TLabel").grid(
            row=0, column=2, sticky="w", padx=(12, 8), pady=4
        )
        self.target_language_selector = MultiLanguageDropdown(
            inputs,
            languages=LANGUAGES,
            selected_ids=self.target_language_ids,
            command=self._target_languages_changed,
        )
        self.target_language_selector.grid(row=0, column=3, sticky="ew", pady=4)
        add_entry(inputs, 1, "Schema filter", self.operation_schema_var, 0)
        add_entry(inputs, 1, "Only table", self.operation_table_var, 2)
        add_entry(inputs, 2, "Test table", self.test_table_var, 0)
        ttk.Checkbutton(
            inputs,
            text="Dry-run: count only, do not insert rows",
            variable=self.dry_run_var,
        ).grid(row=2, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=4)
        add_combo(
            inputs,
            3,
            "Database",
            self.database_target_var,
            [
                database_target_label(GUEREH_DATABASE_TARGET),
                database_target_label(RUGSTRUST_DATABASE_TARGET),
                database_target_label(BOTH_DATABASE_TARGET),
            ],
            0,
        )
        ttk.Label(
            inputs,
            text=f"RugsTrust table: {RUGSTRUST_DEFAULT_SCHEMA}.{RUGSTRUST_CERTIFICATION_TABLE}",
            style="Field.TLabel",
        ).grid(row=3, column=2, columnspan=2, sticky="w", padx=(12, 0), pady=4)

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

        self.pause_button = ttk.Button(
            buttons,
            text="Pause",
            command=self.toggle_pause,
            state="disabled",
            style="Pause.TButton",
        )
        self.pause_button.grid(row=0, column=3, padx=(0, 8))

        self.stop_button = ttk.Button(
            buttons,
            text="Stop",
            command=self.stop_operation,
            state="disabled",
            style="Danger.TButton",
        )
        self.stop_button.grid(row=0, column=4)

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

        self.language_progress = ttk.Progressbar(progress, maximum=100)
        self.language_progress.grid(row=7, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(progress, text="Language queue", style="Field.TLabel").grid(
            row=8, column=0, sticky="w"
        )
        ttk.Label(
            progress,
            textvariable=self.target_language_progress_var,
            style="Field.TLabel",
        ).grid(row=8, column=1, columnspan=3, sticky="w")

        metrics = ttk.Frame(progress, style="Card.TFrame")
        metrics.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        for column_index in range(4):
            metrics.columnconfigure(column_index, weight=1)

        self._add_metric(metrics, 0, 0, "Discovered tables", self.discovered_tables_var)
        self._add_metric(metrics, 0, 1, "Eligible tables", self.eligible_tables_var)
        self._add_metric(metrics, 0, 2, "Skipped tables", self.skipped_tables_var)
        self._add_metric(metrics, 0, 3, "Total pending rows", self.total_rows_var)
        self._add_metric(metrics, 1, 0, "Processed rows", self.processed_rows_var)
        self._add_metric(metrics, 1, 1, "Remaining rows", self.remaining_rows_var)
        self._add_metric(metrics, 1, 2, "Inserted rows", self.inserted_rows_var)
        self._add_metric(metrics, 1, 3, "Updated rows", self.updated_rows_var)
        self._add_metric(metrics, 2, 0, "Existing skipped", self.skipped_existing_rows_var)
        self._add_metric(metrics, 2, 1, "Failed rows", self.failed_rows_var)
        self._add_metric(metrics, 2, 2, "Started", self.operation_started_var)
        self._add_metric(metrics, 2, 3, "Finished", self.operation_finished_var)
        self._add_metric(metrics, 3, 0, "Duration", self.operation_duration_var)
        self._add_metric(metrics, 3, 1, "Avg rec/sec", self.operation_average_rate_var)
        self._add_metric(
            metrics,
            3,
            2,
            "Estimated finish",
            self.operation_estimated_finish_var,
        )

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
        ttk.Label(inputs, text="Target", style="Field.TLabel").grid(
            row=1, column=2, sticky="w", padx=(12, 8), pady=4
        )
        self.resx_target_language_selector = MultiLanguageDropdown(
            inputs,
            languages=LANGUAGES,
            selected_ids=self.resx_target_language_ids,
            command=self._resx_target_languages_changed,
        )
        self.resx_target_language_selector.grid(row=1, column=3, sticky="ew", pady=4)

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
        buttons.columnconfigure(4, weight=1)

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

        self.resx_pause_button = ttk.Button(
            buttons,
            text="Pause",
            command=self.toggle_pause,
            state="disabled",
            style="Pause.TButton",
        )
        self.resx_pause_button.grid(row=0, column=2, padx=(0, 8))

        self.resx_stop_button = ttk.Button(
            buttons,
            text="Stop",
            command=self.stop_operation,
            state="disabled",
            style="Danger.TButton",
        )
        self.resx_stop_button.grid(row=0, column=3)

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

        self.resx_language_progress = ttk.Progressbar(progress, maximum=100)
        self.resx_language_progress.grid(
            row=7, column=0, columnspan=4, sticky="ew", pady=8
        )
        ttk.Label(progress, text="Language queue", style="Field.TLabel").grid(
            row=8, column=0, sticky="w"
        )
        ttk.Label(
            progress,
            textvariable=self.resx_target_language_progress_var,
            style="Field.TLabel",
        ).grid(row=8, column=1, columnspan=3, sticky="w")

        metrics = ttk.Frame(progress, style="Card.TFrame")
        metrics.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(16, 0))
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
        self._add_metric(metrics, 3, 1, "Started", self.resx_started_var)
        self._add_metric(metrics, 3, 2, "Finished", self.resx_finished_var)
        self._add_metric(metrics, 3, 3, "Duration", self.resx_duration_var)
        self._add_metric(metrics, 4, 0, "Avg rec/sec", self.resx_average_rate_var)
        self._add_metric(
            metrics,
            4,
            1,
            "Estimated finish",
            self.resx_estimated_finish_var,
        )

    def _build_logs_tab(self) -> None:
        self.logs_tab.columnconfigure(0, weight=1)
        self.logs_tab.rowconfigure(0, weight=1)
        self.logs_tab.bind("<Enter>", ScrollableFrame.clear_active)

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
        configure_auto_hide_text_scrollbar(self.log_text)
        self.log_text.bind("<Enter>", ScrollableFrame.clear_active)
        self.log_text.grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(self.logs_tab, style="App.TFrame")
        controls.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(controls, text="Clear Log View", command=self.clear_log_view).grid(
            row=0,
            column=0,
        )

    def _build_cleanup_tab(self) -> None:
        self.cleanup_tab.columnconfigure(0, weight=1)
        self.cleanup_tab.rowconfigure(2, weight=1)

        inputs = ttk.LabelFrame(
            self.cleanup_tab,
            text="Cleanup Options",
            padding=12,
            style="Card.TLabelframe",
        )
        inputs.grid(row=0, column=0, sticky="ew")
        inputs.columnconfigure(1, weight=1)
        inputs.columnconfigure(3, weight=1)

        ttk.Label(inputs, text="Languages", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.cleanup_language_selector = MultiLanguageDropdown(
            inputs,
            languages=LANGUAGES,
            selected_ids=self.cleanup_language_ids,
            command=self._cleanup_languages_changed,
        )
        self.cleanup_language_selector.grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=4
        )
        add_entry(inputs, 1, "Schema filter", self.cleanup_schema_var, 0)
        add_entry(inputs, 1, "Only table", self.cleanup_table_var, 2)
        ttk.Checkbutton(
            inputs,
            text="Dry-run: count matching rows, do not delete",
            variable=self.cleanup_dry_run_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=4)
        ttk.Label(
            inputs,
            text=(
                "A row matches only when every textual content column is NULL, "
                "empty, or whitespace. Language and key columns are ignored."
            ),
            style="Field.TLabel",
            wraplength=940,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(5, 0))

        buttons = ttk.Frame(self.cleanup_tab, style="App.TFrame")
        buttons.grid(row=1, column=0, sticky="ew", pady=(12, 12))
        buttons.columnconfigure(4, weight=1)
        self.cleanup_table_button = ttk.Button(
            buttons,
            text="Clean Selected Table",
            command=self.start_cleanup_single_table,
            style="Accent.TButton",
        )
        self.cleanup_table_button.grid(row=0, column=0, padx=(0, 8))
        self.cleanup_all_button = ttk.Button(
            buttons,
            text="Clean All Tables",
            command=self.start_cleanup_all_tables,
        )
        self.cleanup_all_button.grid(row=0, column=1, padx=(0, 8))
        self.cleanup_pause_button = ttk.Button(
            buttons,
            text="Pause",
            command=self.toggle_pause,
            state="disabled",
            style="Pause.TButton",
        )
        self.cleanup_pause_button.grid(row=0, column=2, padx=(0, 8))
        self.cleanup_stop_button = ttk.Button(
            buttons,
            text="Stop",
            command=self.stop_operation,
            state="disabled",
            style="Danger.TButton",
        )
        self.cleanup_stop_button.grid(row=0, column=3)

        progress = ttk.LabelFrame(
            self.cleanup_tab,
            text="Progress",
            padding=12,
            style="Card.TLabelframe",
        )
        progress.grid(row=2, column=0, sticky="nsew")
        progress.columnconfigure(1, weight=1)
        progress.columnconfigure(3, weight=1)

        ttk.Label(progress, text="Status", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=4
        )
        ttk.Label(
            progress, textvariable=self.cleanup_status_var, style="Field.TLabel"
        ).grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(progress, text="Log file", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Label(
            progress, textvariable=self.cleanup_log_file_var, style="Field.TLabel"
        ).grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Label(progress, text="Current table", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Label(
            progress,
            textvariable=self.cleanup_current_table_var,
            style="Field.TLabel",
        ).grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)

        self.cleanup_overall_progress = ttk.Progressbar(progress, maximum=100)
        self.cleanup_overall_progress.grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=8
        )
        ttk.Label(progress, text="Overall", style="Field.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        ttk.Label(
            progress,
            textvariable=self.cleanup_overall_percent_var,
            style="Field.TLabel",
        ).grid(row=4, column=1, sticky="w")
        self.cleanup_table_progress = ttk.Progressbar(progress, maximum=100)
        self.cleanup_table_progress.grid(
            row=5, column=0, columnspan=4, sticky="ew", pady=8
        )
        ttk.Label(progress, text="Table", style="Field.TLabel").grid(
            row=6, column=0, sticky="w"
        )
        ttk.Label(
            progress,
            textvariable=self.cleanup_table_percent_var,
            style="Field.TLabel",
        ).grid(row=6, column=1, sticky="w")
        self.cleanup_language_progress = ttk.Progressbar(progress, maximum=100)
        self.cleanup_language_progress.grid(
            row=7, column=0, columnspan=4, sticky="ew", pady=8
        )
        ttk.Label(progress, text="Language queue", style="Field.TLabel").grid(
            row=8, column=0, sticky="w"
        )
        ttk.Label(
            progress,
            textvariable=self.cleanup_language_progress_var,
            style="Field.TLabel",
        ).grid(row=8, column=1, columnspan=3, sticky="w")

        metrics = ttk.Frame(progress, style="Card.TFrame")
        metrics.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(16, 0))
        for column_index in range(4):
            metrics.columnconfigure(column_index, weight=1)
        self._add_metric(
            metrics, 0, 0, "Discovered tables", self.cleanup_discovered_tables_var
        )
        self._add_metric(
            metrics, 0, 1, "Eligible tables", self.cleanup_eligible_tables_var
        )
        self._add_metric(
            metrics, 0, 2, "Skipped tables", self.cleanup_skipped_tables_var
        )
        self._add_metric(metrics, 0, 3, "Matched rows", self.cleanup_matched_rows_var)
        self._add_metric(
            metrics, 1, 0, "Processed rows", self.cleanup_processed_rows_var
        )
        self._add_metric(
            metrics, 1, 1, "Remaining rows", self.cleanup_remaining_rows_var
        )
        self._add_metric(metrics, 1, 2, "Deleted rows", self.cleanup_deleted_rows_var)
        self._add_metric(metrics, 1, 3, "Failed rows", self.cleanup_failed_rows_var)
        self._add_metric(metrics, 2, 0, "Started", self.cleanup_started_var)
        self._add_metric(metrics, 2, 1, "Finished", self.cleanup_finished_var)
        self._add_metric(metrics, 2, 2, "Duration", self.cleanup_duration_var)
        self._add_metric(metrics, 2, 3, "Avg rec/sec", self.cleanup_average_rate_var)
        self._add_metric(
            metrics, 3, 0, "Estimated finish", self.cleanup_estimated_finish_var
        )

    def _build_readme_tab(self) -> None:
        self.readme_tab.columnconfigure(0, weight=1)
        self.readme_tab.rowconfigure(0, weight=1)
        self.readme_tab.bind("<Enter>", ScrollableFrame.clear_active)

        readme_text = scrolledtext.ScrolledText(
            self.readme_tab,
            width=110,
            height=34,
            wrap="word",
            state="disabled",
            bg="#ffffff",
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            selectbackground="#bfdbfe",
            relief="solid",
            borderwidth=1,
            padx=12,
            pady=12,
            font=("Consolas", 10),
        )
        readme_text.grid(row=0, column=0, sticky="nsew")
        self.readme_text = readme_text
        readme_text.bind("<Enter>", ScrollableFrame.clear_active)
        configure_auto_hide_text_scrollbar(readme_text)
        try:
            readme_content = README_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            readme_content = (
                "ReadMe.md could not be loaded.\n\n"
                f"Path: {README_PATH}\n"
                f"Error: {exc}"
            )
        readme_text.configure(state="normal")
        configure_readme_markdown_tags(readme_text)
        render_markdown(readme_text, readme_content)
        readme_text.configure(state="disabled")

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
        self._show_info("Settings", f"Settings saved to {ENV_PATH.resolve()}.")

    def _show_info(self, title: str, message: str) -> None:
        show_copyable_message(self.root, title, message, "info")

    def _show_error(self, title: str, message: str) -> None:
        show_copyable_message(self.root, title, message, "error")

    def _show_warning(self, title: str, message: str) -> None:
        show_copyable_message(self.root, title, message, "warning")

    def _ask_yes_no(self, title: str, message: str) -> bool:
        return ask_copyable_yes_no(self.root, title, message)

    def test_database_connection(self) -> None:
        try:
            connection_string = self._build_connection_settings().build_connection_string()
        except Exception as exc:
            self._show_error("Settings Error", str(exc))
            return

        self._append_log("Testing database connection...")
        thread = threading.Thread(
            target=self._database_test_worker,
            args=(connection_string,),
            daemon=True,
        )
        thread.start()

    def test_rugstrust_database_connection(self) -> None:
        try:
            rugstrust_settings = self._build_rugstrust_connection_settings()
            connection_string = rugstrust_settings.build_connection_string()
        except Exception as exc:
            self._show_error(
                "RugsTrust Database",
                str(exc),
            )
            return
        self._append_log("Testing RugsTrust database connection...")
        thread = threading.Thread(
            target=self._database_test_worker,
            args=(
                connection_string,
                RUGSTRUST_CERTIFICATION_TABLE,
                self.rugstrust_schema_var.get().strip() or RUGSTRUST_DEFAULT_SCHEMA,
            ),
            daemon=True,
        )
        thread.start()

    def _database_test_worker(
        self,
        connection_string: str,
        table_name: str | None = None,
        schema_name: str | None = None,
    ) -> None:
        connection = None
        try:
            connection = connect(connection_string, autocommit=False)
            tables = SqlServerSchemaReader(connection).get_localize_tables(
                schema_name=schema_name if table_name else None,
                table_name=table_name,
            )
            self.events.put(
                (
                    "message",
                    (
                        "RugsTrust Database Connection" if table_name else "Database Connection",
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
            self._show_error("LibreTranslate", "LibreTranslate URL is required.")
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
            self._show_error("RESX Settings Error", str(exc))
            return

        self._start_resx_job("resx-scan", resx_config, translator_config)

    def start_resx_translation(self) -> None:
        try:
            resx_config, translator_config = self._build_resx_runtime_config()
        except Exception as exc:
            self._show_error("RESX Settings Error", str(exc))
            return

        if not resx_config.dry_run:
            confirmed = self._ask_yes_no(
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
            self._show_warning("Already Running", "Another operation is already running.")
            return

        self.running_job_name = job_name
        self.cancel_event.clear()
        self.pause_event.clear()
        self._reset_resx_progress()
        self._start_resx_timing()
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
        logger: logging.Logger | None = None
        service_started = False

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
                else create_translator(translator_config, logger=logger)
            )
            service = ResxTranslationService(
                translator=translator,
                logger=logger,
                progress_callback=lambda snapshot: self.events.put(
                    ("resx-progress", snapshot)
                ),
                cancel_callback=self.cancel_event.is_set,
                pause_callback=self.pause_event.is_set,
            )
            service_started = True
            summary = service.run(resx_config)
            self.events.put(("job-done", (job_name, summary, str(log_file))))
        except Exception as exc:
            if logger is not None and not service_started:
                logger.exception("RESX job failed before processing started: %s", exc)
                logger.info(
                    "\n%s",
                    format_unfinished_report(
                        [f"operation | status=failed | reason={exc}"],
                        operation_name="RESX translation",
                    ),
                )
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

    def start_cleanup_single_table(self) -> None:
        table_name = self.cleanup_table_var.get().strip()
        if not table_name:
            self._show_error("Cleanup Table", "Enter a table name in Only table.")
            return
        try:
            schema_name, parsed_table_name = split_table_reference(
                table_name,
                self.cleanup_schema_var.get(),
            )
            config = self._build_cleanup_config(
                schema_name=schema_name,
                table_name=parsed_table_name,
            )
        except Exception as exc:
            self._show_error("Cleanup Settings Error", str(exc))
            return
        self._start_cleanup_job("cleanup-single-table", config)

    def start_cleanup_all_tables(self) -> None:
        if self._is_worker_running():
            self._show_warning("Already Running", "Another operation is already running.")
            return
        try:
            config = self._build_cleanup_config(
                schema_name=self.cleanup_schema_var.get(),
                table_name=None,
            )
        except Exception as exc:
            self._show_error("Cleanup Settings Error", str(exc))
            return

        self._last_cleanup_table_selection_eligible = ()
        selected_tables = self._choose_tables_for_all(config, cleanup=True)
        if selected_tables is None:
            return
        all_eligible_names = getattr(
            self,
            "_last_cleanup_table_selection_eligible",
            (),
        )
        config = replace(
            config,
            excluded_table_names=tuple(
                name for name in all_eligible_names if name not in selected_tables
            ),
        )
        self._start_cleanup_job("cleanup-all-tables", config)

    def _start_cleanup_job(self, job_name: str, config: CleanupConfig) -> None:
        if self._is_worker_running():
            self._show_warning("Already Running", "Another operation is already running.")
            return
        if not config.dry_run:
            language_codes = ", ".join(language.code for language in config.languages)
            confirmed = self._ask_yes_no(
                "Confirm Cleanup Delete",
                "Dry-run is off. Empty localization rows will be permanently "
                f"deleted for these languages: {language_codes}. Continue?",
            )
            if not confirmed:
                return

        self.running_job_name = job_name
        self.cancel_event.clear()
        self.pause_event.clear()
        self._reset_cleanup_progress()
        self._start_cleanup_timing()
        self._set_running(True)
        self._append_log(f"Job started: {job_name}")
        self.worker_thread = threading.Thread(
            target=self._cleanup_worker,
            args=(job_name, config),
            daemon=True,
        )
        self.worker_thread.start()

    def _cleanup_worker(self, job_name: str, config: CleanupConfig) -> None:
        read_connection = None
        write_connection = None
        log_file: Path | None = None
        logger: logging.Logger | None = None
        service_started = False
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
            service = DatabaseCleanupService(
                schema_reader=SqlServerSchemaReader(read_connection),
                repository=SqlServerLocalizationRepository(
                    read_connection,
                    write_connection,
                ),
                logger=logger,
                progress_callback=lambda snapshot: self.events.put(
                    ("cleanup-progress", snapshot)
                ),
                cancel_callback=self.cancel_event.is_set,
                pause_callback=self.pause_event.is_set,
            )
            service_started = True
            summary = service.run(config)
            self.events.put(("job-done", (job_name, summary, str(log_file))))
        except Exception as exc:
            if logger is not None and not service_started:
                logger.exception("Cleanup job failed before processing started: %s", exc)
                logger.info(
                    "\n%s",
                    format_unfinished_report(
                        [f"operation | status=failed | reason={exc}"],
                        operation_name="Database cleanup",
                    ),
                )
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

    def start_test_then_prompt(self) -> None:
        test_table = self.test_table_var.get().strip()
        selected_target = database_target_from_label(self.database_target_var.get())
        if selected_target == RUGSTRUST_DATABASE_TARGET:
            # RugsTrust runs always use the fixed localization table and therefore do
            # not need an interactive test-table prompt.
            self.start_single_table()
            return
        if not test_table:
            self._show_error("Test Table", "Enter a test table name.")
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
            self._show_error("Settings Error", str(exc))
            return

        self.followup_config = full_config
        self._start_job("test-then-prompt", test_config)

    def start_single_table(self) -> None:
        selected_target = database_target_from_label(self.database_target_var.get())
        table_name = self.operation_table_var.get().strip() or self.test_table_var.get().strip()
        if selected_target == RUGSTRUST_DATABASE_TARGET:
            table_name = RUGSTRUST_CERTIFICATION_TABLE
        if not table_name:
            self._show_error(
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
            self._show_error("Settings Error", str(exc))
            return

        self.followup_config = None
        self._start_job("single-table", config)

    def start_all_tables(self) -> None:
        if self._is_worker_running():
            self._show_warning("Already Running", "Another operation is already running.")
            return

        try:
            config = self._build_runtime_config(
                schema_name=self.operation_schema_var.get(),
                table_name=None,
            )
        except Exception as exc:
            self._show_error("Settings Error", str(exc))
            return

        self._last_table_selection_eligible = ()
        selected_tables = self._choose_tables_for_all(config)
        if selected_tables is None:
            return

        all_eligible_names = getattr(self, "_last_table_selection_eligible", ())
        excluded_tables = tuple(
            name for name in all_eligible_names if name not in selected_tables
        )
        config = replace(config, excluded_table_names=excluded_tables)
        self.followup_config = None
        self._start_job("all-tables", config)

    def _choose_tables_for_all(
        self,
        config: RuntimeConfig | CleanupConfig,
        *,
        cleanup: bool = False,
    ) -> tuple[str, ...] | None:
        """Show the sorted table review dialog and return checked tables.

        Table metadata and character estimates are loaded in a worker thread so
        opening the dialog never freezes the GUI while SQL Server is busy.
        ``None`` means that the user cancelled the dialog or loading failed.
        """

        dialog = Toplevel(self.root)
        dialog.title("Select Tables to Clean" if cleanup else "Select Tables to Run")
        dialog.configure(background=BG_COLOR)
        dialog.geometry("780x600")
        dialog.minsize(620, 420)
        dialog.transient(self.root)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        heading = ttk.Frame(dialog, padding=(16, 14, 16, 8), style="App.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(
            heading,
            text="Select tables to clean" if cleanup else "Select tables to translate",
            style="Header.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            heading,
            text=(
                "Tables are ordered by matching empty rows. Checked tables will be cleaned."
                if cleanup
                else "Tables are ordered from the smallest pending text to the largest. "
                "Checked tables will be translated."
            ),
            style="SubHeader.TLabel",
            wraplength=740,
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        status_var = StringVar(value="Loading table sizes…")
        ttk.Label(
            dialog,
            textvariable=status_var,
            style="Field.TLabel",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))

        list_outer = ttk.Frame(dialog, padding=(16, 0, 16, 8), style="App.TFrame")
        list_outer.grid(row=2, column=0, sticky="nsew")
        list_outer.columnconfigure(0, weight=1)
        list_outer.rowconfigure(0, weight=1)
        table_canvas = Canvas(
            list_outer,
            background="#ffffff",
            borderwidth=1,
            highlightthickness=0,
            relief="solid",
        )
        table_canvas.grid(row=0, column=0, sticky="nsew")
        table_scrollbar = ttk.Scrollbar(
            list_outer,
            orient="vertical",
            command=table_canvas.yview,
        )
        table_scrollbar.grid(row=0, column=1, sticky="ns")
        table_canvas.configure(yscrollcommand=table_scrollbar.set)
        table_rows = ttk.Frame(table_canvas, padding=8, style="App.TFrame")
        table_window = table_canvas.create_window((0, 0), window=table_rows, anchor="nw")

        def update_scroll_region(_event: object | None = None) -> None:
            bounds = table_canvas.bbox("all")
            if bounds is not None:
                table_canvas.configure(scrollregion=bounds)
            if table_rows.winfo_reqheight() > max(table_canvas.winfo_height(), 1):
                table_scrollbar.grid()
            else:
                table_scrollbar.grid_remove()

        def resize_rows(event: object) -> None:
            table_canvas.itemconfigure(table_window, width=getattr(event, "width", 1))

        table_rows.bind("<Configure>", update_scroll_region)
        table_canvas.bind("<Configure>", resize_rows)

        def update_table_scrollbar(*args: str) -> None:
            table_scrollbar.set(*args)
            try:
                first, last = float(args[0]), float(args[1])
            except (IndexError, ValueError):
                return
            if first <= 0.0 and last >= 1.0:
                table_scrollbar.grid_remove()
            else:
                table_scrollbar.grid()

        table_canvas.configure(yscrollcommand=update_table_scrollbar)
        table_scrollbar.grid_remove()

        button_frame = ttk.Frame(dialog, padding=(16, 0, 16, 14), style="App.TFrame")
        button_frame.grid(row=3, column=0, sticky="ew")
        button_frame.columnconfigure(0, weight=1)

        selection_vars: dict[str, BooleanVar] = {}
        eligible_names: tuple[str, ...] = ()
        result: list[tuple[str, ...] | None] = [None]
        loaded = [False]

        def close_dialog() -> None:
            if dialog.winfo_exists():
                dialog.destroy()

        def cancel() -> None:
            result[0] = None
            close_dialog()

        def set_all(value: bool) -> None:
            if not loaded[0]:
                return
            for variable in selection_vars.values():
                variable.set(value)

        def continue_with_selection() -> None:
            if not loaded[0]:
                return
            selected = tuple(
                name for name, variable in selection_vars.items() if variable.get()
            )
            if not selected:
                status_var.set("Select at least one eligible table, or click Cancel.")
                return
            result[0] = selected
            close_dialog()

        select_all_button = ttk.Button(
            button_frame,
            text="Select all",
            command=lambda: set_all(True),
            state="disabled",
        )
        select_all_button.grid(row=0, column=0, sticky="w")
        clear_all_button = ttk.Button(
            button_frame,
            text="Clear all",
            command=lambda: set_all(False),
            state="disabled",
        )
        clear_all_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(button_frame, text="Cancel", command=cancel).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        continue_button = ttk.Button(
            button_frame,
            text="Clean selected" if cleanup else "Run selected",
            command=continue_with_selection,
            state="disabled",
            style="Accent.TButton",
        )
        continue_button.grid(row=0, column=3, padx=(8, 0))

        def populate(items: tuple[TableSelectionItem, ...]) -> None:
            nonlocal eligible_names
            if not dialog.winfo_exists():
                return
            for child in table_rows.winfo_children():
                child.destroy()
            selection_vars.clear()
            eligible_names = tuple(item.display_name for item in items if item.eligible)
            for index, item in enumerate(items, start=1):
                variable = BooleanVar(value=item.eligible)
                if item.eligible:
                    selection_vars[item.display_name] = variable
                suffix = (
                    f"{item.pending_rows:,} empty rows"
                    if cleanup
                    else f"{item.text_characters:,} characters · "
                    f"{item.pending_rows:,} pending rows"
                )
                if not item.eligible:
                    suffix += f" · skipped: {item.reason or 'not eligible'}"
                checkbutton = ttk.Checkbutton(
                    table_rows,
                    text=f"{index}. {item.display_name}    {suffix}",
                    variable=variable,
                    state="normal" if item.eligible else "disabled",
                )
                checkbutton.grid(row=index, column=0, sticky="ew", pady=2)
            table_rows.columnconfigure(0, weight=1)
            loaded[0] = True
            selected_action = (
                "checked tables will be cleaned"
                if cleanup
                else "checked tables will run"
            )
            status_var.set(
                f"{len(items)} tables found · {len(eligible_names)} eligible · "
                f"{selected_action}"
            )
            select_all_button.configure(state="normal" if eligible_names else "disabled")
            clear_all_button.configure(state="normal" if eligible_names else "disabled")
            continue_button.configure(state="normal" if eligible_names else "disabled")
            update_scroll_region()

        def show_loading_error(error: Exception) -> None:
            if not dialog.winfo_exists():
                return
            status_var.set(f"Could not load table list: {error}")
            continue_button.configure(state="disabled")

        def load_worker() -> None:
            try:
                if cleanup:
                    items = self._load_cleanup_table_selection_items(config)
                else:
                    items = self._load_table_selection_items(config)
            except Exception as exc:
                try:
                    self.root.after(0, lambda exc=exc: show_loading_error(exc))
                except TclError:
                    pass
                return
            try:
                self.root.after(0, lambda items=items: populate(items))
            except TclError:
                pass

        def poll_mousewheel(event: object) -> str:
            delta = getattr(event, "delta", 0)
            if delta:
                table_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
            return "break"

        table_canvas.bind("<MouseWheel>", poll_mousewheel)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda _event: cancel())
        dialog.update_idletasks()
        center_dialog(self.root, dialog)
        try:
            dialog.grab_set()
        except TclError:
            pass
        dialog.focus_set()
        threading.Thread(target=load_worker, daemon=True).start()
        self.root.wait_window(dialog)
        if cleanup:
            self._last_cleanup_table_selection_eligible = eligible_names
        else:
            self._last_table_selection_eligible = eligible_names
        return result[0]

    def _load_cleanup_table_selection_items(
        self,
        config: CleanupConfig,
    ) -> tuple[TableSelectionItem, ...]:
        connection = connect(config.connection_string, autocommit=False)
        try:
            tables = SqlServerSchemaReader(connection).get_localize_tables(
                schema_name=config.schema_name,
                table_name=config.table_name,
            )
            repository = SqlServerLocalizationRepository(connection)
            items: list[TableSelectionItem] = []
            for table in tables:
                if not table.language_column_name:
                    items.append(
                        TableSelectionItem(
                            display_name=table.display_name,
                            eligible=False,
                            reason="LanguageId/LangId column was not found",
                        )
                    )
                    continue
                if not table.cleanup_text_columns():
                    items.append(
                        TableSelectionItem(
                            display_name=table.display_name,
                            eligible=False,
                            reason="no textual content column was found",
                        )
                    )
                    continue
                matched_rows = 0
                for language in config.languages:
                    matched_rows += int(
                        run_with_retry(
                            lambda table=table, language=language: repository.count_empty_localized_rows(
                                table,
                                language_id=language.id,
                            ),
                            operation_name=(
                                f"count empty rows in {table.display_name} ({language.code})"
                            ),
                            attempts=config.retry.attempts,
                            initial_delay_seconds=config.retry.initial_delay_seconds,
                            backoff_factor=config.retry.backoff_factor,
                            logger=logging.getLogger("database_translator"),
                        )
                    )
                items.append(
                    TableSelectionItem(
                        display_name=table.display_name,
                        pending_rows=max(matched_rows, 0),
                    )
                )
        finally:
            connection.close()
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    not item.eligible,
                    item.pending_rows,
                    item.display_name.casefold(),
                ),
            )
        )

    def _load_table_selection_items(
        self,
        config: RuntimeConfig,
    ) -> tuple[TableSelectionItem, ...]:
        """Read the all-table preview data used by the selection modal."""

        connection = connect(config.connection_string, autocommit=False)
        try:
            tables = SqlServerSchemaReader(connection).get_localize_tables(
                schema_name=config.schema_name,
                table_name=config.table_name,
            )
            repository = SqlServerLocalizationRepository(connection)
            items: list[TableSelectionItem] = []
            target_languages = config.selected_target_languages()
            for table in tables:
                try:
                    plan = build_table_translation_plan(table)
                    pending_rows = 0
                    text_characters = 0
                    for target_language in target_languages:
                        target_pending_rows = run_with_retry(
                            lambda plan=plan, target_language=target_language: repository.count_pending_rows(
                                plan,
                                source_language_id=config.source_language.id,
                                target_language_id=target_language.id,
                            ),
                            operation_name=(
                                f"count {table.display_name} ({target_language.code})"
                            ),
                            attempts=config.retry.attempts,
                            initial_delay_seconds=config.retry.initial_delay_seconds,
                            backoff_factor=config.retry.backoff_factor,
                            logger=logging.getLogger("database_translator"),
                        )
                        pending_rows += int(target_pending_rows)
                        if target_pending_rows:
                            try:
                                text_characters += run_with_retry(
                                    lambda plan=plan, target_language=target_language: repository.count_pending_text_characters(
                                        plan,
                                        source_language_id=config.source_language.id,
                                        target_language_id=target_language.id,
                                    ),
                                    operation_name=(
                                        f"measure text in {table.display_name} ({target_language.code})"
                                    ),
                                    attempts=config.retry.attempts,
                                    initial_delay_seconds=config.retry.initial_delay_seconds,
                                    backoff_factor=config.retry.backoff_factor,
                                    logger=logging.getLogger("database_translator"),
                                )
                            except Exception as exc:
                                text_characters += int(target_pending_rows)
                                logging.getLogger("database_translator").warning(
                                    "Could not measure text size for %s (%s); using pending rows: %s",
                                    table.display_name,
                                    target_language.code,
                                    exc,
                                )
                except Exception as exc:
                    items.append(
                        TableSelectionItem(
                            display_name=table.display_name,
                            eligible=False,
                            reason=str(exc),
                        )
                    )
                    continue

                if pending_rows and not text_characters:
                    text_characters = pending_rows
                items.append(
                    TableSelectionItem(
                        display_name=table.display_name,
                        text_characters=max(int(text_characters), 0),
                        pending_rows=max(int(pending_rows), 0),
                    )
                )
        finally:
            connection.close()

        return tuple(
            sorted(
                items,
                key=lambda item: (
                    not item.eligible,
                    item.text_characters if item.eligible else 0,
                    item.pending_rows,
                    item.display_name.casefold(),
                ),
            )
        )

    def _start_job(self, job_name: str, config: RuntimeConfig) -> None:
        if self._is_worker_running():
            self._show_warning("Already Running", "Another operation is already running.")
            return

        if not config.dry_run:
            confirmed = self._ask_yes_no(
                "Confirm Execute Mode",
                "Dry-run is off. The app will insert new rows. Continue?",
            )
            if not confirmed:
                return

        self.running_job_name = job_name
        self.cancel_event.clear()
        self.pause_event.clear()
        self._reset_progress()
        self._start_operation_timing()
        self._set_running(True)
        self._append_log(f"Job started: {job_name}")

        self.worker_thread = threading.Thread(
            target=self._translation_worker,
            args=(job_name, config),
            daemon=True,
        )
        self.worker_thread.start()

    def _translation_worker(self, job_name: str, config: RuntimeConfig) -> None:
        log_file: Path | None = None
        logger: logging.Logger | None = None
        service_started = False

        try:
            queue_handler = QueueLogHandler(self.events)
            logger, log_file = configure_logging(
                config.log_dir,
                extra_handlers=(queue_handler,),
            )
            self.events.put(("log-file", str(log_file)))

            summaries: list[TranslationSummary] = []
            for target_config in self._database_runtime_configs(config):
                self.events.put(
                    ("log", f"Database target started: {target_config.database_target}")
                )
                read_connection = connect(
                    target_config.connection_string,
                    autocommit=False,
                )
                write_connection = (
                    connect(target_config.connection_string, autocommit=True)
                    if not target_config.dry_run
                    else read_connection
                )
                try:
                    service = DatabaseTranslationService(
                        schema_reader=SqlServerSchemaReader(read_connection),
                        repository=SqlServerLocalizationRepository(
                            read_connection,
                            write_connection,
                        ),
                        translator=create_translator(target_config, logger=logger),
                        logger=logger,
                        progress_callback=lambda snapshot: self.events.put(
                            ("progress", snapshot)
                        ),
                        cancel_callback=self.cancel_event.is_set,
                        pause_callback=self.pause_event.is_set,
                    )
                    service_started = True
                    summaries.append(service.run(target_config))
                finally:
                    if write_connection is not read_connection:
                        write_connection.close()
                    read_connection.close()
            summary = merge_translation_summaries(summaries)
            self.events.put(("job-done", (job_name, summary, str(log_file))))
        except Exception as exc:
            if logger is not None and not service_started:
                logger.exception(
                    "Database translation failed before processing started: %s",
                    exc,
                )
                logger.info(
                    "\n%s",
                    format_unfinished_report(
                        [f"operation | status=failed | reason={exc}"],
                        operation_name="Database translation",
                    ),
                )
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

    def _database_runtime_configs(
        self,
        config: RuntimeConfig,
    ) -> tuple[RuntimeConfig, ...]:
        target = normalize_database_target(
            config.database_target,
            rugstrust_available=config.rugstrust_connection_settings is not None,
        )
        guereh = replace(config, database_target=GUEREH_DATABASE_TARGET)
        if target == GUEREH_DATABASE_TARGET:
            return (guereh,)
        if config.rugstrust_connection_settings is None:
            raise ValueError("Configure RugsTrust database settings before selecting it.")
        rugstrust = replace(
            config,
            connection_string=config.rugstrust_connection_settings.build_connection_string(),
            schema_name=config.rugstrust_schema_name or RUGSTRUST_DEFAULT_SCHEMA,
            table_name=RUGSTRUST_CERTIFICATION_TABLE,
            excluded_table_names=(),
            database_target=RUGSTRUST_DATABASE_TARGET,
        )
        if target == RUGSTRUST_DATABASE_TARGET:
            return (rugstrust,)
        return (guereh, rugstrust)

    def stop_operation(self) -> None:
        if not self._is_worker_running():
            return
        self.cancel_event.set()
        self._set_active_job_status("Stop requested...")
        for button_name in (
            "pause_button",
            "resx_pause_button",
            "cleanup_pause_button",
            "stop_button",
            "resx_stop_button",
            "cleanup_stop_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state="disabled")
        self._append_log(
            "Stop requested. The current row, cleanup batch, or resource key will finish before "
            "the job stops."
        )

    def toggle_pause(self) -> None:
        if not self._is_worker_running() or self.cancel_event.is_set():
            return

        if self.pause_event.is_set():
            self._resume_timing()
            self.pause_event.clear()
            self._set_pause_controls(paused=False)
            self._set_active_job_status("Resuming...")
            self._append_log("Resume requested. Continuing the current job.")
            return

        self._pause_timing()
        self.pause_event.set()
        self._set_pause_controls(paused=True)
        self._set_active_job_status("Pause requested...")
        self._append_log(
            "Pause requested. The current row, cleanup batch, or resource key will finish, then "
            "the job will wait for Resume."
        )

    def _set_active_job_status(self, status: str) -> None:
        if self.running_job_name.startswith("resx-"):
            self.resx_status_var.set(status)
        elif self.running_job_name.startswith("cleanup-"):
            self.cleanup_status_var.set(status)
        else:
            self.status_var.set(status)

    def _pause_timing(self) -> None:
        prefix = self._active_timing_prefix()
        paused_name = f"{prefix}_paused_started_monotonic"
        started_name = f"{prefix}_started_monotonic"
        if getattr(self, started_name, None) is not None and getattr(self, paused_name, None) is None:
            setattr(self, paused_name, time.monotonic())

    def _resume_timing(self) -> None:
        prefix = self._active_timing_prefix()
        paused_name = f"{prefix}_paused_started_monotonic"
        total_name = f"{prefix}_paused_total_seconds"
        paused_started = getattr(self, paused_name, None)
        if paused_started is None:
            return
        paused_total = getattr(self, total_name, 0.0)
        setattr(self, total_name, paused_total + max(time.monotonic() - paused_started, 0.0))
        setattr(self, paused_name, None)

    def _active_elapsed_seconds(self, prefix: str) -> float | None:
        started = getattr(self, f"{prefix}_started_monotonic", None)
        if started is None:
            return None
        now = time.monotonic()
        paused_total = getattr(self, f"{prefix}_paused_total_seconds", 0.0)
        paused_started = getattr(self, f"{prefix}_paused_started_monotonic", None)
        if paused_started is not None:
            paused_total += max(now - paused_started, 0.0)
        return max(now - started - paused_total, 0.0)

    def _set_pause_controls(self, *, paused: bool) -> None:
        text = "Resume" if paused else "Pause"
        style = "Resume.TButton" if paused else "Pause.TButton"
        for button_name in (
            "pause_button",
            "resx_pause_button",
            "cleanup_pause_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(text=text, style=style)

    def _active_timing_prefix(self) -> str:
        if self.running_job_name.startswith("resx-"):
            return "resx"
        if self.running_job_name.startswith("cleanup-"):
            return "cleanup"
        return "operation"

    def _target_languages_changed(self) -> None:
        selected = self.target_language_selector.selected_ids()
        if selected:
            self.target_language_var.set(language_label(selected[0]))

    def _resx_target_languages_changed(self) -> None:
        selected = self.resx_target_language_selector.selected_ids()
        if selected:
            self.resx_target_language_var.set(language_label(selected[0]))

    def _cleanup_languages_changed(self) -> None:
        selected = self.cleanup_language_selector.selected_ids()
        if selected:
            self.cleanup_language_var.set(language_label(selected[0]))

    def _selected_target_ids(self, selector_name: str, variable: StringVar) -> tuple[int, ...]:
        selector = getattr(self, selector_name, None)
        if selector is not None:
            selected = tuple(selector.selected_ids())
            if selected:
                return selected
        return (language_id_from_label(variable.get()),)

    def _build_runtime_config(
        self,
        *,
        schema_name: str | None,
        table_name: str | None,
    ) -> RuntimeConfig:
        source_language_id = language_id_from_label(self.source_language_var.get())
        target_ids = self._selected_target_ids(
            "target_language_selector", self.target_language_var
        )
        if not target_ids:
            raise ValueError("Select at least one target language.")
        if source_language_id in target_ids:
            raise ValueError("Source and target languages must be different.")
        target_languages = tuple(get_language(language_id) for language_id in target_ids)

        database_target = database_target_from_label(self.database_target_var.get())
        rugstrust_connection_settings = self._build_rugstrust_connection_settings()
        rugstrust_available = bool(
            rugstrust_connection_settings.server.strip()
            and rugstrust_connection_settings.database.strip()
        )
        database_target = normalize_database_target(
            database_target,
            rugstrust_available=rugstrust_available,
        )
        if database_target == RUGSTRUST_DATABASE_TARGET:
            if not rugstrust_available:
                raise ValueError("Configure RugsTrust database settings before selecting it.")
            connection_string = rugstrust_connection_settings.build_connection_string()
            schema_name = normalize_sql_name(self.rugstrust_schema_var.get()) or RUGSTRUST_DEFAULT_SCHEMA
            table_name = RUGSTRUST_CERTIFICATION_TABLE
        else:
            connection_settings = self._build_connection_settings()
            connection_string = connection_settings.build_connection_string()
            schema_name = normalize_sql_name(schema_name)
            table_name = normalize_sql_name(table_name)

        return RuntimeConfig(
            connection_string=connection_string,
            source_language=get_language(source_language_id),
            target_language=target_languages[0],
            dry_run=self.dry_run_var.get(),
            schema_name=schema_name,
            table_name=table_name,
            batch_size=max(parse_int(self.batch_size_var.get(), "Batch size"), 1),
            progress_every=max(
                parse_int(self.progress_every_var.get(), "Progress every"),
                1,
            ),
            translator_provider="auto",
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
            target_languages=target_languages,
            database_target=database_target,
            rugstrust_connection_settings=(rugstrust_connection_settings if rugstrust_available else None),
            rugstrust_schema_name=normalize_sql_name(self.rugstrust_schema_var.get()) or RUGSTRUST_DEFAULT_SCHEMA,
        )

    def _build_cleanup_config(
        self,
        *,
        schema_name: str | None,
        table_name: str | None,
    ) -> CleanupConfig:
        language_ids = self._selected_target_ids(
            "cleanup_language_selector",
            self.cleanup_language_var,
        )
        if not language_ids:
            raise ValueError("Select at least one cleanup language.")
        return CleanupConfig(
            connection_string=self._build_connection_settings().build_connection_string(),
            languages=tuple(get_language(language_id) for language_id in language_ids),
            dry_run=self.cleanup_dry_run_var.get(),
            schema_name=normalize_sql_name(schema_name),
            table_name=normalize_sql_name(table_name),
            batch_size=max(parse_int(self.batch_size_var.get(), "Batch size"), 1),
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
        target_ids = self._selected_target_ids(
            "resx_target_language_selector", self.resx_target_language_var
        )
        if not target_ids:
            raise ValueError("Select at least one target language.")
        if source_language_id in target_ids:
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
        target_languages = tuple(get_language(language_id) for language_id in target_ids)
        target_language = target_languages[0]
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
            target_languages=target_languages,
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
            translator_provider="auto",
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
            target_languages=target_languages,
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
        username = self.username_var.get().strip() or None
        password = self.password_var.get()
        return SqlServerConnectionSettings(
            connection_string=None,
            driver=self.driver_var.get().strip() or "ODBC Driver 18 for SQL Server",
            server=self.server_var.get().strip(),
            database=self.database_var.get().strip(),
            username=username,
            password=password,
            trusted_connection=self._effective_trusted_connection(username, password),
            encrypt=self.encrypt_var.get(),
            trust_server_certificate=self.trust_server_certificate_var.get(),
        )

    def _build_rugstrust_connection_settings(self) -> SqlServerConnectionSettings:
        return SqlServerConnectionSettings(
            connection_string=None,
            driver=self.rugstrust_driver_var.get().strip() or "ODBC Driver 18 for SQL Server",
            server=self.rugstrust_server_var.get().strip(),
            database=self.rugstrust_database_var.get().strip(),
            username=self.rugstrust_username_var.get().strip() or None,
            password=self.rugstrust_password_var.get(),
            trusted_connection=self.rugstrust_trusted_connection_var.get()
            and not (
                bool(self.rugstrust_username_var.get().strip())
                or self.rugstrust_password_var.get() not in {None, ""}
            ),
            encrypt=self.rugstrust_encrypt_var.get(),
            trust_server_certificate=self.rugstrust_trust_server_certificate_var.get(),
        )

    def _effective_trusted_connection(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> bool:
        if username is None:
            current_username = self.username_var.get().strip() or None
        else:
            current_username = username

        if password is None:
            current_password = self.password_var.get()
        else:
            current_password = password

        has_credentials = bool(current_username) or current_password not in {None, ""}
        return self.trusted_connection_var.get() and not has_credentials

    def _settings_env_values(self) -> dict[str, str]:
        return {
            "GUEREH_SQLSERVER_DRIVER": self.driver_var.get().strip(),
            "GUEREH_SQLSERVER_SERVER": self.server_var.get().strip(),
            "GUEREH_SQLSERVER_DATABASE": self.database_var.get().strip(),
            "GUEREH_SQLSERVER_USERNAME": self.username_var.get().strip(),
            "GUEREH_SQLSERVER_PASSWORD": self.password_var.get(),
            "GUEREH_SQLSERVER_TRUSTED_CONNECTION": bool_to_env(
                self._effective_trusted_connection()
            ),
            "GUEREH_SQLSERVER_NO_ENCRYPT": bool_to_env(not self.encrypt_var.get()),
            "GUEREH_SQLSERVER_TRUST_SERVER_CERTIFICATE": bool_to_env(
                self.trust_server_certificate_var.get()
            ),
            "DATABASE_TARGET": database_target_from_label(self.database_target_var.get()),
            "RUGSTRUST_SQLSERVER_DRIVER": self.rugstrust_driver_var.get().strip(),
            "RUGSTRUST_SQLSERVER_SERVER": self.rugstrust_server_var.get().strip(),
            "RUGSTRUST_SQLSERVER_DATABASE": self.rugstrust_database_var.get().strip(),
            "RUGSTRUST_SQLSERVER_USERNAME": self.rugstrust_username_var.get().strip(),
            "RUGSTRUST_SQLSERVER_PASSWORD": self.rugstrust_password_var.get(),
            "RUGSTRUST_SQLSERVER_TRUSTED_CONNECTION": bool_to_env(
                self.rugstrust_trusted_connection_var.get()
                and not (
                    bool(self.rugstrust_username_var.get().strip())
                    or self.rugstrust_password_var.get() not in {None, ""}
                )
            ),
            "RUGSTRUST_SQLSERVER_NO_ENCRYPT": bool_to_env(not self.rugstrust_encrypt_var.get()),
            "RUGSTRUST_SQLSERVER_TRUST_SERVER_CERTIFICATE": bool_to_env(
                self.rugstrust_trust_server_certificate_var.get()
            ),
            "RUGSTRUST_SQLSERVER_SCHEMA": self.rugstrust_schema_var.get().strip() or RUGSTRUST_DEFAULT_SCHEMA,
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
            "TARGET_LANGUAGE_IDS": ",".join(
                str(language_id)
                for language_id in self._selected_target_ids(
                    "target_language_selector", self.target_language_var
                )
            ),
            "TARGET_LANGUAGE_ID": str(
                self._selected_target_ids(
                    "target_language_selector", self.target_language_var
                )[0]
            ),
            "CLEANUP_LANGUAGE_IDS": ",".join(
                str(language_id)
                for language_id in self._selected_target_ids(
                    "cleanup_language_selector", self.cleanup_language_var
                )
            ),
            "CLEANUP_DRY_RUN": bool_to_env(self.cleanup_dry_run_var.get()),
            "RESX_RESOURCE_DIR": self.resx_resource_dir_var.get().strip(),
            "RESX_SOURCE_LANGUAGE_ID": str(
                language_id_from_label(self.resx_source_language_var.get())
            ),
            "RESX_TARGET_LANGUAGE_ID": str(
                language_id_from_label(self.resx_target_language_var.get())
            ),
            "RESX_TARGET_LANGUAGE_IDS": ",".join(
                str(language_id)
                for language_id in self._selected_target_ids(
                    "resx_target_language_selector", self.resx_target_language_var
                )
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
            elif event_type == "cleanup-progress":
                self._apply_cleanup_progress(payload)
            elif event_type == "log-file":
                self.log_file_var.set(str(payload))
                self.resx_log_file_var.set(str(payload))
                self.cleanup_log_file_var.set(str(payload))
            elif event_type == "job-done":
                job_name, summary, log_file = payload
                if str(job_name).startswith("resx-"):
                    self._handle_resx_job_done(str(job_name), summary, str(log_file))
                elif str(job_name).startswith("cleanup-"):
                    self._handle_cleanup_job_done(str(job_name), summary, str(log_file))
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
                elif str(job_name).startswith("cleanup-"):
                    self._handle_cleanup_job_error(
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
                self._show_info(str(title), str(message))
            elif event_type == "error-message":
                title, message = payload
                self._append_log(str(message))
                self._show_error(str(title), str(message))

        self._refresh_running_duration()
        self.root.after(200, self._poll_events)

    def _apply_progress(self, snapshot: object) -> None:
        if not isinstance(snapshot, ProgressSnapshot):
            return

        if self.cancel_event.is_set():
            self.status_var.set("Stop requested...")
        elif self.pause_event.is_set():
            self.status_var.set("Paused")
        else:
            self.status_var.set(phase_label(snapshot.phase))
        self.current_table_var.set(snapshot.current_table or "-")
        self.overall_progress["value"] = snapshot.percent
        self.table_progress["value"] = snapshot.table_percent
        self.language_progress["value"] = queue_progress_percent(snapshot)
        self.overall_percent_var.set(f"{snapshot.percent:.2f}%")
        self.table_percent_var.set(f"{snapshot.table_percent:.2f}%")
        self.target_language_progress_var.set(
            f"Language {snapshot.target_language_index}/{snapshot.total_target_languages}: "
            f"{snapshot.current_target_language or '-'} · "
            f"Completed: {format_language_code_list(snapshot.completed_target_language_codes)} · "
            f"Remaining: {format_language_code_list(snapshot.remaining_target_language_codes)}"
        )
        self.discovered_tables_var.set(str(snapshot.discovered_tables))
        self.eligible_tables_var.set(str(snapshot.eligible_tables))
        self.skipped_tables_var.set(str(snapshot.skipped_tables))
        self.total_rows_var.set(str(snapshot.pending_rows))
        self.processed_rows_var.set(str(snapshot.processed_rows))
        self.remaining_rows_var.set(str(snapshot.remaining_rows))
        self.inserted_rows_var.set(str(snapshot.inserted_rows))
        self.updated_rows_var.set(str(snapshot.updated_rows))
        self.skipped_existing_rows_var.set(str(snapshot.skipped_existing_rows))
        self.failed_rows_var.set(str(snapshot.failed_rows))
        self.operation_last_processed_rows = snapshot.processed_rows
        self.operation_last_remaining_rows = snapshot.remaining_rows
        self._refresh_operation_throughput()

    def _apply_resx_progress(self, snapshot: object) -> None:
        if not isinstance(snapshot, ResxProgressSnapshot):
            return

        if self.cancel_event.is_set():
            self.resx_status_var.set("Stop requested...")
        elif self.pause_event.is_set():
            self.resx_status_var.set("Paused")
        else:
            self.resx_status_var.set(resx_phase_label(snapshot.phase))
        self.resx_current_file_var.set(snapshot.current_file or "-")
        self.resx_current_key_var.set(snapshot.current_key or "-")
        self.resx_overall_progress["value"] = snapshot.percent
        self.resx_file_progress["value"] = snapshot.file_percent
        self.resx_language_progress["value"] = queue_progress_percent(snapshot)
        self.resx_overall_percent_var.set(f"{snapshot.percent:.2f}%")
        self.resx_file_percent_var.set(f"{snapshot.file_percent:.2f}%")
        self.resx_target_language_progress_var.set(
            f"Language {snapshot.target_language_index}/{snapshot.total_target_languages}: "
            f"{snapshot.current_target_language or '-'} · "
            f"Completed: {format_language_code_list(snapshot.completed_target_language_codes)} · "
            f"Remaining: {format_language_code_list(snapshot.remaining_target_language_codes)}"
        )
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
        self.resx_last_processed_entries = snapshot.processed_entries
        self.resx_last_remaining_entries = snapshot.remaining_entries
        self._refresh_resx_throughput()

    def _apply_cleanup_progress(self, snapshot: object) -> None:
        if not isinstance(snapshot, CleanupProgressSnapshot):
            return
        if self.cancel_event.is_set():
            self.cleanup_status_var.set("Stop requested...")
        elif self.pause_event.is_set():
            self.cleanup_status_var.set("Paused")
        else:
            self.cleanup_status_var.set(phase_label(snapshot.phase))
        self.cleanup_current_table_var.set(snapshot.current_table or "-")
        self.cleanup_overall_progress["value"] = snapshot.percent
        self.cleanup_table_progress["value"] = snapshot.table_percent
        self.cleanup_language_progress["value"] = queue_progress_percent(snapshot)
        self.cleanup_overall_percent_var.set(f"{snapshot.percent:.2f}%")
        self.cleanup_table_percent_var.set(f"{snapshot.table_percent:.2f}%")
        self.cleanup_language_progress_var.set(
            f"Language {snapshot.target_language_index}/{snapshot.total_target_languages}: "
            f"{snapshot.current_target_language or '-'} · "
            f"Completed: {format_language_code_list(snapshot.completed_target_language_codes)} · "
            f"Remaining: {format_language_code_list(snapshot.remaining_target_language_codes)}"
        )
        self.cleanup_discovered_tables_var.set(str(snapshot.discovered_tables))
        self.cleanup_eligible_tables_var.set(str(snapshot.eligible_tables))
        self.cleanup_skipped_tables_var.set(str(snapshot.skipped_tables))
        self.cleanup_matched_rows_var.set(str(snapshot.matched_rows))
        self.cleanup_processed_rows_var.set(str(snapshot.processed_rows))
        self.cleanup_remaining_rows_var.set(str(snapshot.remaining_rows))
        self.cleanup_deleted_rows_var.set(str(snapshot.deleted_rows))
        self.cleanup_failed_rows_var.set(str(snapshot.failed_rows))
        self.cleanup_last_processed_rows = snapshot.processed_rows
        self.cleanup_last_remaining_rows = snapshot.remaining_rows
        self._refresh_cleanup_throughput()

    def _handle_resx_job_done(
        self,
        job_name: str,
        summary: object,
        log_file: str,
    ) -> None:
        stopped = self.cancel_event.is_set()
        self._set_running(False)
        self._finish_resx_timing()
        self.resx_status_var.set("Stopped" if stopped else "Finished")
        self.resx_log_file_var.set(log_file)

        if stopped:
            self._append_log(f"Job stopped: {job_name}")
            return

        self._append_log(f"Job finished: {job_name}")

        translated = getattr(summary, "translated_entries", 0)
        skipped_existing = getattr(summary, "skipped_existing_entries", 0)
        failed = getattr(summary, "failed_entries", 0)
        created = getattr(summary, "created_files", 0)
        updated = getattr(summary, "updated_files", 0)
        self._show_info(
            "RESX Finished",
            "RESX operation finished.\n"
            f"Translated: {translated}\n"
            f"Existing skipped: {skipped_existing}\n"
            f"Failed: {failed}\n"
            f"Created files: {created}\n"
            f"Updated files: {updated}",
        )

    def _handle_cleanup_job_done(
        self,
        job_name: str,
        summary: object,
        log_file: str,
    ) -> None:
        stopped = self.cancel_event.is_set()
        self._set_running(False)
        self._finish_cleanup_timing()
        self.cleanup_status_var.set("Stopped" if stopped else "Finished")
        self.cleanup_log_file_var.set(log_file)
        if stopped:
            self._append_log(f"Job stopped: {job_name}")
            return
        self._append_log(f"Job finished: {job_name}")
        self._show_info(
            "Cleanup Finished",
            "Cleanup operation finished.\n"
            f"Matched: {getattr(summary, 'matched_rows', 0)}\n"
            f"Deleted: {getattr(summary, 'deleted_rows', 0)}\n"
            f"Failed: {getattr(summary, 'failed_rows', 0)}\n"
            f"Skipped tables: {getattr(summary, 'skipped_tables', 0)}",
        )

    def _handle_job_done(
        self,
        job_name: str,
        summary: object,
        log_file: str,
    ) -> None:
        stopped = self.cancel_event.is_set()
        self._set_running(False)
        self._finish_operation_timing()
        self.status_var.set("Stopped" if stopped else "Finished")
        self.log_file_var.set(log_file)

        if stopped:
            self._append_log(f"Job stopped: {job_name}")
            return

        self._append_log(f"Job finished: {job_name}")

        if job_name != "test-then-prompt" or self.followup_config is None:
            return

        inserted_rows = getattr(summary, "inserted_rows", 0)
        updated_rows = getattr(summary, "updated_rows", 0)
        failed_rows = getattr(summary, "failed_rows", 0)
        skipped_tables = getattr(summary, "skipped_tables", 0)
        should_continue = self._ask_yes_no(
            "Continue Operation",
            "Test table finished.\n"
            f"Inserted: {inserted_rows}\n"
            f"Updated: {updated_rows}\n"
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
        self._finish_operation_timing()
        self.status_var.set("Error")
        self.log_file_var.set(log_file)
        self._append_log(f"Job failed: {job_name}: {message}")
        self._append_log(details)
        self._show_error("Job Error", message)

    def _handle_resx_job_error(
        self,
        job_name: str,
        message: str,
        details: str,
        log_file: str,
    ) -> None:
        self._set_running(False)
        self._finish_resx_timing()
        self.resx_status_var.set("Error")
        self.resx_log_file_var.set(log_file)
        self._append_log(f"Job failed: {job_name}: {message}")
        self._append_log(details)
        self._show_error("RESX Job Error", message)

    def _handle_cleanup_job_error(
        self,
        job_name: str,
        message: str,
        details: str,
        log_file: str,
    ) -> None:
        self._set_running(False)
        self._finish_cleanup_timing()
        self.cleanup_status_var.set("Error")
        self.cleanup_log_file_var.set(log_file)
        self._append_log(f"Job failed: {job_name}: {message}")
        self._append_log(details)
        self._show_error("Cleanup Job Error", message)

    def _reset_progress(self) -> None:
        self.status_var.set("Starting...")
        self.current_table_var.set("-")
        self.overall_progress["value"] = 0
        self.table_progress["value"] = 0
        self.language_progress["value"] = 0
        self.target_language_progress_var.set("Languages: 0/0 completed · 0 remaining")
        self.overall_percent_var.set("0.00%")
        self.table_percent_var.set("0.00%")
        self.discovered_tables_var.set("0")
        self.eligible_tables_var.set("0")
        self.skipped_tables_var.set("0")
        self.total_rows_var.set("0")
        self.processed_rows_var.set("0")
        self.remaining_rows_var.set("0")
        self.inserted_rows_var.set("0")
        self.updated_rows_var.set("0")
        self.skipped_existing_rows_var.set("0")
        self.failed_rows_var.set("0")
        self.operation_started_var.set("-")
        self.operation_finished_var.set("-")
        self.operation_duration_var.set("-")
        self.operation_average_rate_var.set("-")
        self.operation_estimated_finish_var.set("-")
        self.operation_started_monotonic = None
        self.operation_paused_started_monotonic = None
        self.operation_paused_total_seconds = 0.0
        self.operation_last_processed_rows = 0
        self.operation_last_remaining_rows = 0

    def _reset_resx_progress(self) -> None:
        self.resx_status_var.set("Starting...")
        self.resx_current_file_var.set("-")
        self.resx_current_key_var.set("-")
        self.resx_overall_progress["value"] = 0
        self.resx_file_progress["value"] = 0
        self.resx_language_progress["value"] = 0
        self.resx_target_language_progress_var.set("Languages: 0/0 completed · 0 remaining")
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
        self.resx_started_var.set("-")
        self.resx_finished_var.set("-")
        self.resx_duration_var.set("-")
        self.resx_average_rate_var.set("-")
        self.resx_estimated_finish_var.set("-")
        self.resx_started_monotonic = None
        self.resx_paused_started_monotonic = None
        self.resx_paused_total_seconds = 0.0
        self.resx_last_processed_entries = 0
        self.resx_last_remaining_entries = 0

    def _reset_cleanup_progress(self) -> None:
        self.cleanup_status_var.set("Starting...")
        self.cleanup_current_table_var.set("-")
        self.cleanup_overall_progress["value"] = 0
        self.cleanup_table_progress["value"] = 0
        self.cleanup_language_progress["value"] = 0
        self.cleanup_language_progress_var.set(
            "Languages: 0/0 completed · 0 remaining"
        )
        self.cleanup_overall_percent_var.set("0.00%")
        self.cleanup_table_percent_var.set("0.00%")
        self.cleanup_discovered_tables_var.set("0")
        self.cleanup_eligible_tables_var.set("0")
        self.cleanup_skipped_tables_var.set("0")
        self.cleanup_matched_rows_var.set("0")
        self.cleanup_processed_rows_var.set("0")
        self.cleanup_remaining_rows_var.set("0")
        self.cleanup_deleted_rows_var.set("0")
        self.cleanup_failed_rows_var.set("0")
        self.cleanup_started_var.set("-")
        self.cleanup_finished_var.set("-")
        self.cleanup_duration_var.set("-")
        self.cleanup_average_rate_var.set("-")
        self.cleanup_estimated_finish_var.set("-")
        self.cleanup_started_monotonic = None
        self.cleanup_paused_started_monotonic = None
        self.cleanup_paused_total_seconds = 0.0
        self.cleanup_last_processed_rows = 0
        self.cleanup_last_remaining_rows = 0

    def _start_operation_timing(self) -> None:
        self.operation_started_monotonic = time.monotonic()
        self.operation_started_var.set(format_timestamp(datetime.now()))
        self.operation_finished_var.set("-")
        self.operation_duration_var.set("00:00:00")
        self.operation_average_rate_var.set("-")
        self.operation_estimated_finish_var.set("-")
        self.operation_paused_started_monotonic = None
        self.operation_paused_total_seconds = 0.0

    def _finish_operation_timing(self) -> None:
        if self.operation_started_monotonic is None:
            return
        elapsed_seconds = self._active_elapsed_seconds("operation") or 0.0
        self.operation_finished_var.set(format_timestamp(datetime.now()))
        self.operation_duration_var.set(format_duration(elapsed_seconds))
        self._refresh_operation_throughput(elapsed_seconds, force=True)
        self.operation_started_monotonic = None
        self.operation_paused_started_monotonic = None
        self.operation_paused_total_seconds = 0.0

    def _start_resx_timing(self) -> None:
        self.resx_started_monotonic = time.monotonic()
        self.resx_started_var.set(format_timestamp(datetime.now()))
        self.resx_finished_var.set("-")
        self.resx_duration_var.set("00:00:00")
        self.resx_average_rate_var.set("-")
        self.resx_estimated_finish_var.set("-")
        self.resx_paused_started_monotonic = None
        self.resx_paused_total_seconds = 0.0

    def _finish_resx_timing(self) -> None:
        if self.resx_started_monotonic is None:
            return
        elapsed_seconds = self._active_elapsed_seconds("resx") or 0.0
        self.resx_finished_var.set(format_timestamp(datetime.now()))
        self.resx_duration_var.set(format_duration(elapsed_seconds))
        self._refresh_resx_throughput(elapsed_seconds, force=True)
        self.resx_started_monotonic = None
        self.resx_paused_started_monotonic = None
        self.resx_paused_total_seconds = 0.0

    def _start_cleanup_timing(self) -> None:
        self.cleanup_started_monotonic = time.monotonic()
        self.cleanup_started_var.set(format_timestamp(datetime.now()))
        self.cleanup_finished_var.set("-")
        self.cleanup_duration_var.set("00:00:00")
        self.cleanup_average_rate_var.set("-")
        self.cleanup_estimated_finish_var.set("-")
        self.cleanup_paused_started_monotonic = None
        self.cleanup_paused_total_seconds = 0.0

    def _finish_cleanup_timing(self) -> None:
        if self.cleanup_started_monotonic is None:
            return
        elapsed_seconds = self._active_elapsed_seconds("cleanup") or 0.0
        self.cleanup_finished_var.set(format_timestamp(datetime.now()))
        self.cleanup_duration_var.set(format_duration(elapsed_seconds))
        self._refresh_cleanup_throughput(elapsed_seconds, force=True)
        self.cleanup_started_monotonic = None
        self.cleanup_paused_started_monotonic = None
        self.cleanup_paused_total_seconds = 0.0

    def _refresh_running_duration(self) -> None:
        if not self._is_worker_running():
            return

        if self.running_job_name.startswith("resx-"):
            elapsed_seconds = self._active_elapsed_seconds("resx")
            if elapsed_seconds is not None:
                self.resx_duration_var.set(format_duration(elapsed_seconds))
                self._refresh_resx_throughput(elapsed_seconds)
            return

        if self.running_job_name.startswith("cleanup-"):
            elapsed_seconds = self._active_elapsed_seconds("cleanup")
            if elapsed_seconds is not None:
                self.cleanup_duration_var.set(format_duration(elapsed_seconds))
                self._refresh_cleanup_throughput(elapsed_seconds)
            return

        elapsed_seconds = self._active_elapsed_seconds("operation")
        if elapsed_seconds is not None:
            self.operation_duration_var.set(format_duration(elapsed_seconds))
            self._refresh_operation_throughput(elapsed_seconds)

    def _refresh_operation_throughput(
        self,
        elapsed_seconds: float | None = None,
        *,
        force: bool = False,
    ) -> None:
        if self.pause_event.is_set() and not force:
            return
        if elapsed_seconds is None:
            elapsed_seconds = self._active_elapsed_seconds("operation")
            if elapsed_seconds is None:
                return
        self.operation_average_rate_var.set(
            format_average_rate(self.operation_last_processed_rows, elapsed_seconds)
        )
        self.operation_estimated_finish_var.set(
            format_estimated_finish(
                self.operation_last_processed_rows,
                self.operation_last_remaining_rows,
                elapsed_seconds,
            )
        )

    def _refresh_resx_throughput(
        self,
        elapsed_seconds: float | None = None,
        *,
        force: bool = False,
    ) -> None:
        if self.pause_event.is_set() and not force:
            return
        if elapsed_seconds is None:
            elapsed_seconds = self._active_elapsed_seconds("resx")
            if elapsed_seconds is None:
                return
        self.resx_average_rate_var.set(
            format_average_rate(self.resx_last_processed_entries, elapsed_seconds)
        )
        self.resx_estimated_finish_var.set(
            format_estimated_finish(
                self.resx_last_processed_entries,
                self.resx_last_remaining_entries,
                elapsed_seconds,
            )
        )

    def _refresh_cleanup_throughput(
        self,
        elapsed_seconds: float | None = None,
        *,
        force: bool = False,
    ) -> None:
        if self.pause_event.is_set() and not force:
            return
        if elapsed_seconds is None:
            elapsed_seconds = self._active_elapsed_seconds("cleanup")
            if elapsed_seconds is None:
                return
        self.cleanup_average_rate_var.set(
            format_average_rate(self.cleanup_last_processed_rows, elapsed_seconds)
        )
        self.cleanup_estimated_finish_var.set(
            format_estimated_finish(
                self.cleanup_last_processed_rows,
                self.cleanup_last_remaining_rows,
                elapsed_seconds,
            )
        )

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for button_name in (
            "run_test_button",
            "run_table_button",
            "run_all_button",
            "resx_scan_button",
            "resx_run_button",
            "cleanup_table_button",
            "cleanup_all_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state=state)

        for button_name in (
            "pause_button",
            "resx_pause_button",
            "cleanup_pause_button",
            "stop_button",
            "resx_stop_button",
            "cleanup_stop_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state="disabled")

        if running:
            if self.running_job_name.startswith("resx-"):
                active_controls = ("resx_pause_button", "resx_stop_button")
            elif self.running_job_name.startswith("cleanup-"):
                active_controls = ("cleanup_pause_button", "cleanup_stop_button")
            else:
                active_controls = ("pause_button", "stop_button")
            for button_name in active_controls:
                button = getattr(self, button_name, None)
                if button is not None:
                    button.configure(state="normal")

        if not running:
            self.pause_event.clear()
            self._set_pause_controls(paused=False)

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


def show_copyable_message(
    parent: Tk,
    title: str,
    message: str,
    kind: str,
) -> None:
    dialog = build_copyable_dialog(parent, title, message, kind)
    buttons = ttk.Frame(dialog, style="App.TFrame")
    buttons.grid(row=2, column=0, sticky="e", padx=14, pady=(0, 14))

    ttk.Button(
        buttons,
        text="Copy",
        command=lambda: copy_to_clipboard(parent, message),
    ).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(
        buttons,
        text="OK",
        command=dialog.destroy,
        style="Accent.TButton",
    ).grid(row=0, column=1)

    show_modal_dialog(parent, dialog)


def ask_copyable_yes_no(parent: Tk, title: str, message: str) -> bool:
    result = BooleanVar(value=False)
    dialog = build_copyable_dialog(parent, title, message, "question")
    buttons = ttk.Frame(dialog, style="App.TFrame")
    buttons.grid(row=2, column=0, sticky="e", padx=14, pady=(0, 14))

    def close_with(value: bool) -> None:
        result.set(value)
        dialog.destroy()

    ttk.Button(
        buttons,
        text="Copy",
        command=lambda: copy_to_clipboard(parent, message),
    ).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(
        buttons,
        text="No",
        command=lambda: close_with(False),
    ).grid(row=0, column=1, padx=(0, 8))
    ttk.Button(
        buttons,
        text="Yes",
        command=lambda: close_with(True),
        style="Accent.TButton",
    ).grid(row=0, column=2)

    show_modal_dialog(parent, dialog)
    return result.get()


def build_copyable_dialog(parent: Tk, title: str, message: str, kind: str) -> Toplevel:
    dialog = Toplevel(parent)
    dialog.title(title)
    dialog.configure(background=BG_COLOR)
    dialog.minsize(520, 220)
    dialog.resizable(True, True)
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(1, weight=1)

    heading = ttk.Frame(dialog, padding=(14, 14, 14, 8), style="App.TFrame")
    heading.grid(row=0, column=0, sticky="ew")
    heading.columnconfigure(0, weight=1)
    ttk.Label(
        heading,
        text=title,
        style="Header.TLabel",
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(
        heading,
        text=copyable_dialog_kind_label(kind),
        style="SubHeader.TLabel",
    ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    text_height = min(max(message.count("\n") + 4, 8), 18)
    text = scrolledtext.ScrolledText(
        dialog,
        width=84,
        height=text_height,
        wrap="word",
        bg="#ffffff",
        fg=TEXT_COLOR,
        insertbackground=TEXT_COLOR,
        selectbackground="#bfdbfe",
        relief="solid",
        borderwidth=1,
        padx=10,
        pady=10,
        font=("TkDefaultFont", 10),
    )
    configure_auto_hide_text_scrollbar(text)
    text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
    text.insert("1.0", message)
    text.configure(state="disabled")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    return dialog


def show_modal_dialog(parent: Tk, dialog: Toplevel) -> None:
    dialog.transient(parent)
    dialog.update_idletasks()
    center_dialog(parent, dialog)
    try:
        dialog.grab_set()
    except TclError:
        pass
    dialog.focus_set()
    parent.wait_window(dialog)


def center_dialog(parent: Tk, dialog: Toplevel) -> None:
    parent.update_idletasks()
    dialog.update_idletasks()
    width = dialog.winfo_width()
    height = dialog.winfo_height()
    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()
    x = parent_x + max((parent_width - width) // 2, 0)
    y = parent_y + max((parent_height - height) // 2, 0)
    dialog.geometry(f"+{x}+{y}")


def copy_to_clipboard(parent: Tk, text: str) -> None:
    parent.clipboard_clear()
    parent.clipboard_append(text)
    parent.update_idletasks()


def configure_readme_markdown_tags(text_widget: object) -> None:
    """Configure the visual styles used by the README Markdown renderer."""

    # Keep the renderer dependency-free so the packaged executable does not need
    # a separate Markdown package.  Text tags also work while the widget is
    # disabled, which means links remain clickable after the README is loaded.
    tag_configure = getattr(text_widget, "tag_configure")
    tag_configure(
        "readme-strong",
        font=("TkDefaultFont", 10, "bold"),
    )
    tag_configure(
        "readme-emphasis",
        font=("TkDefaultFont", 10, "italic"),
    )
    tag_configure(
        "readme-strikethrough",
        overstrike=True,
        foreground=MUTED_TEXT_COLOR,
    )
    tag_configure(
        "readme-inline-code",
        font=("TkFixedFont", 10),
        background="#eef2f6",
        foreground="#b42318",
    )
    tag_configure(
        "readme-code-block",
        font=("TkFixedFont", 10),
        background="#101828",
        foreground="#e4e7ec",
        lmargin1=12,
        lmargin2=12,
        rmargin=12,
    )
    tag_configure(
        "readme-blockquote",
        foreground=MUTED_TEXT_COLOR,
        lmargin1=18,
        lmargin2=30,
        rmargin=12,
    )
    tag_configure(
        "readme-list",
        lmargin1=18,
        lmargin2=34,
    )
    tag_configure(
        "readme-rule",
        foreground="#98a2b3",
        spacing1=6,
        spacing3=6,
    )
    tag_configure(
        "readme-table",
        font=("TkFixedFont", 10),
    )
    tag_configure(
        "readme-table-header",
        font=("TkFixedFont", 10, "bold"),
        foreground="#101828",
    )
    tag_configure(
        "readme-link",
        foreground="#175cd3",
        underline=True,
    )
    tag_configure(
        "readme-image",
        foreground=MUTED_TEXT_COLOR,
        font=("TkDefaultFont", 9, "italic"),
    )
    for level, size in enumerate((20, 17, 15, 13, 12, 11), start=1):
        tag_configure(
            f"readme-heading-{level}",
            font=("TkDefaultFont", size, "bold"),
            foreground="#101828",
            spacing1=10 if level <= 2 else 7,
            spacing3=5,
        )


def render_markdown(text_widget: object, markdown: str) -> None:
    """Render a useful Markdown subset into a Tk ``Text``/``ScrolledText``.

    The README is intentionally rendered without adding a runtime dependency.
    Headings, lists, quotes, fenced code, tables, links, and the common inline
    emphasis forms are supported.  Unsupported HTML is treated as formatting
    instead of being shown as raw markup where practical.
    """

    widget = text_widget
    widget.delete("1.0", "end")
    # Keep PhotoImage instances alive for as long as the rendered README is
    # visible.  Tk removes an image as soon as its Python object is collected.
    try:
        setattr(widget, "_readme_image_refs", [])
    except Exception:
        pass
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    link_index = [0]
    in_fence = False
    fence_marker = ""
    line_index = 0

    while line_index < len(lines):
        original_line = lines[line_index]
        if in_fence:
            closing_marker = _readme_fence_marker(original_line)
            if closing_marker and closing_marker[0] == fence_marker:
                in_fence = False
                fence_marker = ""
            else:
                # Do not normalize HTML or Markdown inside a code block.
                widget.insert("end", original_line + "\n", ("readme-code-block",))
            line_index += 1
            continue

        line = _normalize_readme_html_line(original_line)
        if line is None:
            line_index += 1
            continue

        fence_marker_match = _readme_fence_marker(line)
        if fence_marker_match:
            in_fence = True
            fence_marker = fence_marker_match[0]
            line_index += 1
            continue

        table_end = _readme_table_end(lines, line_index)
        if table_end is not None:
            _render_readme_table(widget, lines[line_index:table_end], link_index)
            line_index = table_end
            continue

        heading_match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading_match:
            level = len(heading_match.group(1))
            _insert_readme_inline(
                widget,
                heading_match.group(2),
                (f"readme-heading-{level}",),
                link_index,
            )
            widget.insert("end", "\n")
            line_index += 1
            continue

        if _is_readme_horizontal_rule(line):
            widget.insert("end", "────────────────────────────\n", ("readme-rule",))
            line_index += 1
            continue

        quote_match = re.match(r"^\s*((?:>\s*)+)(.*)$", line)
        if quote_match:
            quote_depth = quote_match.group(1).count(">")
            prefix = "│ " * max(quote_depth, 1)
            widget.insert("end", prefix, ("readme-blockquote",))
            _insert_readme_inline(
                widget,
                quote_match.group(2).strip(),
                ("readme-blockquote",),
                link_index,
            )
            widget.insert("end", "\n")
            line_index += 1
            continue

        unordered_match = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        ordered_match = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
        if unordered_match or ordered_match:
            if unordered_match:
                indentation, item = unordered_match.groups()
                marker = "• "
            else:
                indentation = ordered_match.group(1)
                item = ordered_match.group(2)
                number_match = re.match(r"^\s*(\d+)[.)]", line)
                marker = f"{number_match.group(1)}. " if number_match else "1. "
            prefix = " " * min(len(indentation), 12) + marker
            widget.insert("end", prefix, ("readme-list",))
            _insert_readme_inline(widget, item, ("readme-list",), link_index)
            widget.insert("end", "\n")
            line_index += 1
            continue

        _insert_readme_inline(widget, line, (), link_index)
        widget.insert("end", "\n")
        line_index += 1


def _insert_readme_inline(
    text_widget: object,
    value: str,
    inherited_tags: tuple[str, ...],
    link_index: list[int],
) -> None:
    """Insert inline Markdown while retaining block-level tag styling."""

    token_pattern = re.compile(
        r"(?P<image>!\[[^\]]*\]\([^)]*\))"
        r"|(?P<link>\[[^\]]+\]\([^)]*\))"
        r"|(?P<code>`[^`\n]+`)"
        r"|(?P<strong>\*\*[^*\n]+\*\*|__[^_\n]+__)"
        r"|(?P<strike>~~[^~\n]+~~)"
        r"|(?P<emphasis>(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_))"
    )
    position = 0
    for match in token_pattern.finditer(value):
        if match.start() > position:
            _insert_readme_plain(
                text_widget,
                value[position : match.start()],
                inherited_tags,
            )

        token = match.group(0)
        kind = match.lastgroup
        if kind == "image":
            image_match = re.match(r"!\[([^]]*)\]\(([^)]*)\)", token)
            if image_match:
                alt_text, target = image_match.groups()
                if not _insert_readme_image(text_widget, target):
                    _insert_readme_link_or_text(
                        text_widget,
                        alt_text or "Image",
                        target.strip(),
                        inherited_tags + ("readme-image",),
                        link_index,
                    )
        elif kind == "link":
            link_match = re.match(r"\[([^]]+)\]\(([^)]*)\)", token)
            if link_match:
                label, target = link_match.groups()
                target = target.strip().split(None, 1)[0] if target.strip() else ""
                tag_name = f"readme-link-{link_index[0]}"
                link_index[0] += 1
                _configure_readme_link(text_widget, tag_name, target)
                _insert_readme_inline(
                    text_widget,
                    label,
                    inherited_tags + ("readme-link", tag_name),
                    link_index,
                )
        elif kind == "code":
            _insert_readme_plain(
                text_widget,
                token[1:-1],
                inherited_tags + ("readme-inline-code",),
            )
        elif kind == "strong":
            _insert_readme_inline(
                text_widget,
                token[2:-2],
                inherited_tags + ("readme-strong",),
                link_index,
            )
        elif kind == "strike":
            _insert_readme_inline(
                text_widget,
                token[2:-2],
                inherited_tags + ("readme-strikethrough",),
                link_index,
            )
        elif kind == "emphasis":
            _insert_readme_inline(
                text_widget,
                token[1:-1],
                inherited_tags + ("readme-emphasis",),
                link_index,
            )
        position = match.end()

    if position < len(value):
        _insert_readme_plain(text_widget, value[position:], inherited_tags)


def _insert_readme_plain(
    text_widget: object,
    value: str,
    tags: tuple[str, ...],
) -> None:
    # Markdown backslash escapes should display their escaped character only.
    value = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>])", r"\1", value)
    text_widget.insert("end", value, tags)


def _insert_readme_link_or_text(
    text_widget: object,
    label: str,
    target: str,
    inherited_tags: tuple[str, ...],
    link_index: list[int],
) -> None:
    if not target:
        _insert_readme_plain(text_widget, label, inherited_tags)
        return
    tag_name = f"readme-link-{link_index[0]}"
    link_index[0] += 1
    _configure_readme_link(text_widget, tag_name, target)
    _insert_readme_plain(
        text_widget,
        label,
        inherited_tags + ("readme-link", tag_name),
    )


def _insert_readme_image(
    text_widget: object,
    target: str,
) -> bool:
    """Insert a local README image when Tk can load it.

    Remote images and images omitted from the packaged executable intentionally
    fall back to their alt text, so rendering never depends on network access.
    """

    image_create = getattr(text_widget, "image_create", None)
    if not callable(image_create):
        return False

    image_target = target.strip().split(None, 1)[0] if target.strip() else ""
    if not image_target or re.match(r"^[a-z][a-z0-9+.-]*://", image_target, re.I):
        return False

    image_path = Path(image_target)
    if not image_path.is_absolute():
        image_path = README_PATH.parent / image_path
    if not image_path.is_file():
        return False

    try:
        image = PhotoImage(master=text_widget, file=str(image_path))
        # The source logo is square and intentionally has a 170px display size
        # in README.md.  Other local screenshots are kept readable but bounded.
        max_width = 180 if image_path.name.lower() == "app_logo.png" else 720
        image_width = int(image.width())
        if image_width > max_width:
            scale = max((image_width + max_width - 1) // max_width, 1)
            image = image.subsample(scale, scale)

        image_create("end", image=image, padx=2, pady=2)
        image_refs = getattr(text_widget, "_readme_image_refs", None)
        if image_refs is None:
            image_refs = []
            setattr(text_widget, "_readme_image_refs", image_refs)
        image_refs.append(image)
        return True
    except (TclError, OSError, TypeError, ValueError):
        return False


def _configure_readme_link(text_widget: object, tag_name: str, target: str) -> None:
    if not target:
        return
    text_widget.tag_bind(
        tag_name,
        "<Button-1>",
        lambda _event, url=target: _open_readme_link(url),
    )
    text_widget.tag_bind(
        tag_name,
        "<Enter>",
        lambda _event: text_widget.configure(cursor="hand2"),
    )
    text_widget.tag_bind(
        tag_name,
        "<Leave>",
        lambda _event: text_widget.configure(cursor="xterm"),
    )


def _open_readme_link(target: str) -> str:
    try:
        webbrowser.open_new_tab(target)
    except Exception:
        pass
    return "break"


def _normalize_readme_html_line(line: str) -> str | None:
    """Convert the small amount of HTML used by README into Markdown-like text."""

    stripped = line.strip()
    if re.fullmatch(r"</?(?:p|div|section|center)(?:\s[^>]*)?>", stripped, re.I):
        return None

    heading_match = re.fullmatch(
        r"<h([1-6])(?:\s[^>]*)?>(.*?)</h\1>",
        stripped,
        re.I,
    )
    if heading_match:
        return f"{'#' * int(heading_match.group(1))} {heading_match.group(2).strip()}"

    image_match = re.fullmatch(r"<img\s+([^>]*)/?>", stripped, re.I)
    if image_match:
        attributes = image_match.group(1)
        alt_match = re.search(r"\balt\s*=\s*['\"]([^'\"]*)['\"]", attributes, re.I)
        src_match = re.search(r"\bsrc\s*=\s*['\"]([^'\"]*)['\"]", attributes, re.I)
        alt_text = alt_match.group(1) if alt_match else "Image"
        target = src_match.group(1) if src_match else ""
        return f"![{alt_text}]({target})" if target else f"**{alt_text}**"

    line = re.sub(
        r"<a\s+[^>]*href\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        lambda match: f"[{match.group(2)}]({match.group(1)})",
        line,
        flags=re.I,
    )
    line = re.sub(r"<strong(?:\s[^>]*)?>(.*?)</strong>", r"**\1**", line, flags=re.I)
    line = re.sub(r"<em(?:\s[^>]*)?>(.*?)</em>", r"*\1*", line, flags=re.I)
    line = re.sub(r"<br\s*/?>", "  ", line, flags=re.I)
    line = re.sub(r"</?(?:p|div|span|section|center)(?:\s[^>]*)?>", "", line, flags=re.I)
    return line.strip()


def _readme_table_end(lines: list[str], start: int) -> int | None:
    if start + 1 >= len(lines):
        return None
    if not _looks_like_readme_table_row(lines[start]):
        return None
    if not _is_readme_table_separator(lines[start + 1]):
        return None
    end = start + 2
    while end < len(lines) and _looks_like_readme_table_row(lines[end]):
        end += 1
    return end


def _is_readme_horizontal_rule(line: str) -> bool:
    value = line.strip()
    return bool(re.fullmatch(r"(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}", value))


def _readme_fence_marker(line: str) -> str | None:
    match = re.match(r"^\s*(`{3,}|~{3,})(?:\s*[^`]*)?$", line)
    return match.group(1) if match else None


def _looks_like_readme_table_row(line: str) -> bool:
    return not line.strip().startswith("```") and line.count("|") >= 1


def _is_readme_table_separator(line: str) -> bool:
    cells = _split_readme_table_row(line)
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) is not None for cell in cells
    )


def _split_readme_table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", value)]


def _readme_plain_cell(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"(`{1,3}|[*_~])", "", value)
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!>])", r"\1", value).strip()


def _render_readme_table(
    text_widget: object,
    source_lines: list[str],
    link_index: list[int],
) -> None:
    rows = [_split_readme_table_row(line) for line in source_lines]
    if len(rows) < 2:
        return
    separator = rows[1]
    data_rows = [rows[0], *rows[2:]]
    column_count = max(len(row) for row in data_rows)
    for row in data_rows:
        row.extend([""] * (column_count - len(row)))
    alignments: list[str] = []
    for cell in separator:
        stripped = cell.strip()
        if stripped.startswith(":") and stripped.endswith(":"):
            alignments.append("center")
        elif stripped.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")
    alignments.extend(["left"] * (column_count - len(alignments)))
    widths = [
        max(3, max(len(_readme_plain_cell(row[column])) for row in data_rows))
        for column in range(column_count)
    ]

    for row_index, row in enumerate(data_rows):
        row_tag = ("readme-table-header",) if row_index == 0 else ("readme-table",)
        for column, cell in enumerate(row):
            plain_cell = _readme_plain_cell(cell)
            width = widths[column]
            alignment = alignments[column]
            if alignment == "right":
                padding = " " * max(width - len(plain_cell), 0)
                cell = padding + cell
            elif alignment == "center":
                total_padding = max(width - len(plain_cell), 0)
                left_padding = total_padding // 2
                cell = " " * left_padding + cell + " " * (total_padding - left_padding)
            else:
                cell = cell + " " * max(width - len(plain_cell), 0)
            _insert_readme_inline(text_widget, cell, row_tag, link_index)
            if column < column_count - 1:
                text_widget.insert("end", "  ", row_tag)
        text_widget.insert("end", "\n")


def configure_auto_hide_text_scrollbar(text_widget: object) -> None:
    """Hide a ScrolledText scrollbar while all text fits in its viewport."""

    scrollbar = getattr(text_widget, "vbar")

    def update_scrollbar(*args: str) -> None:
        scrollbar.set(*args)
        try:
            first, last = float(args[0]), float(args[1])
        except (IndexError, ValueError):
            return
        if first <= 0.0 and last >= 1.0:
            scrollbar.pack_forget()
        else:
            scrollbar.pack(side="right", fill="y")

    text_widget.configure(yscrollcommand=update_scrollbar)
    scrollbar.pack_forget()


def copyable_dialog_kind_label(kind: str) -> str:
    labels = {
        "info": "Information",
        "error": "Error details",
        "warning": "Warning",
        "question": "Confirmation",
    }
    return labels.get(kind, "Message")


def maximize_window(root: Tk) -> None:
    try:
        root.state("zoomed")
        return
    except TclError:
        pass

    try:
        root.attributes("-zoomed", True)
        return
    except TclError:
        pass
    try:
        width = root.winfo_screenwidth()
        height = root.winfo_screenheight()
        root.geometry(f"{width}x{height}+0+0")
    except TclError:
        pass


def open_project_repository() -> bool:
    return bool(webbrowser.open_new_tab(PROJECT_REPOSITORY_URL))


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


def format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds: float) -> str:
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def mousewheel_units(event: object) -> int:
    event_num = getattr(event, "num", None)
    if event_num == 4:
        return -1
    if event_num == 5:
        return 1

    delta = int(getattr(event, "delta", 0))
    if delta == 0:
        return 0

    units = int(-delta / 120)
    if units == 0:
        return -1 if delta > 0 else 1
    return units


def can_scroll(view: tuple[float, float], units: int) -> bool:
    first, last = view
    if units == 0 or (first <= 0.0 and last >= 1.0):
        return False
    if units < 0 and first <= 0.0:
        return False
    if units > 0 and last >= 1.0:
        return False
    return True


def calculate_average_rate(processed_items: int, elapsed_seconds: float) -> float | None:
    if processed_items <= 0 or elapsed_seconds <= 0:
        return None
    return processed_items / elapsed_seconds


def format_average_rate(
    processed_items: int,
    elapsed_seconds: float,
    *,
    unit: str = "rec",
) -> str:
    average_rate = calculate_average_rate(processed_items, elapsed_seconds)
    if average_rate is None:
        return "-"
    return f"{average_rate:.2f} {unit}/s"


def estimate_remaining_seconds(
    processed_items: int,
    remaining_items: int,
    elapsed_seconds: float,
) -> float | None:
    if remaining_items <= 0:
        return 0.0 if processed_items > 0 else None

    average_rate = calculate_average_rate(processed_items, elapsed_seconds)
    if average_rate is None:
        return None
    return remaining_items / average_rate


def format_estimated_finish(
    processed_items: int,
    remaining_items: int,
    elapsed_seconds: float,
    *,
    now: datetime | None = None,
) -> str:
    remaining_seconds = estimate_remaining_seconds(
        processed_items,
        remaining_items,
        elapsed_seconds,
    )
    if remaining_seconds is None:
        return "-"

    current_time = now or datetime.now()
    return format_timestamp(current_time + timedelta(seconds=remaining_seconds))


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


def parse_language_ids(
    value: str | None,
    *,
    default: tuple[int, ...] = (2,),
) -> tuple[int, ...]:
    """Parse a comma-separated language queue while preserving its order."""

    parsed: list[int] = []
    for raw_value in str(value or "").split(","):
        try:
            language_id = int(raw_value.strip())
        except ValueError:
            continue
        if language_id in LANGUAGES and language_id not in parsed:
            parsed.append(language_id)
    return tuple(parsed) or tuple(default)


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
    title = rtl_display_text(language.title) if language.right_to_left else language.title
    return f"{language.id} - {title} ({language.code})"


def format_language_code_list(codes: tuple[str, ...] | list[str]) -> str:
    if not codes:
        return "-"
    titles_by_code = {language.code: language.title for language in LANGUAGES.values()}
    return ", ".join(
        f"{titles_by_code.get(code, code)} ({code})" for code in codes
    )


def language_id_from_label(label: str) -> int:
    raw_id = label.split("-", 1)[0].strip()
    return get_language(int(raw_id)).id


def database_target_label(target: str) -> str:
    return {
        GUEREH_DATABASE_TARGET: "Guereh database",
        RUGSTRUST_DATABASE_TARGET: "RugsTrust database",
        BOTH_DATABASE_TARGET: "Both databases",
    }.get(target, "Guereh database")


def database_target_from_label(label: str) -> str:
    return {
        "Guereh database": GUEREH_DATABASE_TARGET,
        "RugsTrust database": RUGSTRUST_DATABASE_TARGET,
        "Both databases": BOTH_DATABASE_TARGET,
    }.get(label, GUEREH_DATABASE_TARGET)


def merge_translation_summaries(
    summaries: list[TranslationSummary],
) -> TranslationSummary:
    """Combine sequential Guereh/RugsTrust GUI runs into one summary."""

    result = TranslationSummary()
    for summary in summaries:
        result.discovered_tables += summary.discovered_tables
        result.eligible_tables += summary.eligible_tables
        result.skipped_tables += summary.skipped_tables
        result.pending_rows += summary.pending_rows
        result.processed_rows += summary.processed_rows
        result.inserted_rows += summary.inserted_rows
        result.updated_rows += summary.updated_rows
        result.skipped_existing_rows += summary.skipped_existing_rows
        result.failed_rows += summary.failed_rows
        result.unfinished_records.extend(summary.unfinished_records)
    return result


def phase_label(phase: str) -> str:
    labels = {
        "discovered": "Discovering tables",
        "prepare": "Preparing tables",
        "prepared": "Ready",
        "running": "Running",
        "table-started": "Starting table",
        "table-finished": "Table finished",
        "table-failed": "Table failed",
        "finished": "Finished",
        "language-finished": "Language finished",
        "queue-finished": "All languages finished",
        "stopped": "Stopped",
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
        "language-finished": "Language finished",
        "queue-finished": "All languages finished",
        "stopped": "Stopped",
    }
    return labels.get(phase, phase)


def queue_progress_percent(snapshot: object) -> float:
    """Convert per-language queue metadata into a 0–100 progress value."""

    total = max(int(getattr(snapshot, "total_target_languages", 1)), 1)
    completed = max(int(getattr(snapshot, "completed_target_languages", 0)), 0)
    if completed >= total:
        return 100.0
    language_percent = min(
        max(float(getattr(snapshot, "language_percent", 0.0)), 0.0),
        100.0,
    )
    return min((completed + language_percent / 100.0) / total * 100.0, 100.0)


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
