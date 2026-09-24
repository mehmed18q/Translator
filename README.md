<p align="center">
  <img src="translator_app/assets/app_logo.png" alt="SQL Server Localization Translator logo" width="170">
</p>

<h1 align="center">SQL Server Localization Translator</h1>

<p align="center">
  A desktop and command-line tool for safely translating SQL Server localization records and .NET RESX resources.
</p>

<p align="center">
  <img alt="Version 2.4.1" src="https://img.shields.io/badge/version-2.4.1-2563eb">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Windows x64" src="https://img.shields.io/badge/Windows-x64-0078D4?logo=windows&logoColor=white">
  <img alt="Tkinter GUI" src="https://img.shields.io/badge/GUI-Tkinter-2ea44f">
  <img alt="91 tests passing" src="https://img.shields.io/badge/tests-91%20passing-2ea44f">
</p>

<p align="center">
  <strong>Version 2.4.1</strong> · <strong>2026</strong> · Created by <strong><a href="https://github.com/mehmed18q">Sadeq Kiumarsi</a></strong>
</p>

---

## Overview

SQL Server Localization Translator discovers localization tables, reads records in a source language, translates approved text fields, and creates or completes records in a destination language. It also translates standard `.resx` files used by .NET applications.

The Windows executable is self-contained: the destination computer does **not** need Python or any Python package installed. Microsoft ODBC Driver 18 for SQL Server remains a system prerequisite.

## Screenshots

The screenshots below show version 1.2.6 with the main application tabs and workflow provided by the Windows executable.

| Connection | Operation |
|:---:|:---:|
| ![Connection tab](docs/screenshots/connection.png) | ![Operation tab](docs/screenshots/operation.png) |

| Resources | Logs |
|:---:|:---:|
| ![Resources tab](docs/screenshots/resources.png) | ![Logs tab](docs/screenshots/logs.png) |

## Highlights

- Desktop GUI with `Connection`, `Operation`, `Resources`, `Logs`, `Cleanup`, and formatted `ReadMe` tabs.
- Automatic discovery of tables ending in `Localize` or `Localizes`.
- Special handling for `dbo.Resource` and `dbo.SystemMessages`.
- Inserts missing destination-language records.
- Updates only empty translatable fields in an existing destination record.
- Never overwrites a populated translation.
- Dry-run mode is enabled by default for safer review.
- Cleanup of localization rows whose textual content fields are all null, empty,
  or whitespace, with table selection, language queues, dry-run, and batched deletes.
- RESX scanning, creation, and translation with existing values preserved.
- Separate Pause/Resume and permanent Stop controls for database and RESX jobs.
- Live progress, retries, translation caching, and detailed logs.
- Checkbox-based destination language queues. Selected languages run sequentially,
  with per-language progress and completed/remaining counters for both database
  tables and RESX resources.
- Google-first translation with automatic fallback to LibreTranslate when
  Google reports a rate limit or quota response.
- Persistent settings and logs beside the packaged executable.
- Startup detection for Microsoft ODBC Driver 18.
- Validation and automatic repair of translated HTML before database or RESX writes.
- Visible application version in the window title and permanent footer.
- Clickable GitHub shortcut in the footer that opens the project repository.

## Windows executable

The ready-to-run application is produced in both distribution locations:

```text
dist\Translator.exe
output\Translator.exe
```

To use it on another Windows x64 computer:

1. Copy `Translator.exe` into a folder where the user has write permission.
2. Install **Microsoft ODBC Driver 18 for SQL Server** if it is not already installed.
3. Open `Translator.exe`.
4. Enter the SQL Server and LibreTranslate fallback settings in the `Connection` tab.
5. Use `Test Database` and, when applicable, `Test LibreTranslate`.
6. Save the settings and start with a dry run from the `Operation`, `Resources`, or `Cleanup` tab.

Python, `pip`, Tkinter, `requests`, and `pyodbc` are bundled into the executable. They do not need to be installed separately.

> [!IMPORTANT]
> Microsoft ODBC Driver 18 is not bundled because it is a Windows system driver. At startup, the application checks for the exact driver name `ODBC Driver 18 for SQL Server`. If it is missing, a warning explains what must be installed before connecting to SQL Server.

The driver can normally be installed from PowerShell with:

```powershell
winget install Microsoft.msodbcsql.18
```

## Files created beside the executable

The packaged application deliberately keeps user data outside its temporary embedded-Python directory:

```text
Translator.exe
.env
logs\
  translator_YYYYMMDD_HHMMSS.log
```

- `.env` stores settings saved from the GUI.
- `logs` contains one timestamped log file for each operation.
- Both locations remain stable after the application closes or Windows restarts.

Keep `Translator.exe` in a writable folder. The `.env` file can contain database credentials, so protect the folder and do not publish that file.

## Application tabs

| Tab          | Purpose |
|--------------|---|
| `Connection` | Configure and test SQL Server, configure the LibreTranslate fallback, set retry/runtime options, and save settings. |
| `Operation`  | Select a source and one or more checkbox-based destination languages, filter by schema/table, review and exclude tables before an all-table run, and pause, resume, or stop the active database job. |
| `Resources`  | Scan or translate `.resx` files into the selected destination language queue, review per-language progress and completed/remaining languages, and pause, resume, or stop the active resource job. |
| `Logs`       | Follow the current operation in real time and clear the on-screen log view. Clearing the view does not delete the log file. |
| `Cleanup`    | Select one or more languages and tables, preview empty localization rows in dry-run mode, and delete confirmed matches in bounded batches with live progress and Pause/Stop controls. |
| `ReadMe`     | Read this documentation inside the application as formatted Markdown. |

The footer is outside the tab area and always shows:

```text
Created by Sadeq Kiumarsi | 2026 | Version 2.4.1 | GitHub
```

When `Run All Tables` is selected, a modal review lists the tables ordered by
pending source-text characters (smallest first), with pending row counts. All
eligible tables are checked initially. Individual tables can be excluded, or
the `Select all` and `Clear all` controls can be used before choosing `Run selected`.

## Empty localization cleanup

The `Cleanup` tab removes a localization row only when every textual content
column in that row is empty. `NULL`, an empty string, and a string containing
only whitespace are treated as empty. Language columns, entity keys, primary
keys, and computed columns are excluded from this test, so metadata such as a
`Resource.Key` does not prevent an otherwise empty row from being found.

Cleanup can target one table or a reviewed selection of all discovered tables.
The selected language checkboxes form a sequential queue, and the table review
shows the matching row count across that queue. Dry-run is enabled by default
and performs no delete. With dry-run disabled, a confirmation is required and
matching rows are deleted in batches using the configured batch size.

## Pause, resume, and stop

Database and RESX operations provide two distinct controls:

- **Pause** finishes the row or resource key currently being handled, then keeps the same worker, connection, progress counters, and in-memory translation cache waiting. The button changes to **Resume**; selecting it continues from the next pending item without restarting the job.
- **Stop** permanently ends the current job. A stopped job cannot be resumed with the Resume button; start a new run if more work remains.

The application must remain open while a job is paused. An HTTP request or database command already in progress is allowed to finish before the pause checkpoint is reached. Pressing Stop while paused immediately releases the paused worker and ends the job safely.

Completed SQL rows and RESX file changes are not rolled back when Stop is selected. A later run discovers the remaining work and preserves destination values that were already written. Pause, resume, and stop transitions are recorded in the operation log.

## Safe translation behavior

Database processing follows these rules:

1. Discover eligible localization and special-case tables from SQL Server metadata.
2. Match source and destination records by entity key plus language ID.
3. Insert a translated record when the destination record does not exist.
4. When the destination exists, translate only its empty approved text columns.
5. Preserve every destination field that already has a value.

Only textual SQL columns whose names are approved in `translator_app/translatable_columns.py` are translated. Identity, computed, and rowversion columns are excluded from inserts. Foreign-key metadata is preferred for entity matching; the table name is used as a fallback convention when a suitable foreign key is unavailable.

HTML content is detected automatically. `HTMLContent` fields and values that look like HTML are sent to compatible providers with `format=html`; ordinary values use `format=text`. When an HTML value already contains directional markup (`dir`, `align`, `direction`, or `text-align`), those declarations are normalized for the target language: RTL languages use `dir="rtl"` and right alignment, while LTR languages use `dir="ltr"` and left alignment. Script and style contents are left untouched.

Every HTML response is validated before it is written to SQL Server or a RESX file. The source tag structure, attributes (apart from intentional target-direction normalization), comments, and non-translatable `script`/`style` content are preserved. Escaped HTML and Markdown code fences are normalized automatically. If the provider returns missing, changed, or unbalanced markup, the application translates the source text nodes separately and rebuilds them inside the original HTML structure. If a safe reconstruction is not possible, the item fails and malformed HTML is not stored.

### Special tables

| Table | Matching key | Translated value |
|---|---|---|
| `dbo.Resource` | `Key` | `Value` |
| `dbo.SystemMessages` | `SystemMessageStateId` + `MessageKey` | `Value` |

## Supported languages

| ID | Language | Code |
|---:|---|:---:|
| 1 | فارسی | `fa` |
| 2 | English | `en` |
| 3 | عربي | `ar` |
| 4 | Français | `fr` |
| 5 | 中国人 | `zh` |
| 6 | Русский | `ru` |

## Translation providers

The application automatically uses Google's public endpoint first. If Google
returns a rate-limit or quota response, the active job switches to
LibreTranslate and keeps using it for the remainder of that job. Configure a
self-hosted or remote LibreTranslate URL (and optional API key) in the
`Connection` tab or with `LIBRETRANSLATE_URL`.

The CLI still accepts `--provider google-free` and `--provider libretranslate`
for backwards compatibility. New runs should use the default `--provider auto`.
For controlled production use, a self-hosted LibreTranslate instance is
recommended.

## RESX translation

The `Resources` tab supports standard .NET resource naming:

- English uses a neutral resource such as `Resources.resx` or `Messages.resx`.
- Other languages use a culture suffix, such as `Resources.fa.resx`, `Resources.ar.resx`, or `Messages.ru.resx`.
- `Resources.resx`, `Messages.resx`, and `Message.resx` can be selected directly; extra base filenames are also supported.
- Existing destination keys with values are skipped.
- Empty source values are skipped.
- A missing destination file is created from the source structure so the RESX headers and schema are preserved.
- Format placeholders such as `{0}` are preserved during translation.
- Dry-run scans and reports pending entries without writing files.

## Logs

Every database or RESX operation creates a UTF-8 log file named:

```text
logs/translator_YYYYMMDD_HHMMSS.log
```

Logs include discovered and eligible tables/files, pending counts, row or key progress, inserted and updated records, skipped existing values, retries, failures, and full exception details. HTML checks are explicitly recorded as `HTML response validated`, `HTML response repaired`, or `HTML direction normalized`, including the table, column and row identifier—or the RESX filename and key—so every automatic repair can be traced. The final entry of every database, RESX, or cleanup log is an `END-OF-RUN UNFINISHED RECORDS REPORT`. It is sorted by table/file, language, and record/key; shows a status summary; and renders each failed, timed-out, stopped, excluded, skipped, or otherwise unprocessed item as a readable multi-line block. When exact identifiers are unavailable, the report shows the affected row or entry count. A successful run ends with an explicit zero-item completion report. In the GUI, the same operation messages appear live in the `Logs` tab.

For the packaged executable, logs are written beside `Translator.exe`. For a source checkout, relative log paths are resolved from the current working directory.

## Run from source

### Requirements

- Python 3.12
- Tkinter
- Microsoft ODBC Driver 18 for SQL Server
- Access to SQL Server
- Access to Google and, when Google is rate-limited, the configured LibreTranslate fallback

Create an environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux, install Tkinter, unixODBC, and Microsoft ODBC Driver 18 through your distribution before starting the application.

### Start the GUI

Running without arguments opens the GUI:

```bash
python main.py
```

The explicit form is also supported:

```bash
python main.py --gui
```

### Configure with `.env`

Copy `.env.example` to `.env`, then update the values you need. Database
connection strings are assembled internally from the Guereh/RugsTrust fields;
no raw connection-string setting is required.

```dotenv
GUEREH_SQLSERVER_DRIVER=ODBC Driver 18 for SQL Server
GUEREH_SQLSERVER_SERVER=localhost
GUEREH_SQLSERVER_DATABASE=YourDatabase
GUEREH_SQLSERVER_USERNAME=sa
GUEREH_SQLSERVER_PASSWORD=your_password
GUEREH_SQLSERVER_TRUSTED_CONNECTION=false
GUEREH_SQLSERVER_NO_ENCRYPT=false
GUEREH_SQLSERVER_TRUST_SERVER_CERTIFICATE=true

RUGSTRUST_SQLSERVER_DRIVER=ODBC Driver 18 for SQL Server
RUGSTRUST_SQLSERVER_SERVER=
RUGSTRUST_SQLSERVER_DATABASE=
RUGSTRUST_SQLSERVER_USERNAME=
RUGSTRUST_SQLSERVER_PASSWORD=
RUGSTRUST_SQLSERVER_TRUSTED_CONNECTION=false
RUGSTRUST_SQLSERVER_NO_ENCRYPT=false
RUGSTRUST_SQLSERVER_TRUST_SERVER_CERTIFICATE=true
RUGSTRUST_SQLSERVER_SCHEMA=dbo

LIBRETRANSLATE_URL=http://127.0.0.1:5000
LIBRETRANSLATE_API_KEY=
LOG_DIR=logs
CLEANUP_LANGUAGE_IDS=2
CLEANUP_DRY_RUN=true
```

## Command-line examples

The CLI uses dry-run unless `--execute` is explicitly supplied.

Preview Persian-to-English database work:

```bash
python main.py --cli --source-language-id 1 --target-language-id 2
```

Execute all eligible tables:

```bash
python main.py --cli --source-language-id 1 --target-language-id 2 --execute
```

Process one table only:

```bash
python main.py --cli --source-language-id 1 --target-language-id 2 \
  --schema dbo --table SiteMenuLocalize --execute
```

Run one test table before deciding whether to continue:

```bash
python main.py --cli --source-language-id 1 --target-language-id 2 \
  --test-table dbo.SiteMenuLocalize --execute
```

Use LibreTranslate only (legacy override):

```bash
python main.py --cli --provider libretranslate \
  --libretranslate-url http://localhost:5000 \
  --source-language-id 1 --target-language-id 2 --execute
```

Run `python main.py --cli --help` for the complete option list.

## Nightly translation and cleanup job

For a server, the independent non-interactive job can run without opening the
GUI or answering CLI prompts:

```bash
python -m translator_app.scheduled_job --env-file /opt/translator/job.env
```

The job always performs these phases in order:

1. translate the configured source language into the destination language queue;
2. after the translation phase returns, scan and clean empty rows for the
   configured cleanup-language queue.

One operating-system file lock covers both phases. If an earlier scheduled run
is still active, the next invocation waits for it by default. Set
`JOB_LOCK_TIMEOUT_SECONDS=0` when a cron invocation should exit immediately
instead (exit code `2`) rather than wait. A crashed process does not leave a
stale lock because the OS releases the lock automatically.

Copy [`job.env.example`](job.env.example) to a protected location and fill in
the SQL Server credentials. `SOURCE_LANGUAGE_ID`, `TARGET_LANGUAGE_IDS`,
`CLEANUP_LANGUAGE_IDS`, `JOB_SCHEMA`, and optional `JOB_TABLE` control the
operation. Omitting `JOB_TABLE` processes all eligible `Localize`/
`Localizes` tables. The job executes writes by default; set `JOB_DRY_RUN=true`
or pass `--dry-run` for a preview.

The job is non-interactive and automatic. With `DATABASE_TARGET=auto` (the
default for the scheduled job), setting the `RUGSTRUST_SQLSERVER_SERVER` and
`RUGSTRUST_SQLSERVER_DATABASE` values causes each run to process the Guereh database first and then the fixed
`dbo.RugCertificationLocalize` table in the RugsTrust database. Leave those settings
empty to run only the Guereh database. Translation always finishes before
cleanup, and the process lock prevents overlapping runs.

For a nightly run at local midnight, use the example in
[`deploy/translation-cleanup.cron.example`](deploy/translation-cleanup.cron.example):

```cron
0 0 * * * cd /opt/translator && /opt/translator/.venv/bin/python -m translator_app.scheduled_job --env-file /opt/translator/job.env >> /opt/translator/translation-cleanup-cron.log 2>&1
```

The GUI and interactive CLI can also select the Guereh database, RugsTrust database,
or both; they remain independent of the scheduled job.

## Persisted translation failures and retry

The job/application automatically creates this table during its first execute
run on each configured database. To create it manually instead, execute
[`sql/create_translation_failure_log.sql`](sql/create_translation_failure_log.sql)
once on the same SQL Server database used by the application. The table is
`dbo.TranslatorTranslationFailureLog`; it stores the source row, entity key,
language pair, missing columns, reason, and retry count. Credentials are not
stored in the table: the existing application/job database connection is used.

For example, with `sqlcmd` and SQL authentication:

```bash
sqlcmd -S "$GUEREH_SQLSERVER_SERVER" -d "$GUEREH_SQLSERVER_DATABASE" \
  -U "$GUEREH_SQLSERVER_USERNAME" -P "$GUEREH_SQLSERVER_PASSWORD" \
  -i sql/create_translation_failure_log.sql
```

If `DATABASE_TARGET=auto` or `both` is used, each database keeps its own
failure records; the warm-up runs separately for Guereh and RugsTrust.

When a database translation row fails during an execute-mode run, it is
upserted into this table. At the beginning of each target-language run, the
application and scheduled job read the unresolved rows first. A successful
retry writes the translation and removes the log row. A failed retry updates
the reason/attempt count and skips that row for the rest of the current run;
the normal queue continues with other rows. `--dry-run` never writes, updates,
or deletes failure-log rows.

## Build the Windows executable

From Windows PowerShell in the project root:

```powershell
.\build_windows.ps1
```

The script creates a Python 3.12 build environment when needed, installs build dependencies, runs the test suite, and packages the GUI with PyInstaller. The result is written to:

```text
output\Translator.exe
```

The GitHub Actions workflow named **Build Windows executable** can also be started manually. It publishes the `Translator-Windows-x64` artifact after a successful test and build run.

## Technology

| Component | Technology |
|---|---|
| Primary language | Python 3.12 |
| Desktop interface | Tkinter / ttk |
| SQL Server access | pyodbc + Microsoft ODBC Driver 18 |
| HTTP translation clients | Requests and Python standard library |
| Windows packaging | PyInstaller |
| Configuration | `.env`-style key/value file |
| Testing | Python `unittest` |

## Tests

Run the complete test suite with:

```bash
python -m unittest discover -s tests -p "test*.py"
```

The current release passes **91 tests**.

## Versioning

The single source of truth for the application version is `translator_app/__init__.py`:

```python
__version__ = "2.4.1"
```

Update this value for future releases. The GUI window title and permanent footer read it automatically, making the version visible to every user.

## Author

Designed and developed by **[Sadeq Kiumarsi](https://github.com/mehmed18q)** in **2026**.

Project repository: **[github.com/mehmed18q/Translator](https://github.com/mehmed18q/Translator)**
