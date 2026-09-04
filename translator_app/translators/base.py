from __future__ import annotations

from abc import ABC, abstractmethod


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
