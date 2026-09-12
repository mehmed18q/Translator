from __future__ import annotations

import logging
import threading

from translator_app.translators.base import (
    TranslationLimitError,
    Translator,
    is_translation_limit_error,
)


class GoogleThenLibreTranslator(Translator):
    """Use Google until it reports a limit, then use LibreTranslate.

    The switch is intentionally sticky for the lifetime of this translator.
    A translation job can issue many requests, and trying Google again after a
    quota response would only produce more failures (and consume retry slots).
    """

    def __init__(
        self,
        *,
        google: Translator,
        libretranslate: Translator | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.google = google
        self.libretranslate = libretranslate
        self.logger = logger or logging.getLogger(__name__)
        self._fallback_active = False
        self._state_lock = threading.Lock()

    @property
    def fallback_active(self) -> bool:
        """Whether this instance has permanently switched to LibreTranslate."""

        with self._state_lock:
            return self._fallback_active

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        if self.fallback_active:
            return self._translate_with_libre(
                text,
                source_language,
                target_language,
                text_format=text_format,
            )

        try:
            return self.google.translate(
                text,
                source_language,
                target_language,
                text_format=text_format,
            )
        except Exception as exc:
            if not is_translation_limit_error(exc):
                raise
            limit_error = (
                exc
                if isinstance(exc, TranslationLimitError)
                else TranslationLimitError(
                    str(exc),
                    status_code=getattr(
                        getattr(exc, "response", None),
                        "status_code",
                        None,
                    ),
                )
            )
            with self._state_lock:
                first_switch = not self._fallback_active
                self._fallback_active = True

            if first_switch:
                self.logger.warning(
                    "Google translation limit reached; switching to LibreTranslate%s.",
                    f" (status={limit_error.status_code})"
                    if limit_error.status_code
                    else "",
                )

            return self._translate_with_libre(
                text,
                source_language,
                target_language,
                text_format=text_format,
            )

    def _translate_with_libre(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str,
    ) -> str:
        if self.libretranslate is None:
            raise ValueError(
                "Google translation limit reached, but LibreTranslate is not "
                "configured. Set LIBRETRANSLATE_URL or pass "
                "--libretranslate-url."
            )
        return self.libretranslate.translate(
            text,
            source_language,
            target_language,
            text_format=text_format,
        )


# A shorter generic name is useful to callers that do not need to mention the
# concrete providers, while retaining the descriptive class above for logs and
# documentation.
FallbackTranslator = GoogleThenLibreTranslator
