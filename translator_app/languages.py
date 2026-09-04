from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageOption:
    id: int
    code: str
    name_fa: str


LANGUAGES: dict[int, LanguageOption] = {
    1: LanguageOption(1, "fa", "فارسی"),
    2: LanguageOption(2, "en", "انگلیسی"),
    3: LanguageOption(3, "ar", "عربی"),
    4: LanguageOption(4, "fr", "فرانسه"),
    5: LanguageOption(5, "zh", "چینی"),
    6: LanguageOption(6, "ru", "روسی"),
}


def get_language(language_id: int) -> LanguageOption:
    try:
        return LANGUAGES[language_id]
    except KeyError as exc:
        valid_ids = ", ".join(str(item) for item in sorted(LANGUAGES))
        raise ValueError(f"شناسه زبان نامعتبر است. گزینه‌های معتبر: {valid_ids}") from exc


def format_language_options() -> str:
    return "\n".join(
        f"{language.id}. {language.name_fa} ({language.code})"
        for language in LANGUAGES.values()
    )
