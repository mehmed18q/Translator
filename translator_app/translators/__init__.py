from __future__ import annotations

from translator_app.config import RuntimeConfig
from translator_app.translators.base import Translator
from translator_app.translators.google_free import GoogleFreeTranslator
from translator_app.translators.libretranslate import LibreTranslateTranslator


def create_translator(config: RuntimeConfig) -> Translator:
    if config.translator_provider == "google-free":
        return GoogleFreeTranslator(
            timeout_seconds=config.request_timeout_seconds,
            delay_seconds=config.request_delay_seconds,
        )

    if config.translator_provider == "libretranslate":
        if not config.libretranslate_url:
            raise ValueError(
                "When provider=libretranslate, configure LIBRETRANSLATE_URL "
                "or pass --libretranslate-url."
            )
        return LibreTranslateTranslator(
            base_url=config.libretranslate_url,
            api_key=config.libretranslate_api_key,
            timeout_seconds=config.request_timeout_seconds,
            delay_seconds=config.request_delay_seconds,
        )

    raise ValueError(f"Unsupported translator provider: {config.translator_provider}")
