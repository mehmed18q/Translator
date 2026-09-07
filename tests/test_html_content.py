from __future__ import annotations

import unittest

from translator_app.html_content import (
    parse_html_structure,
    validate_or_repair_html_translation,
)


class HtmlContentTests(unittest.TestCase):
    def test_accepts_valid_translation_and_preserves_html(self) -> None:
        result = validate_or_repair_html_translation(
            '<div class="content"><p>سلام <strong>دنیا</strong></p></div>',
            '<div class="content"><p>Hello <strong>world</strong></p></div>',
            translate_text=lambda _text: self.fail("repair should not be used"),
        )

        self.assertEqual(
            result.value,
            '<div class="content"><p>Hello <strong>world</strong></p></div>',
        )
        self.assertFalse(result.repaired)

    def test_restores_source_tags_and_attributes(self) -> None:
        result = validate_or_repair_html_translation(
            '<p class="source">سلام</p>',
            '<p class="changed">Hello</p>',
            translate_text=lambda _text: self.fail("repair should not be used"),
        )

        self.assertEqual(result.value, '<p class="source">Hello</p>')
        self.assertTrue(result.repaired)
        self.assertIn("attributes restored", result.reason or "")

    def test_restores_escaped_html_response(self) -> None:
        result = validate_or_repair_html_translation(
            "<p>سلام</p>",
            "&lt;p&gt;Hello&lt;/p&gt;",
            translate_text=lambda _text: self.fail("repair should not be used"),
        )

        self.assertEqual(result.value, "<p>Hello</p>")
        self.assertTrue(result.repaired)
        self.assertEqual(result.reason, "Escaped HTML markup restored")

    def test_rebuilds_broken_response_from_source_structure(self) -> None:
        translated_nodes: list[str] = []

        def translate_text(value: str) -> str:
            translated_nodes.append(value)
            return {"سلام": "Hello", "دنیا": "world"}[value]

        source = '<div class="safe"><p>سلام <strong>دنیا</strong></p></div>'
        result = validate_or_repair_html_translation(
            source,
            "<div><p>Wrong <em>broken</em></p>",
            translate_text=translate_text,
        )

        self.assertEqual(
            result.value,
            '<div class="safe"><p>Hello <strong>world</strong></p></div>',
        )
        self.assertEqual(translated_nodes, ["سلام", "دنیا"])
        self.assertTrue(result.repaired)
        self.assertEqual(
            parse_html_structure(result.value).skeleton,
            parse_html_structure(source).skeleton,
        )

    def test_removes_markdown_code_fence(self) -> None:
        result = validate_or_repair_html_translation(
            "<p>سلام</p>",
            "```html\n<p>Hello</p>\n```",
            translate_text=lambda _text: self.fail("repair should not be used"),
        )

        self.assertEqual(result.value, "<p>Hello</p>")
        self.assertTrue(result.repaired)

    def test_wraps_plain_response_when_html_column_has_plain_source(self) -> None:
        result = validate_or_repair_html_translation(
            "سلام",
            "Hello & welcome",
            translate_text=lambda _text: self.fail("repair should not be used"),
        )

        self.assertEqual(result.value, "<p>Hello &amp; welcome</p>")
        self.assertTrue(result.repaired)

    def test_preserves_script_content_from_source(self) -> None:
        result = validate_or_repair_html_translation(
            "<p>سلام</p><script>window.ready = true;</script>",
            "<p>Hello</p><script>translated and broken</script>",
            translate_text=lambda _text: self.fail("repair should not be used"),
        )

        self.assertEqual(
            result.value,
            "<p>Hello</p><script>window.ready = true;</script>",
        )
        self.assertTrue(result.repaired)
        self.assertIn("script or style", result.reason or "")


if __name__ == "__main__":
    unittest.main()
