from __future__ import annotations

import time

from translator_app.translators.base import Translator


class LibreTranslateTranslator(Translator):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        delay_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "requests is not installed. Run `pip install -r requirements.txt` first."
            ) from exc

        if not text or not text.strip():
            return text

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        payload: dict[str, str] = {
            "q": text,
            "source": source_language,
            "target": target_language,
            "format": normalize_text_format(text_format),
        }
        if self.api_key:
            payload["api_key"] = self.api_key

        response = requests.post(
            f"{self.base_url}/translate",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        translated_text = data.get("translatedText")
        if not translated_text:
            raise ValueError("LibreTranslate returned an empty response.")
        return str(translated_text)


def normalize_text_format(text_format: str) -> str:
    if text_format.casefold() == "html":
        return "html"
    return "text"
