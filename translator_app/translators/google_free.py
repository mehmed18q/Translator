from __future__ import annotations

import time
from urllib.parse import quote

from translator_app.translators.base import TranslationLimitError, Translator


class GoogleFreeTranslator(Translator):
    """Translator using Google's public web endpoint.

    This endpoint does not need an API key, but it is not an official Google
    Cloud contract. For production guarantees, replace this provider with an
    official paid provider or a self-hosted LibreTranslate instance. HTTP 403
    and 429 quota responses are surfaced as a limit so the automatic provider
    chain can switch to LibreTranslate.
    """

    endpoint = "https://translate.googleapis.com/translate_a/single"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        delay_seconds: float,
        max_chunk_chars: int = 4500,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds
        self.max_chunk_chars = max_chunk_chars

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        if not text or not text.strip():
            return text

        leading_whitespace = text[: len(text) - len(text.lstrip())]
        trailing_whitespace = text[len(text.rstrip()) :]
        body = text.strip()

        translated_chunks = [
            self._translate_chunk(chunk, source_language, target_language)
            for chunk in split_text(body, self.max_chunk_chars)
        ]
        return leading_whitespace + " ".join(translated_chunks) + trailing_whitespace

    def _translate_chunk(
        self, text: str, source_language: str, target_language: str
    ) -> str:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "requests is not installed. Run `pip install -r requirements.txt` first."
            ) from exc

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        response = requests.get(
            self.endpoint,
            params={
                "client": "gtx",
                "sl": source_language,
                "tl": target_language,
                "dt": "t",
                "q": text,
            },
            headers={
                "User-Agent": "Mozilla/5.0 database-localize-translator/0.1"
            },
            timeout=self.timeout_seconds,
        )
        status_code = response_status_code(response)
        if is_limit_response(response):
            raise TranslationLimitError(
                "Google translator rate limit or quota reached.",
                provider="google-free",
                status_code=status_code,
            )
        try:
            response.raise_for_status()
        except Exception as exc:
            # Some requests-compatible clients attach the response only to
            # the exception, so inspect it as a second chance to classify a
            # quota response correctly.
            error_response = getattr(exc, "response", None)
            error_status = response_status_code(error_response) or status_code
            if error_status in {403, 429}:
                raise TranslationLimitError(
                    "Google translator rate limit or quota reached.",
                    provider="google-free",
                    status_code=error_status,
                ) from exc
            raise
        payload = response.json()

        if is_limit_payload(payload):
            raise TranslationLimitError(
                "Google translator rate limit or quota reached.",
                provider="google-free",
                status_code=status_code,
            )
        if not isinstance(payload, list) or not payload:
            raise ValueError("Google translator returned an unreadable response.")

        translated_parts = []
        for segment in payload[0]:
            if isinstance(segment, list) and segment:
                translated_parts.append(str(segment[0]))

        result = "".join(translated_parts).strip()
        if not result:
            raise ValueError("Google translator returned an empty response.")
        return result


def split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut_index = remaining.rfind(" ", 0, max_chars)
        newline_index = remaining.rfind("\n", 0, max_chars)
        cut_index = max(cut_index, newline_index)
        if cut_index <= 0:
            cut_index = max_chars
        chunks.append(remaining[:cut_index].strip())
        remaining = remaining[cut_index:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks


def response_status_code(response: object) -> int | None:
    value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_limit_response(response: object) -> bool:
    status_code = response_status_code(response)
    if status_code in {403, 429}:
        return True

    # A few proxies return a generic 4xx while preserving the quota message in
    # the body.  Keep this deliberately narrow so a malformed response does
    # not unexpectedly disable Google for the rest of a job.
    if status_code is not None and not 400 <= status_code < 500:
        return False
    body = getattr(response, "text", "")
    return contains_limit_message(body)


def is_limit_payload(payload: object) -> bool:
    if isinstance(payload, dict):
        body = " ".join(str(value) for value in payload.values())
    elif isinstance(payload, list):
        # Keep translated text itself out of the check; only structured error
        # objects in an otherwise list-shaped response are candidates.
        error_objects = [item for item in payload if isinstance(item, dict)]
        if not error_objects:
            return False
        body = " ".join(
            str(value)
            for item in error_objects
            for value in item.values()
        )
    else:
        return False
    return contains_limit_message(body)


def contains_limit_message(value: object) -> bool:
    text = str(value).casefold()
    return any(
        marker in text
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


def build_debug_url(text: str, source_language: str, target_language: str) -> str:
    return (
        "https://translate.google.com/?sl="
        f"{source_language}&tl={target_language}&text={quote(text)}&op=translate"
    )
