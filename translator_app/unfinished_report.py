from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from textwrap import wrap


FIELD_LABELS = {
    "table": "Table",
    "file": "File",
    "row": "Record",
    "key": "Key",
    "rows": "Affected rows",
    "entries": "Affected entries",
    "language": "Language",
    "status": "Status",
    "reason": "Reason",
}
FIELD_ORDER = (
    "table",
    "file",
    "row",
    "key",
    "rows",
    "entries",
    "language",
    "status",
    "reason",
)
REPORT_WIDTH = 88


def format_unfinished_report(
    records: Iterable[str],
    *,
    operation_name: str,
) -> str:
    """Build a deterministic, human-readable end-of-run report."""

    parsed_records = _deduplicated_records(records)
    parsed_records.sort(key=_sort_key)
    status_counts = Counter(
        record.get("status", "unspecified").strip().casefold() or "unspecified"
        for record in parsed_records
    )

    border = "=" * REPORT_WIDTH
    divider = "-" * REPORT_WIDTH
    lines = [
        border,
        "END-OF-RUN UNFINISHED RECORDS REPORT",
        f"Operation : {operation_name}",
        f"Total     : {len(parsed_records)}",
    ]
    if not parsed_records:
        lines.extend(
            [
                "Result    : No failed, stopped, skipped, or unprocessed items were recorded.",
                border,
            ]
        )
        return "\n".join(lines)

    lines.append(
        "Statuses  : "
        + ", ".join(
            f"{status}={count}"
            for status, count in sorted(status_counts.items())
        )
    )
    lines.append(divider)
    for index, record in enumerate(parsed_records, start=1):
        lines.append(f"[{index:03d}] {_item_title(record)}")
        emitted_fields: set[str] = set()
        for field_name in FIELD_ORDER:
            value = record.get(field_name)
            if value is None:
                continue
            emitted_fields.add(field_name)
            display_value = value.upper() if field_name == "status" else value
            _append_field(lines, FIELD_LABELS[field_name], display_value)
        for field_name in sorted(record.keys() - emitted_fields - {"kind", "raw"}):
            _append_field(
                lines,
                field_name.replace("_", " ").title(),
                record[field_name],
            )
        if index < len(parsed_records):
            lines.append("")
    lines.extend([divider, "End of unfinished records report.", border])
    return "\n".join(lines)


def _deduplicated_records(records: Iterable[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_record in records:
        normalized = " ".join(str(raw_record).split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(_parse_record(normalized))
    return parsed


def _parse_record(raw_record: str) -> dict[str, str]:
    result: dict[str, str] = {"raw": raw_record}
    for part in raw_record.split(" | "):
        if "=" not in part:
            value = part.strip()
            if "reason" in result and value:
                result["reason"] = f"{result['reason']} | {value}"
            else:
                result.setdefault("kind", value or "item")
            continue
        field_name, value = part.split("=", 1)
        normalized_name = field_name.strip().casefold().replace(" ", "_")
        if normalized_name.startswith("operation_status"):
            result.setdefault("kind", "operation")
            normalized_name = "status"
        if normalized_name in result and normalized_name == "reason":
            result[normalized_name] = f"{result[normalized_name]} | {value.strip()}"
        else:
            result[normalized_name] = value.strip()
    if len(result) == 1:
        result["kind"] = "item"
        result["reason"] = raw_record
    return result


def _sort_key(record: dict[str, str]) -> tuple[str, ...]:
    item_identifier = record.get("row", record.get("key", ""))
    return (
        record.get("table", record.get("file", record.get("kind", ""))).casefold(),
        record.get("language", "").casefold(),
        "0" if item_identifier else "1",
        item_identifier.casefold(),
        record.get("status", "").casefold(),
        record.get("reason", "").casefold(),
    )


def _item_title(record: dict[str, str]) -> str:
    if "table" in record and "row" in record:
        return "Database record"
    if "table" in record:
        return "Database table / row group"
    if "file" in record and "key" in record:
        return "RESX record"
    if "file" in record:
        return "RESX file / entry group"
    return record.get("kind", "Item").replace("_", " ").title()


def _append_field(lines: list[str], label: str, value: str) -> None:
    prefix = f"      {label:<16}: "
    continuation = " " * len(prefix)
    wrapped_lines = wrap(
        value,
        width=max(REPORT_WIDTH - len(prefix), 24),
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    lines.append(prefix + wrapped_lines[0])
    lines.extend(continuation + line for line in wrapped_lines[1:])
