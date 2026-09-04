from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

from translator_app.models import (
    InsertColumnPlan,
    LocalizeTable,
    TableTranslationPlan,
)
from translator_app.sqlserver.sql import quote_identifier, quote_table


class SqlServerLocalizationRepository:
    def __init__(
        self,
        read_connection: object,
        write_connection: object | None = None,
    ) -> None:
        self.read_connection = read_connection
        self.write_connection = write_connection or read_connection

    def count_missing_rows(
        self,
        table: LocalizeTable,
        *,
        source_language_id: int,
        target_language_id: int,
    ) -> int:
        sql = self._missing_rows_sql(table, "COUNT_BIG(1)", order_by=False)
        cursor = self.read_connection.cursor()
        value = cursor.execute(
            sql,
            source_language_id,
            target_language_id,
        ).fetchone()[0]
        return int(value)

    def iter_missing_source_rows(
        self,
        plan: TableTranslationPlan,
        *,
        source_language_id: int,
        target_language_id: int,
        batch_size: int,
    ) -> Iterator[dict[str, object]]:
        select_columns = ", ".join(
            f"src.{quote_identifier(column_name)} AS {quote_identifier(column_name)}"
            for column_name in plan.source_column_names
        )
        sql = self._missing_rows_sql(plan.table, select_columns, order_by=True)
        cursor = self.read_connection.cursor()
        cursor.execute(sql, source_language_id, target_language_id)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                return
            column_names = [column[0] for column in cursor.description]
            for row in rows:
                yield dict(zip(column_names, row, strict=False))

    def insert_translation(
        self,
        plan: TableTranslationPlan,
        *,
        source_row: dict[str, object],
        translated_values: dict[str, object],
        target_language_id: int,
    ) -> None:
        column_names = [column_plan.column.name for column_plan in plan.insert_columns]
        placeholders = ", ".join("?" for _ in column_names)
        sql = (
            f"INSERT INTO {quote_table(plan.table.schema_name, plan.table.table_name)} "
            f"({', '.join(quote_identifier(name) for name in column_names)}) "
            f"VALUES ({placeholders})"
        )
        values = [
            self._resolve_insert_value(
                column_plan,
                source_row=source_row,
                translated_values=translated_values,
                target_language_id=target_language_id,
            )
            for column_plan in plan.insert_columns
        ]
        self.write_connection.cursor().execute(sql, *values)

    def destination_exists(
        self,
        table: LocalizeTable,
        *,
        entity_key_value: object,
        target_language_id: int,
    ) -> bool:
        table_name = quote_table(table.schema_name, table.table_name)
        language_column = quote_identifier(required(table.language_column_name))
        entity_key_column = quote_identifier(required(table.entity_key_column_name))
        sql = f"""
SELECT TOP (1) 1
FROM {table_name}
WHERE {entity_key_column} = ? AND {language_column} = ?
"""
        row = self.write_connection.cursor().execute(
            sql,
            entity_key_value,
            target_language_id,
        ).fetchone()
        return row is not None

    def _missing_rows_sql(
        self,
        table: LocalizeTable,
        select_expression: str,
        *,
        order_by: bool,
    ) -> str:
        table_name = quote_table(table.schema_name, table.table_name)
        language_column = quote_identifier(required(table.language_column_name))
        entity_key_column = quote_identifier(required(table.entity_key_column_name))
        sql = f"""
SELECT {select_expression}
FROM {table_name} AS src
WHERE
    src.{language_column} = ?
    AND NOT EXISTS (
        SELECT 1
        FROM {table_name} AS dst
        WHERE
            dst.{entity_key_column} = src.{entity_key_column}
            AND dst.{language_column} = ?
    )
"""
        if order_by:
            sql += f"ORDER BY src.{entity_key_column}"
        return sql

    def _resolve_insert_value(
        self,
        column_plan: InsertColumnPlan,
        *,
        source_row: dict[str, object],
        translated_values: dict[str, object],
        target_language_id: int,
    ) -> object:
        column_name = column_plan.column.name
        if column_plan.mode == "copy_from_source":
            return source_row[column_name]
        if column_plan.mode == "target_language":
            return target_language_id
        if column_plan.mode == "translated_text":
            return translated_values[column_name]
        if column_plan.mode == "generated_uuid":
            return str(uuid4())
        raise ValueError(f"Insert mode نامعتبر است: {column_plan.mode}")


def required(value: str | None) -> str:
    if value is None:
        raise ValueError("metadata جدول کامل نیست.")
    return value
