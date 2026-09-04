from __future__ import annotations

import time
from urllib.parse import quote

from translator_app.translators.base import Translator


class GoogleFreeTranslator(Translator):
    """Translator using Google's public web endpoint.

    This endpoint does not need an API key, but it is not an official Google
    Cloud contract. For production guarantees, replace this provider with an
    official paid provider or a self-hosted LibreTranslate instance.
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
        response.raise_for_status()
        payload = response.json()

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


def build_debug_url(text: str, source_language: str, target_language: str) -> str:
    return (
        "https://translate.google.com/?sl="
        f"{source_language}&tl={target_language}&text={quote(text)}&op=translate"
    )
