from __future__ import annotations


def quote_identifier(identifier: str) -> str:
    return f"[{identifier.replace(']', ']]')}]"


def quote_table(schema_name: str, table_name: str) -> str:
    return f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"
