"""Database target helpers shared by CLI, GUI, and the scheduled job."""

from __future__ import annotations

from dataclasses import replace

from translator_app.config import RuntimeConfig, SqlServerConnectionSettings


GUEREH_DATABASE_TARGET = "guereh"
RUGSTRUST_DATABASE_TARGET = "rugstrust"
BOTH_DATABASE_TARGET = "both"
AUTO_DATABASE_TARGET = "auto"
RUGSTRUST_CERTIFICATION_TABLE = "RugCertificationLocalize"
RUGSTRUST_DEFAULT_SCHEMA = "dbo"


def normalize_database_target(value: str | None, *, rugstrust_available: bool) -> str:
    target = (value or AUTO_DATABASE_TARGET).strip().casefold()
    if target == AUTO_DATABASE_TARGET:
        return BOTH_DATABASE_TARGET if rugstrust_available else GUEREH_DATABASE_TARGET
    if target not in {
        GUEREH_DATABASE_TARGET,
        RUGSTRUST_DATABASE_TARGET,
        BOTH_DATABASE_TARGET,
    }:
        raise ValueError(
            "Unsupported database target. Use guereh, rugstrust, both, or auto."
        )
    return target


def rugstrust_runtime_config(
    config: RuntimeConfig,
    *,
    connection_settings: SqlServerConnectionSettings,
    schema_name: str | None = None,
) -> RuntimeConfig:
    """Create the fixed RugCertificationLocalize run from common settings."""

    normalized_schema = (schema_name or "").strip() or RUGSTRUST_DEFAULT_SCHEMA
    return replace(
        config,
        connection_string=connection_settings.build_connection_string(),
        schema_name=normalized_schema,
        table_name=RUGSTRUST_CERTIFICATION_TABLE,
        database_target=RUGSTRUST_DATABASE_TARGET,
        rugstrust_schema_name=normalized_schema,
        rugstrust_connection_settings=connection_settings,
    )
