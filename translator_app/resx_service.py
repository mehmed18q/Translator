from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from translator_app.config import RetrySettings
from translator_app.languages import LanguageOption
from translator_app.retry import run_with_retry
from translator_app.service import calculate_percent, detect_text_format, format_progress
from translator_app.translators.base import Translator


XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
FORMAT_PLACEHOLDER_PATTERN = re.compile(r"(?<!\{)\{[0-9]+(?::[^{}]+)?\}(?!\})")

ET.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
ET.register_namespace("msdata", "urn:schemas-microsoft-com:xml-msdata")


@dataclass(frozen=True)
class ResxTranslationConfig:
    resource_dir: Path
    base_file_names: tuple[str, ...]
    source_language: LanguageOption
    target_language: LanguageOption
    dry_run: bool
    progress_every: int
    retry: RetrySettings


@dataclass
class ResxTranslationSummary:
    discovered_files: int = 0
    eligible_files: int = 0
    skipped_files: int = 0
    total_entries: int = 0
    pending_entries: int = 0
    processed_entries: int = 0
    translated_entries: int = 0
    skipped_existing_entries: int = 0
    skipped_empty_entries: int = 0
    failed_entries: int = 0
    created_files: int = 0
    updated_files: int = 0


@dataclass(frozen=True)
class ResxProgressSnapshot:
    phase: str
    discovered_files: int
    eligible_files: int
    skipped_files: int
    total_entries: int
    pending_entries: int
    processed_entries: int
    remaining_entries: int
    translated_entries: int
    skipped_existing_entries: int
    skipped_empty_entries: int
    failed_entries: int
    created_files: int
    updated_files: int
    percent: float
    current_file: str | None = None
    current_key: str | None = None
    current_file_index: int = 0
    total_files: int = 0
    file_pending_entries: int = 0
    file_processed_entries: int = 0
    file_remaining_entries: int = 0
    file_percent: float = 0.0


@dataclass(frozen=True)
class ResxEntry:
    key: str
    value: str
    element: ET.Element


@dataclass
class ResxDocument:
    path: Path
    tree: ET.ElementTree

    @property
    def root(self) -> ET.Element:
        return self.tree.getroot()


@dataclass(frozen=True)
class ResxFilePlan:
    base_file_name: str
    source_path: Path
    target_path: Path
    target_exists: bool
    entries: tuple[ResxEntry, ...]
    pending_entries: tuple[ResxEntry, ...]


ResxProgressCallback = Callable[[ResxProgressSnapshot], None]
CancelCallback = Callable[[], bool]


class ResxTranslationService:
    def __init__(
        self,
        *,
        translator: Translator,
        logger: logging.Logger,
        progress_callback: ResxProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
    ) -> None:
        self.translator = translator
        self.logger = logger
        self.progress_callback = progress_callback
        self.cancel_callback = cancel_callback
        self._translation_cache: dict[tuple[str, str, str, str], str] = {}

    def run(self, config: ResxTranslationConfig) -> ResxTranslationSummary:
        self.logger.info(
            "Starting RESX translation: source=%s target=%s | mode=%s | folder=%s",
            config.source_language.code,
            config.target_language.code,
            "dry-run" if config.dry_run else "execute",
            config.resource_dir,
        )

        summary = ResxTranslationSummary(discovered_files=len(config.base_file_names))
        self._emit_progress(summary, phase="discovered", total_files=len(config.base_file_names))

        plans = self._prepare_files(config, summary)
        self._emit_progress(summary, phase="prepared", total_files=len(plans))

        if config.dry_run:
            self.logger.info(
                "RESX dry-run finished: files=%s pending=%s skipped_existing=%s skipped_empty=%s",
                summary.eligible_files,
                summary.pending_entries,
                summary.skipped_existing_entries,
                summary.skipped_empty_entries,
            )
            self._emit_progress(summary, phase="finished", total_files=len(plans))
            return summary

        self._translate_files(plans, config, summary)
        self.logger.info(
            "RESX finished: total=%s pending=%s processed=%s translated=%s skipped_existing=%s failed=%s created_files=%s updated_files=%s",
            summary.total_entries,
            summary.pending_entries,
            summary.processed_entries,
            summary.translated_entries,
            summary.skipped_existing_entries,
            summary.failed_entries,
            summary.created_files,
            summary.updated_files,
        )
        self._emit_progress(summary, phase="finished", total_files=len(plans))
        return summary

    def _prepare_files(
        self,
        config: ResxTranslationConfig,
        summary: ResxTranslationSummary,
    ) -> list[ResxFilePlan]:
        plans: list[ResxFilePlan] = []
        selected_files = tuple(dict.fromkeys(clean_base_file_name(name) for name in config.base_file_names))

        for file_index, base_file_name in enumerate(selected_files, start=1):
            if self._is_cancelled():
                self.logger.warning("RESX operation stopped during preparation.")
                break

            source_path = source_resx_path(
                config.resource_dir,
                base_file_name,
                config.source_language.code,
            )
            target_path = target_resx_path(
                config.resource_dir,
                base_file_name,
                config.target_language.code,
            )

            if not source_path.exists():
                summary.skipped_files += 1
                self.logger.warning(
                    "RESX source skipped: %s was not found for source language %s",
                    source_path,
                    config.source_language.code,
                )
                continue

            try:
                source_document = parse_resx(source_path)
                source_entries = tuple(iter_string_entries(source_document.root))
                target_existing_keys = self._target_existing_keys(target_path)
            except Exception as exc:
                summary.skipped_files += 1
                self.logger.exception("RESX file skipped: %s | reason=%s", base_file_name, exc)
                continue

            if not source_entries:
                summary.skipped_files += 1
                self.logger.warning("RESX file skipped: %s has no string entries.", source_path)
                continue

            pending_entries: list[ResxEntry] = []
            for entry in source_entries:
                summary.total_entries += 1
                if not entry.value.strip():
                    summary.skipped_empty_entries += 1
                    summary.processed_entries += 1
                    continue
                if entry.key in target_existing_keys:
                    summary.skipped_existing_entries += 1
                    summary.processed_entries += 1
                    continue
                pending_entries.append(entry)

            summary.eligible_files += 1
            summary.pending_entries += len(pending_entries)
            plan = ResxFilePlan(
                base_file_name=base_file_name,
                source_path=source_path,
                target_path=target_path,
                target_exists=target_path.exists(),
                entries=source_entries,
                pending_entries=tuple(pending_entries),
            )
            plans.append(plan)
            self._emit_progress(
                summary,
                phase="prepare",
                current_file=base_file_name,
                current_file_index=file_index,
                total_files=len(selected_files),
                file_pending_entries=len(pending_entries),
            )
            self.logger.info(
                "RESX ready: %s | source=%s | target=%s | total=%s pending=%s skipped_existing=%s",
                base_file_name,
                source_path,
                target_path,
                len(source_entries),
                len(pending_entries),
                len(source_entries) - len(pending_entries),
            )

        return plans

    def _translate_files(
        self,
        plans: list[ResxFilePlan],
        config: ResxTranslationConfig,
        summary: ResxTranslationSummary,
    ) -> None:
        for file_index, plan in enumerate(plans, start=1):
            if self._is_cancelled():
                self.logger.warning("RESX operation stopped before the next file.")
                return

            target_document = self._load_or_create_target_document(plan)
            target_root = target_document.root
            target_keys = existing_data_keys(target_root)
            file_processed = 0
            file_failed = 0
            changed = False

            try:
                for entry in plan.pending_entries:
                    if self._is_cancelled():
                        self.logger.warning("RESX operation stopped by user request.")
                        return

                    status = "failed"
                    try:
                        if entry.key in target_keys:
                            summary.skipped_existing_entries += 1
                            status = "skipped-existing"
                        else:
                            translated_value = self._translate_entry(entry, config)
                            target_root.append(clone_entry_with_value(entry, translated_value))
                            target_keys.add(entry.key)
                            summary.translated_entries += 1
                            changed = True
                            status = "translated"
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        summary.failed_entries += 1
                        file_failed += 1
                        status = "failed"
                        self.logger.exception(
                            "RESX key failed: file=%s key=%s | reason=%s",
                            plan.base_file_name,
                            entry.key,
                            exc,
                        )
                    finally:
                        summary.processed_entries += 1
                        file_processed += 1

                    if file_processed % config.progress_every == 0:
                        self._emit_progress(
                            summary,
                            phase="running",
                            current_file=plan.base_file_name,
                            current_key=entry.key,
                            current_file_index=file_index,
                            total_files=len(plans),
                            file_pending_entries=len(plan.pending_entries),
                            file_processed_entries=file_processed,
                        )
                        self.logger.info(
                            "%s | file %s/%s %s | %s key=%s | status=%s | translated=%s skipped_existing=%s failed=%s",
                            format_progress(summary.processed_entries, summary.total_entries),
                            file_index,
                            len(plans),
                            format_progress(file_processed, len(plan.pending_entries)),
                            plan.base_file_name,
                            entry.key,
                            status,
                            summary.translated_entries,
                            summary.skipped_existing_entries,
                            summary.failed_entries,
                        )
            finally:
                if changed or not plan.target_exists:
                    write_resx(target_document)
                    if plan.target_exists:
                        summary.updated_files += 1
                    else:
                        summary.created_files += 1
                    self.logger.info("RESX target written: %s", plan.target_path)

            self.logger.info(
                "RESX file finished: %s | processed=%s failed=%s",
                plan.base_file_name,
                file_processed,
                file_failed,
            )
            self._emit_progress(
                summary,
                phase="file-finished",
                current_file=plan.base_file_name,
                current_file_index=file_index,
                total_files=len(plans),
                file_pending_entries=len(plan.pending_entries),
                file_processed_entries=file_processed,
            )

    def _target_existing_keys(self, target_path: Path) -> set[str]:
        if not target_path.exists():
            return set()
        return existing_data_keys(parse_resx(target_path).root)

    def _load_or_create_target_document(self, plan: ResxFilePlan) -> ResxDocument:
        if plan.target_path.exists():
            return parse_resx(plan.target_path)

        source_document = parse_resx(plan.source_path)
        target_tree = copy.deepcopy(source_document.tree)
        remove_data_elements(target_tree.getroot())
        return ResxDocument(plan.target_path, target_tree)

    def _translate_entry(
        self,
        entry: ResxEntry,
        config: ResxTranslationConfig,
    ) -> str:
        text_format = detect_text_format(entry.key, entry.value)
        cache_key = (
            config.source_language.code,
            config.target_language.code,
            text_format,
            entry.value,
        )
        if cache_key not in self._translation_cache:
            protected_value, placeholders = protect_format_placeholders(entry.value)
            translated_value = run_with_retry(
                lambda: self.translator.translate(
                    protected_value,
                    config.source_language.code,
                    config.target_language.code,
                    text_format=text_format,
                ),
                operation_name=f"translate RESX key {entry.key} ({text_format})",
                attempts=config.retry.attempts,
                initial_delay_seconds=config.retry.initial_delay_seconds,
                backoff_factor=config.retry.backoff_factor,
                logger=self.logger,
            )
            restored_value = restore_format_placeholders(translated_value, placeholders)
            missing_placeholders = find_missing_placeholders(
                entry.value,
                restored_value,
            )
            if missing_placeholders:
                self.logger.warning(
                    "RESX key translated with missing placeholders: key=%s placeholders=%s",
                    entry.key,
                    ", ".join(missing_placeholders),
                )
            self._translation_cache[cache_key] = restored_value
        return self._translation_cache[cache_key]

    def _is_cancelled(self) -> bool:
        return bool(self.cancel_callback and self.cancel_callback())

    def _emit_progress(
        self,
        summary: ResxTranslationSummary,
        *,
        phase: str,
        current_file: str | None = None,
        current_key: str | None = None,
        current_file_index: int = 0,
        total_files: int = 0,
        file_pending_entries: int = 0,
        file_processed_entries: int = 0,
    ) -> None:
        if not self.progress_callback:
            return

        remaining_entries = max(summary.total_entries - summary.processed_entries, 0)
        file_remaining_entries = max(file_pending_entries - file_processed_entries, 0)
        snapshot = ResxProgressSnapshot(
            phase=phase,
            discovered_files=summary.discovered_files,
            eligible_files=summary.eligible_files,
            skipped_files=summary.skipped_files,
            total_entries=summary.total_entries,
            pending_entries=summary.pending_entries,
            processed_entries=summary.processed_entries,
            remaining_entries=remaining_entries,
            translated_entries=summary.translated_entries,
            skipped_existing_entries=summary.skipped_existing_entries,
            skipped_empty_entries=summary.skipped_empty_entries,
            failed_entries=summary.failed_entries,
            created_files=summary.created_files,
            updated_files=summary.updated_files,
            percent=calculate_percent(summary.processed_entries, summary.total_entries),
            current_file=current_file,
            current_key=current_key,
            current_file_index=current_file_index,
            total_files=total_files,
            file_pending_entries=file_pending_entries,
            file_processed_entries=file_processed_entries,
            file_remaining_entries=file_remaining_entries,
            file_percent=calculate_percent(file_processed_entries, file_pending_entries),
        )

        try:
            self.progress_callback(snapshot)
        except Exception:
            self.logger.debug("RESX progress callback failed", exc_info=True)


def source_resx_path(resource_dir: Path, base_file_name: str, language_code: str) -> Path:
    clean_name = clean_base_file_name(base_file_name)
    if language_code == "en":
        return resource_dir / clean_name
    return localized_resx_path(resource_dir, clean_name, language_code)


def target_resx_path(resource_dir: Path, base_file_name: str, language_code: str) -> Path:
    clean_name = clean_base_file_name(base_file_name)
    if language_code == "en":
        return resource_dir / clean_name
    return localized_resx_path(resource_dir, clean_name, language_code)


def localized_resx_path(resource_dir: Path, base_file_name: str, language_code: str) -> Path:
    path = Path(base_file_name)
    return resource_dir / f"{path.stem}.{language_code}{path.suffix}"


def clean_base_file_name(file_name: str) -> str:
    clean_name = Path(file_name.strip()).name
    if not clean_name:
        raise ValueError("RESX file name cannot be empty.")
    if clean_name.casefold().endswith(".resx"):
        return clean_name
    return f"{clean_name}.resx"


def parse_resx(path: Path) -> ResxDocument:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ResxDocument(path=path, tree=ET.parse(path, parser=parser))


def write_resx(document: ResxDocument) -> None:
    document.path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(document.tree, space="  ")
    document.tree.write(
        document.path,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )


def iter_string_entries(root: ET.Element) -> list[ResxEntry]:
    entries: list[ResxEntry] = []
    for data_element in iter_data_elements(root):
        if not is_translatable_resx_data(data_element):
            continue
        key = data_element.attrib.get("name")
        value_element = find_child(data_element, "value")
        if not key or value_element is None or value_element.text is None:
            continue
        entries.append(ResxEntry(key=key, value=value_element.text, element=data_element))
    return entries


def existing_data_keys(root: ET.Element) -> set[str]:
    keys: set[str] = set()
    for data_element in iter_data_elements(root):
        key = data_element.attrib.get("name")
        value_element = find_child(data_element, "value")
        if key and value_element is not None and value_element.text is not None:
            keys.add(key)
    return keys


def iter_data_elements(root: ET.Element) -> list[ET.Element]:
    return [
        child
        for child in list(root)
        if isinstance(child.tag, str) and local_name(child.tag) == "data"
    ]


def remove_data_elements(root: ET.Element) -> None:
    for child in iter_data_elements(root):
        root.remove(child)


def is_translatable_resx_data(data_element: ET.Element) -> bool:
    if "type" in data_element.attrib or "mimetype" in data_element.attrib:
        return False
    return find_child(data_element, "value") is not None


def clone_entry_with_value(entry: ResxEntry, translated_value: str) -> ET.Element:
    cloned = copy.deepcopy(entry.element)
    value_element = find_child(cloned, "value")
    if value_element is None:
        value_element = ET.SubElement(cloned, "value")
    value_element.text = translated_value
    if XML_SPACE not in cloned.attrib:
        cloned.attrib[XML_SPACE] = "preserve"
    return cloned


def find_child(element: ET.Element, child_name: str) -> ET.Element | None:
    for child in list(element):
        if isinstance(child.tag, str) and local_name(child.tag) == child_name:
            return child
    return None


def local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def protect_format_placeholders(text: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    replacements: list[tuple[str, str]] = []

    def replace_match(match: re.Match[str]) -> str:
        token = f"RESXPLACEHOLDER{len(replacements)}TOKEN"
        replacements.append((token, match.group(0)))
        return token

    return FORMAT_PLACEHOLDER_PATTERN.sub(replace_match, text), tuple(replacements)


def restore_format_placeholders(
    text: str,
    replacements: tuple[tuple[str, str], ...],
) -> str:
    restored = text
    for token, placeholder in replacements:
        restored = restored.replace(token, placeholder)
    return restored


def find_missing_placeholders(source_text: str, translated_text: str) -> list[str]:
    return [
        placeholder
        for placeholder in FORMAT_PLACEHOLDER_PATTERN.findall(source_text)
        if placeholder not in translated_text
    ]
