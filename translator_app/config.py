from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from translator_app.languages import LanguageOption


@dataclass(frozen=True)
class SqlServerConnectionSettings:
    connection_string: str | None
    driver: str
    server: str
    database: str
    username: str | None
    password: str | None
    trusted_connection: bool
    encrypt: bool
    trust_server_certificate: bool

    def build_connection_string(self) -> str:
        if self.connection_string:
            return self.connection_string

        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={self.database}",
        ]

        has_explicit_credentials = bool(self.username) or self.password not in {None, ""}

        if has_explicit_credentials:
            if not self.username:
                raise ValueError("SQL Server username is not configured.")
            if self.password is None:
                raise ValueError("SQL Server password is not configured.")
            parts.extend([f"UID={self.username}", f"PWD={self.password}"])
        elif self.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            raise ValueError(
                "Configure SQL Server username/password or enable Trusted Connection."
            )

        parts.append(f"Encrypt={'yes' if self.encrypt else 'no'}")
        parts.append(
            "TrustServerCertificate="
            f"{'yes' if self.trust_server_certificate else 'no'}"
        )
        return ";".join(parts)


@dataclass(frozen=True)
class RetrySettings:
    attempts: int
    initial_delay_seconds: float
    backoff_factor: float


@dataclass(frozen=True)
class RuntimeConfig:
    connection_string: str
    source_language: LanguageOption
    target_language: LanguageOption
    dry_run: bool
    schema_name: str | None
    table_name: str | None
    batch_size: int
    progress_every: int
    translator_provider: str
    request_timeout_seconds: float
    request_delay_seconds: float
    libretranslate_url: str | None
    libretranslate_api_key: str | None
    log_dir: Path
    retry: RetrySettings
