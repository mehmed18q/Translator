from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from translator_app.config import RetrySettings
from translator_app.languages import get_language
from translator_app.resx_service import (
    ResxTranslationConfig,
    ResxTranslationService,
    source_resx_path,
    target_resx_path,
)
from translator_app.translators.base import Translator


class FakeTranslator(Translator):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        *,
        text_format: str = "text",
    ) -> str:
        self.calls.append((text, source_language, target_language, text_format))
        return f"{text} [{target_language}]"


class ResxServiceTests(unittest.TestCase):
    def test_creates_missing_destination_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir)
            write_resx(
                resource_dir / "Resources.fa.resx",
                {"Hello": "Salam", "Bye": "Khodafez"},
            )

            translator = FakeTranslator()
            service = build_service(translator)
            summary = service.run(build_config(resource_dir, ("Resources.resx",), 1, 6))

            target_path = resource_dir / "Resources.ru.resx"
            self.assertTrue(target_path.exists())
            self.assertEqual(read_resx_values(target_path)["Hello"], "Salam [ru]")
            self.assertEqual(summary.created_files, 1)
            self.assertEqual(summary.translated_entries, 2)
            self.assertEqual(len(translator.calls), 2)

    def test_skips_existing_destination_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir)
            write_resx(
                resource_dir / "Messages.fa.resx",
                {"Existing": "Old source", "New": "New source"},
            )
            write_resx(resource_dir / "Messages.ar.resx", {"Existing": "Keep me"})

            translator = FakeTranslator()
            service = build_service(translator)
            summary = service.run(build_config(resource_dir, ("Messages.resx",), 1, 3))

            values = read_resx_values(resource_dir / "Messages.ar.resx")
            self.assertEqual(values["Existing"], "Keep me")
            self.assertEqual(values["New"], "New source [ar]")
            self.assertEqual(summary.skipped_existing_entries, 1)
            self.assertEqual(summary.translated_entries, 1)

    def test_english_uses_neutral_resx_file_name(self) -> None:
        resource_dir = Path("/tmp/resources")

        self.assertEqual(
            source_resx_path(resource_dir, "Messages.resx", "en"),
            resource_dir / "Messages.resx",
        )
        self.assertEqual(
            target_resx_path(resource_dir, "Messages.resx", "en"),
            resource_dir / "Messages.resx",
        )
        self.assertEqual(
            target_resx_path(resource_dir, "Messages.resx", "zh"),
            resource_dir / "Messages.zh.resx",
        )

    def test_passes_html_format_for_html_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir)
            write_resx(resource_dir / "Resources.fa.resx", {"HtmlBody": "<p>Hello</p>"})

            translator = FakeTranslator()
            service = build_service(translator)
            service.run(build_config(resource_dir, ("Resources.resx",), 1, 2))

            self.assertEqual(translator.calls[0][3], "html")

    def test_repairs_and_logs_broken_html_resx_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir)
            write_resx(
                resource_dir / "Resources.fa.resx",
                {"HtmlBody": "<p>Hello</p>"},
            )

            translator = FakeTranslator()
            service = build_service(translator)
            with self.assertLogs("test_resx_service", level="WARNING") as captured:
                service.run(build_config(resource_dir, ("Resources.resx",), 1, 2))

            translated = read_resx_values(resource_dir / "Resources.resx")["HtmlBody"]
            self.assertEqual(translated, "<p>Hello [en]</p>")
            repair_log = next(
                message
                for message in captured.output
                if "HTML response repaired" in message
            )
            self.assertIn("file=Resources.resx", repair_log)
            self.assertIn("key=HtmlBody", repair_log)

    def test_preserves_format_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resource_dir = Path(temp_dir)
            write_resx(resource_dir / "Resources.fa.resx", {"Required": "Enter {0}"})

            translator = FakeTranslator()
            service = build_service(translator)
            service.run(build_config(resource_dir, ("Resources.resx",), 1, 6))

            self.assertEqual(translator.calls[0][0], "Enter RESXPLACEHOLDER0TOKEN")
            self.assertEqual(
                read_resx_values(resource_dir / "Resources.ru.resx")["Required"],
                "Enter {0} [ru]",
            )


def build_service(translator: Translator) -> ResxTranslationService:
    logger = logging.getLogger("test_resx_service")
    logger.addHandler(logging.NullHandler())
    return ResxTranslationService(translator=translator, logger=logger)


def build_config(
    resource_dir: Path,
    files: tuple[str, ...],
    source_language_id: int,
    target_language_id: int,
) -> ResxTranslationConfig:
    return ResxTranslationConfig(
        resource_dir=resource_dir,
        base_file_names=files,
        source_language=get_language(source_language_id),
        target_language=get_language(target_language_id),
        dry_run=False,
        progress_every=1,
        retry=RetrySettings(
            attempts=1,
            initial_delay_seconds=0,
            backoff_factor=1,
        ),
    )


def write_resx(path: Path, values: dict[str, str]) -> None:
    root = ET.Element("root")
    for key, value in values.items():
        data = ET.SubElement(root, "data", {"name": key})
        value_element = ET.SubElement(data, "value")
        value_element.text = value
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def read_resx_values(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    result: dict[str, str] = {}
    for data in root.findall("data"):
        value = data.find("value")
        if value is not None:
            result[str(data.attrib["name"])] = value.text or ""
    return result


if __name__ == "__main__":
    unittest.main()
