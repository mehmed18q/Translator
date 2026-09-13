from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from translator_app.languages import LanguageOption


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
HTML_START_TAG = re.compile(
    r"<(?P<tag>[A-Za-z][A-Za-z0-9:-]*)(?P<attrs>(?:[^\"'<>]|\"[^\"]*\"|'[^']*')*)>"
)
HTML_ATTRIBUTE = re.compile(
    r"(?P<prefix>\s+)(?P<name>[A-Za-z_:][A-Za-z0-9_.:-]*)"
    r"(?:\s*=\s*(?P<quoted>\"[^\"]*\"|'[^']*')|\s*=\s*(?P<bare>[^\s/>]+))?",
    re.DOTALL,
)
HTML_STYLE_PROPERTY = re.compile(
    r"(?P<prefix>^|;)(?P<before>\s*)(?P<name>direction|text-align)"
    r"(?P<between>\s*:\s*)(?P<value>[^;]+)",
    re.IGNORECASE,
)


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


def apply_html_direction(
    value: str,
    target_language: LanguageOption | bool,
) -> str:
    """Align directional HTML declarations with the target language.

    Existing ``dir``, ``align``, ``direction`` and ``text-align`` declarations
    are corrected consistently.  HTML that does not contain any directional
    declaration is left untouched: adding layout attributes to arbitrary
    markup could change intentionally centered or mixed-direction content.
    ``target_language`` accepts a ``LanguageOption`` or a boolean for callers
    that only have the RTL flag available.
    """

    right_to_left = bool(
        target_language.right_to_left
        if isinstance(target_language, LanguageOption)
        else getattr(target_language, "right_to_left", target_language)
    )
    direction = "rtl" if right_to_left else "ltr"
    alignment = "right" if right_to_left else "left"

    if not value or not _contains_directional_html(value):
        return value

    protected_ranges = _protected_html_ranges(value)
    result: list[str] = []
    position = 0
    for match in HTML_START_TAG.finditer(value):
        result.append(value[position : match.start()])
        if any(start <= match.start() < end for start, end in protected_ranges):
            result.append(match.group(0))
        else:
            result.append(
                _rewrite_directional_start_tag(
                    match.group(0),
                    match.group("tag"),
                    direction=direction,
                    alignment=alignment,
                )
            )
        position = match.end()
    result.append(value[position:])
    return "".join(result)


def normalize_html_direction(
    value: str,
    target_language: LanguageOption | bool,
) -> str:
    """Backward-compatible descriptive alias for :func:`apply_html_direction`."""

    return apply_html_direction(value, target_language)


def _contains_directional_html(value: str) -> bool:
    protected_ranges = _protected_html_ranges(value)
    for match in HTML_START_TAG.finditer(value):
        if any(start <= match.start() < end for start, end in protected_ranges):
            continue
        tag = match.group(0)
        if re.search(r"\s(?:dir|align)\s*=", tag, re.IGNORECASE):
            return True
        style = _html_attribute_value(tag, "style")
        if style and re.search(r"(?:^|;)\s*(?:direction|text-align)\s*:", style, re.I):
            return True
    return False


def _protected_html_ranges(value: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    comment_pattern = re.compile(r"<!--.*?-->", re.DOTALL)
    ranges.extend((match.start(), match.end()) for match in comment_pattern.finditer(value))
    for tag_name in ("script", "style"):
        pattern = re.compile(
            rf"<{tag_name}\b[^>]*>.*?</{tag_name}\s*>",
            re.IGNORECASE | re.DOTALL,
        )
        ranges.extend((match.start(), match.end()) for match in pattern.finditer(value))
    return tuple(ranges)


def _html_attribute_value(tag: str, attribute_name: str) -> str | None:
    for match in HTML_ATTRIBUTE.finditer(tag):
        if match.group("name").casefold() != attribute_name.casefold():
            continue
        quoted = match.group("quoted")
        if quoted is not None:
            return quoted[1:-1]
        return match.group("bare")
    return None


def _rewrite_directional_start_tag(
    raw_tag: str,
    tag_name: str,
    *,
    direction: str,
    alignment: str,
) -> str:
    if tag_name.casefold() in NON_TRANSLATABLE_ELEMENTS:
        return raw_tag

    has_dir = _html_attribute_value(raw_tag, "dir") is not None
    has_align = _html_attribute_value(raw_tag, "align") is not None
    style = _html_attribute_value(raw_tag, "style")
    has_style_direction = bool(
        style and re.search(r"(?:^|;)\s*direction\s*:", style, re.I)
    )
    has_style_alignment = bool(
        style and re.search(r"(?:^|;)\s*text-align\s*:", style, re.I)
    )
    if not (has_dir or has_align or has_style_direction or has_style_alignment):
        return raw_tag

    rewritten = _replace_html_attribute(raw_tag, "dir", direction)
    rewritten = _replace_html_attribute(rewritten, "align", alignment)
    style = _html_attribute_value(rewritten, "style")
    if style is not None:
        style = _rewrite_directional_style(
            style,
            direction=direction,
            alignment=alignment,
        )
        rewritten = _replace_html_attribute(rewritten, "style", style)
    return rewritten


def _replace_html_attribute(tag: str, attribute_name: str, value: str) -> str:
    pattern = re.compile(
        rf"(?P<prefix>\s+)(?P<name>{re.escape(attribute_name)})"
        rf"(?P<spacing>\s*=\s*)(?:(?P<quoted>\"[^\"]*\"|'[^']*')|"
        rf"(?P<bare>[^\s/>]+))",
        re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        spacing = match.group("spacing")
        quoted = match.group("quoted")
        if quoted:
            return f"{prefix}{match.group('name')}{spacing}{quoted[0]}{value}{quoted[0]}"
        return f"{prefix}{match.group('name')}{spacing}{value}"

    return pattern.sub(replace, tag, count=1)


def _rewrite_directional_style(
    style: str,
    *,
    direction: str,
    alignment: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name.casefold() == "direction":
            value = direction
        else:
            value = alignment
        original_value = match.group("value")
        leading = original_value[: len(original_value) - len(original_value.lstrip())]
        trailing = original_value[len(original_value.rstrip()) :]
        important = " !important" if re.search(r"!\s*important\s*$", original_value, re.I) else ""
        return (
            f"{match.group('prefix')}{match.group('before')}"
            f"{name}{match.group('between')}{leading}{value}{important}{trailing}"
        )

    return HTML_STYLE_PROPERTY.sub(replace, style)


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
