from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser


VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
NON_TRANSLATABLE_ELEMENTS = frozenset({"script", "style"})
MARKDOWN_HTML_FENCE = re.compile(
    r"^\s*```(?:html)?\s*(.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)
EDGE_WHITESPACE = re.compile(r"^(\s*)(.*?)(\s*)$", re.DOTALL)


@dataclass(frozen=True)
class ParsedHtml:
    markup: tuple[str, ...]
    skeleton: tuple[tuple[str, str], ...]
    text_slots: tuple[str, ...]
    protected_slots: tuple[bool, ...]
    balanced: bool

    @property
    def has_html_tags(self) -> bool:
        return any(kind in {"start", "end", "void"} for kind, _name in self.skeleton)

    def render(self, text_slots: tuple[str, ...]) -> str:
        if len(text_slots) != len(self.markup) + 1:
            raise ValueError("HTML text slots do not match the source structure.")

        parts: list[str] = []
        for index, markup in enumerate(self.markup):
            parts.append(text_slots[index])
            parts.append(markup)
        parts.append(text_slots[-1])
        return "".join(parts)


@dataclass(frozen=True)
class HtmlTranslationResult:
    value: str
    repaired: bool
    reason: str | None = None


class _HtmlStructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.markup: list[str] = []
        self.skeleton: list[tuple[str, str]] = []
        self.text_slots: list[str] = [""]
        self.protected_slots: list[bool] = [False]
        self.open_elements: list[str] = []
        self.balanced = True

    def _is_protected(self) -> bool:
        return any(tag in NON_TRANSLATABLE_ELEMENTS for tag in self.open_elements)

    def _add_markup(self, raw: str, kind: str, name: str = "") -> None:
        self.markup.append(raw)
        self.skeleton.append((kind, name.casefold()))
        self.text_slots.append("")
        self.protected_slots.append(self._is_protected())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        raw = self.get_starttag_text() or f"<{tag}>"
        if normalized in VOID_ELEMENTS:
            self._add_markup(raw, "void", normalized)
            return
        self.open_elements.append(normalized)
        self._add_markup(raw, "start", normalized)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.casefold()
        self._add_markup(self.get_starttag_text() or f"<{tag}/>", "void", normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in VOID_ELEMENTS:
            self.balanced = False
        elif not self.open_elements or self.open_elements[-1] != normalized:
            self.balanced = False
            if normalized in self.open_elements:
                reverse_index = self.open_elements[::-1].index(normalized)
                del self.open_elements[len(self.open_elements) - reverse_index - 1 :]
        else:
            self.open_elements.pop()
        self._add_markup(f"</{tag}>", "end", normalized)

    def handle_data(self, data: str) -> None:
        self.text_slots[-1] += data

    def handle_entityref(self, name: str) -> None:
        self.text_slots[-1] += f"&{name};"

    def handle_charref(self, name: str) -> None:
        self.text_slots[-1] += f"&#{name};"

    def handle_comment(self, data: str) -> None:
        self._add_markup(f"<!--{data}-->", "comment")

    def handle_decl(self, decl: str) -> None:
        self._add_markup(f"<!{decl}>", "declaration")

    def handle_pi(self, data: str) -> None:
        self._add_markup(f"<?{data}>", "processing-instruction")

    def unknown_decl(self, data: str) -> None:
        self._add_markup(f"<![{data}]>", "declaration")

    def result(self) -> ParsedHtml:
        if self.open_elements:
            self.balanced = False
        return ParsedHtml(
            markup=tuple(self.markup),
            skeleton=tuple(self.skeleton),
            text_slots=tuple(self.text_slots),
            protected_slots=tuple(self.protected_slots),
            balanced=self.balanced,
        )


def parse_html_structure(value: str) -> ParsedHtml:
    parser = _HtmlStructureParser()
    parser.feed(value)
    parser.close()
    return parser.result()


def validate_or_repair_html_translation(
    source_html: str,
    translated_html: str,
    *,
    translate_text: Callable[[str], str],
) -> HtmlTranslationResult:
    source = parse_html_structure(source_html)
    candidate_value, fence_removed = _remove_markdown_fence(translated_html)

    if not source.has_html_tags:
        return _normalize_html_without_source_markup(
            candidate_value,
            fence_removed=fence_removed,
        )

    candidates = [(candidate_value, fence_removed, "Markdown code fence removed")]
    unescaped_candidate = html.unescape(candidate_value)
    if unescaped_candidate != candidate_value:
        candidates.append(
            (unescaped_candidate, True, "Escaped HTML markup restored")
        )

    for candidate_text, repaired, reason in candidates:
        candidate = parse_html_structure(candidate_text)
        if not _structures_are_compatible(source, candidate):
            continue

        result = source.render(
            _translated_slots_in_source_markup(source, candidate)
        )
        _assert_source_structure_preserved(source, result)
        markup_changed = tuple(candidate.markup) != source.markup
        protected_content_changed = any(
            source.protected_slots[index]
            and source.text_slots[index] != candidate.text_slots[index]
            for index in range(len(source.text_slots))
        )
        normalization_reason = None
        if markup_changed:
            normalization_reason = "Source HTML tags and attributes restored"
        elif protected_content_changed:
            normalization_reason = "Non-translatable script or style content restored"
        return HtmlTranslationResult(
            value=result,
            repaired=repaired or markup_changed or protected_content_changed,
            reason=reason if repaired else normalization_reason,
        )

    repaired_value = source.render(
        _translate_source_text_slots(source, translate_text)
    )
    _assert_source_structure_preserved(source, repaired_value)
    return HtmlTranslationResult(
        value=repaired_value,
        repaired=True,
        reason="Invalid or changed HTML structure rebuilt from the source markup",
    )


def _remove_markdown_fence(value: str) -> tuple[str, bool]:
    match = MARKDOWN_HTML_FENCE.fullmatch(value)
    if not match:
        return value, False
    return match.group(1), True


def _normalize_html_without_source_markup(
    translated_value: str,
    *,
    fence_removed: bool,
) -> HtmlTranslationResult:
    candidate = parse_html_structure(translated_value)
    if candidate.has_html_tags and candidate.balanced:
        return HtmlTranslationResult(
            value=translated_value,
            repaired=fence_removed,
            reason="Markdown code fence removed" if fence_removed else None,
        )

    normalized_text = _normalize_text_for_html(translated_value)
    return HtmlTranslationResult(
        value=f"<p>{normalized_text}</p>",
        repaired=True,
        reason="Plain response wrapped in an HTML paragraph",
    )


def _structures_are_compatible(source: ParsedHtml, candidate: ParsedHtml) -> bool:
    if source.skeleton != candidate.skeleton:
        return False
    if len(source.text_slots) != len(candidate.text_slots):
        return False

    for index, source_slot in enumerate(source.text_slots):
        if source.protected_slots[index]:
            continue
        source_has_text = _has_visible_text(source_slot)
        candidate_has_text = _has_visible_text(candidate.text_slots[index])
        if source_has_text != candidate_has_text:
            return False
    return True


def _translated_slots_in_source_markup(
    source: ParsedHtml,
    candidate: ParsedHtml,
) -> tuple[str, ...]:
    slots: list[str] = []
    for index, source_slot in enumerate(source.text_slots):
        if source.protected_slots[index] or not _has_visible_text(source_slot):
            slots.append(source_slot)
            continue
        slots.append(_replace_text_preserving_source_whitespace(
            source_slot,
            candidate.text_slots[index],
        ))
    return tuple(slots)


def _translate_source_text_slots(
    source: ParsedHtml,
    translate_text: Callable[[str], str],
) -> tuple[str, ...]:
    slots: list[str] = []
    for index, source_slot in enumerate(source.text_slots):
        if source.protected_slots[index] or not _has_visible_text(source_slot):
            slots.append(source_slot)
            continue

        leading, core, trailing = _split_edge_whitespace(source_slot)
        translated = translate_text(html.unescape(core))
        if not translated or not translated.strip():
            raise ValueError("HTML repair translation returned an empty text node.")
        slots.append(f"{leading}{_normalize_text_for_html(translated)}{trailing}")
    return tuple(slots)


def _replace_text_preserving_source_whitespace(
    source_slot: str,
    translated_slot: str,
) -> str:
    leading, _core, trailing = _split_edge_whitespace(source_slot)
    translated_core = html.unescape(translated_slot).strip()
    if not translated_core:
        raise ValueError("HTML translation returned an empty text node.")
    return f"{leading}{html.escape(translated_core, quote=False)}{trailing}"


def _split_edge_whitespace(value: str) -> tuple[str, str, str]:
    match = EDGE_WHITESPACE.fullmatch(value)
    if not match:
        return "", value, ""
    return match.group(1), match.group(2), match.group(3)


def _normalize_text_for_html(value: str) -> str:
    return html.escape(html.unescape(value).strip(), quote=False)


def _has_visible_text(value: str) -> bool:
    return bool(html.unescape(value).strip())


def _assert_source_structure_preserved(source: ParsedHtml, value: str) -> None:
    result = parse_html_structure(value)
    if result.skeleton != source.skeleton:
        raise ValueError("HTML repair could not preserve the source tag structure.")
