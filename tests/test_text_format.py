from __future__ import annotations

import logging
import unittest
from pathlib import Path

from translator_app.config import RetrySettings, RuntimeConfig
from translator_app.languages import get_language
from translator_app.models import ColumnInfo, LocalizeTable, build_table_translation_plan
from translator_app.service import DatabaseTranslationService, detect_text_format
from translator_app.translators.base import Translator


def column(name: str, data_type: str) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        data_type=data_type,
        max_length=None,
        is_nullable=False,
        is_identity=False,
        is_computed=False,
        has_default=False,
        is_primary_key=False,
    )


class CapturingTranslator(Translator):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        self.calls.append((text, text_format))
        return text


class BrokenHtmlTranslator(Translator):
    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        del source_language, target_language
        if text_format == "html":
            return "<div>Broken response</div>"
        return {"سلام": "Hello"}.get(text, text)


class TextFormatTests(unittest.TestCase):
    def test_detects_html_from_column_name(self) -> None:
        self.assertEqual(detect_text_format("HTMLContent", "Plain text"), "html")

    def test_detects_html_from_content(self) -> None:
        self.assertEqual(detect_text_format("Body", "<p>Hello</p>"), "html")

    def test_uses_text_for_plain_content(self) -> None:
        self.assertEqual(detect_text_format("Body", "Plain text"), "text")

    def test_service_passes_html_format_to_translator(self) -> None:
        table = LocalizeTable(
            schema_name="dbo",
            table_name="SampleLocalize",
            object_id=1,
            columns=(
                column("SampleId", "int"),
                column("LanguageId", "int"),
                column("HTMLContent", "nvarchar"),
            ),
            foreign_keys=(),
            language_column_name="LanguageId",
            entity_key_column_name="SampleId",
            referenced_table_name="Sample",
        )
        plan = build_table_translation_plan(table)
        translator = CapturingTranslator()
        service = DatabaseTranslationService(
            schema_reader=object(),
            repository=object(),
            translator=translator,
            logger=logging.getLogger("test_text_format"),
        )

        config = RuntimeConfig(
            connection_string="",
            source_language=get_language(1),
            target_language=get_language(2),
            dry_run=True,
            schema_name=None,
            table_name=None,
            batch_size=1,
            progress_every=1,
            translator_provider="libretranslate",
            request_timeout_seconds=1,
            request_delay_seconds=0,
            libretranslate_url="http://localhost:5000",
            libretranslate_api_key=None,
            log_dir=Path("logs"),
            retry=RetrySettings(
                attempts=1,
                initial_delay_seconds=0,
                backoff_factor=1,
            ),
        )

        service._translate_row(
            plan,
            {"SampleId": 1, "LanguageId": 1, "HTMLContent": "<p>سلام</p>"},
            config,
        )

        self.assertEqual(translator.calls, [("<p>سلام</p>", "html")])

    def test_repairs_broken_html_and_logs_before_database_write(self) -> None:
        table = LocalizeTable(
            schema_name="dbo",
            table_name="SampleLocalize",
            object_id=1,
            columns=(
                column("SampleId", "int"),
                column("LanguageId", "int"),
                column("HTMLContent", "nvarchar"),
            ),
            foreign_keys=(),
            language_column_name="LanguageId",
            entity_key_column_name="SampleId",
            referenced_table_name="Sample",
        )
        plan = build_table_translation_plan(table)
        logger = logging.getLogger("test_broken_html_logging")
        service = DatabaseTranslationService(
            schema_reader=object(),
            repository=object(),
            translator=BrokenHtmlTranslator(),
            logger=logger,
        )
        config = RuntimeConfig(
            connection_string="",
            source_language=get_language(1),
            target_language=get_language(2),
            dry_run=False,
            schema_name=None,
            table_name=None,
            batch_size=1,
            progress_every=1,
            translator_provider="libretranslate",
            request_timeout_seconds=1,
            request_delay_seconds=0,
            libretranslate_url="http://localhost:5000",
            libretranslate_api_key=None,
            log_dir=Path("logs"),
            retry=RetrySettings(
                attempts=1,
                initial_delay_seconds=0,
                backoff_factor=1,
            ),
        )

        with self.assertLogs(logger, level="WARNING") as captured:
            translated = service._translate_row(
                plan,
                {"SampleId": 42, "LanguageId": 1, "HTMLContent": "<p>سلام</p>"},
                config,
            )

        self.assertEqual(translated["HTMLContent"], "<p>Hello</p>")
        self.assertIn("HTML response repaired", captured.output[0])
        self.assertIn("table=dbo.SampleLocalize", captured.output[0])
        self.assertIn("column=HTMLContent", captured.output[0])
        self.assertIn("row=SampleId=42", captured.output[0])


if __name__ == "__main__":
    unittest.main()
