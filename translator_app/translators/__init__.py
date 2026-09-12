from __future__ import annotations

import logging

from translator_app.config import RuntimeConfig
from translator_app.translators.base import TranslationLimitError, Translator
from translator_app.translators.fallback import (
    FallbackTranslator,
    GoogleThenLibreTranslator,
)
from translator_app.translators.google_free import GoogleFreeTranslator
from translator_app.translators.libretranslate import LibreTranslateTranslator


def create_translator(
    config: RuntimeConfig,
    *,
    logger: logging.Logger | None = None,
) -> Translator:
    provider = (config.translator_provider or "auto").strip().casefold()

    if provider in {"auto", "google-then-libre", "google_then_libre"}:
        google = GoogleFreeTranslator(
            timeout_seconds=config.request_timeout_seconds,
            delay_seconds=config.request_delay_seconds,
        )
        libretranslate = _create_libretranslate(config)
        return GoogleThenLibreTranslator(
            google=google,
            libretranslate=libretranslate,
            logger=logger,
        )

    # Keep explicit provider values working for existing CLI scripts and .env
    # files. New GUI/CLI configurations use ``auto`` above.
    if provider == "google-free":
        return GoogleFreeTranslator(
            timeout_seconds=config.request_timeout_seconds,
            delay_seconds=config.request_delay_seconds,
        )

    if provider == "libretranslate":
        return _create_libretranslate(config, required=True)

    raise ValueError(f"Unsupported translator provider: {config.translator_provider}")


def _create_libretranslate(
    config: RuntimeConfig,
    *,
    required: bool = False,
) -> LibreTranslateTranslator | None:
    if not config.libretranslate_url:
        if required:
            raise ValueError(
                "When provider=libretranslate, configure LIBRETRANSLATE_URL "
                "or pass --libretranslate-url."
            )
        return None
    return LibreTranslateTranslator(
        base_url=config.libretranslate_url,
        api_key=config.libretranslate_api_key,
        timeout_seconds=config.request_timeout_seconds,
        delay_seconds=config.request_delay_seconds,
    )


__all__ = [
    "FallbackTranslator",
    "GoogleThenLibreTranslator",
    "GoogleFreeTranslator",
    "LibreTranslateTranslator",
    "TranslationLimitError",
    "create_translator",
]
