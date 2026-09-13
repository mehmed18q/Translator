from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata


@dataclass(frozen=True)
class LanguageOption:
    id: int
    code: str
    title: str
    right_to_left: bool = False


@lru_cache(maxsize=1)
def _arabic_presentation_forms() -> dict[str, dict[str, str]]:
    """Build a base-letter to Arabic presentation-form lookup table."""

    forms: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r"^(ARABIC LETTER .+?) (ISOLATED|FINAL|INITIAL|MEDIAL) FORM$"
    )
    for codepoint in range(0xFB50, 0xFEFF):
        character = chr(codepoint)
        name = unicodedata.name(character, "")
        match = pattern.match(name)
        if match is None:
            continue
        try:
            base_character = unicodedata.lookup(match.group(1))
        except KeyError:
            continue
        forms.setdefault(base_character, {})[match.group(2).casefold()] = character
    return forms


def shape_rtl_text(value: str) -> str:
    """Pre-shape Arabic-script letters for Tk widgets.

    Tk's text renderer on some platforms applies bidirectional ordering but
    does not perform Arabic joining.  Replacing base letters with their
    Unicode presentation forms keeps the logical text unchanged while making
    connected glyphs render correctly in comboboxes and other simple widgets.
    """

    if not value:
        return value

    forms = _arabic_presentation_forms()
    characters = list(value)
    original = tuple(characters)

    for index, character in enumerate(original):
        character_forms = forms.get(character)
        if not character_forms:
            continue

        previous_index = _joining_neighbor(original, index, direction=-1)
        next_index = _joining_neighbor(original, index, direction=1)
        joins_previous = (
            previous_index is not None
            and _can_join_next(forms.get(original[previous_index], {}))
            and _can_join_previous(character_forms)
        )
        joins_next = (
            next_index is not None
            and _can_join_next(character_forms)
            and _can_join_previous(forms.get(original[next_index], {}))
        )

        if joins_previous and joins_next:
            form_name = "medial"
        elif joins_previous:
            form_name = "final"
        elif joins_next:
            form_name = "initial"
        else:
            form_name = "isolated"
        characters[index] = character_forms.get(form_name, character)

    return "".join(characters)


def rtl_display_text(value: str) -> str:
    """Return a Tk-friendly visual representation of an RTL string.

    Tk versions used by the packaged application do not consistently apply
    Arabic shaping and bidirectional reordering.  Shape the letters first,
    then reverse their visual clusters so a left-to-right widget displays the
    same appearance as a proper RTL text renderer.
    """

    shaped = shape_rtl_text(value)
    if not shaped:
        return shaped

    presentation_characters = {
        character
        for character_forms in _arabic_presentation_forms().values()
        for character in character_forms.values()
    }
    if not any(character in presentation_characters for character in shaped):
        return value

    def is_presentation_character(character: str) -> bool:
        return character in presentation_characters

    output: list[str] = []
    clusters: list[str] = []

    def flush_rtl_clusters() -> None:
        if clusters:
            output.extend(reversed(clusters))
            clusters.clear()

    for character in shaped:
        if is_presentation_character(character):
            clusters.append(character)
        elif unicodedata.combining(character) and clusters:
            clusters[-1] += character
        else:
            flush_rtl_clusters()
            output.append(character)
    flush_rtl_clusters()
    return "".join(output)


def _joining_neighbor(
    characters: tuple[str, ...],
    index: int,
    *,
    direction: int,
) -> int | None:
    cursor = index + direction
    while 0 <= cursor < len(characters):
        character = characters[cursor]
        if unicodedata.combining(character):
            cursor += direction
            continue
        if character not in _arabic_presentation_forms():
            return None
        return cursor
    return None


def _can_join_previous(forms: dict[str, str]) -> bool:
    return "final" in forms or "medial" in forms


def _can_join_next(forms: dict[str, str]) -> bool:
    return "initial" in forms or "medial" in forms


LANGUAGES: dict[int, LanguageOption] = {
    1: LanguageOption(1, "fa", "فارسی", True),
    2: LanguageOption(2, "en", "English"),
    3: LanguageOption(3, "ar", "عربي", True),
    4: LanguageOption(4, "fr", "Français"),
    5: LanguageOption(5, "zh", "中国人"),
    6: LanguageOption(6, "ru", "Русский"),
}


def get_language(language_id: int) -> LanguageOption:
    try:
        return LANGUAGES[language_id]
    except KeyError as exc:
        valid_ids = ", ".join(str(item) for item in sorted(LANGUAGES))
        raise ValueError(f"Invalid language ID. Valid options: {valid_ids}") from exc


def format_language_options() -> str:
    return "\n".join(
        f"{language.id}. {language.title} ({language.code})"
        for language in LANGUAGES.values()
    )
