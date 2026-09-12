from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationLimitError(RuntimeError):
    """Raised when a translation provider has rejected a request due to a limit.

    Providers may expose this condition as a HTTP status (for example, 429 or
    403) or as an error in their response body.  Keeping a provider-neutral
    exception lets the provider chain switch to its fallback without treating
    ordinary network/server errors as a quota event.
    """

    def __init__(
        self,
        message: str = "Translation provider limit reached.",
        *,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


def is_translation_limit_error(error: BaseException) -> bool:
    """Best-effort classification for provider quota/rate-limit failures."""

    if isinstance(error, TranslationLimitError):
        return True

    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    try:
        if int(status_code) in {403, 429}:
            return True
    except (TypeError, ValueError):
        pass

    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "too many requests",
            "rate limit",
            "rate-limit",
            "quota exceeded",
            "quota limit",
            "daily limit",
            "limit exceeded",
        )
    )


class Translator(ABC):
    @abstractmethod
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        raise NotImplementedError
