from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

from translator_app.models import (
    InsertColumnPlan,
    LocalizeTable,
    TableTranslationPlan,
)
from translator_app.sqlserver.sql import quote_identifier, quote_table


DEFAULT_SQL_COMMAND_TIMEOUT_SECONDS = 120
TARGET_EXISTS_COLUMN_NAME = "__target_exists"
TARGET_VALUE_COLUMN_PREFIX = "__target_"


def target_value_column_name(column_name: str) -> str:
    return f"{TARGET_VALUE_COLUMN_PREFIX}{column_name}"


class SqlServerLocalizationRepository:
    def __init__(
        self,
        read_connection: object,
        write_connection: object | None = None,
        command_timeout_seconds: int | None = DEFAULT_SQL_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self.read_connection = read_connection
        self.write_connection = write_connection or read_connection
        self.command_timeout_seconds = command_timeout_seconds

    def count_missing_rows(
        self,
        table: LocalizeTable,
        *,
        source_language_id: int,
        target_language_id: int,
    ) -> int:
        sql = self._missing_rows_sql(table, "COUNT_BIG(1)", order_by=False)
        cursor = self._cursor(self.read_connection)
        try:
            value = cursor.execute(
                sql,
                source_language_id,
                target_language_id,
            ).fetchone()[0]
            return int(value)
        finally:
            cursor.close()

    def count_pending_rows(
        self,
        plan: TableTranslationPlan,
        *,
        source_language_id: int,
        target_language_id: int,
    ) -> int:
        sql = self._pending_rows_sql(plan, "COUNT_BIG(1)", order_by=False)
        cursor = self._cursor(self.read_connection)
        try:
            value = cursor.execute(
                sql,
                target_language_id,
                source_language_id,
            ).fetchone()[0]
            return int(value)
        finally:
            cursor.close()

    def count_pending_text_characters(
        self,
        plan: TableTranslationPlan,
        *,
        source_language_id: int,
        target_language_id: int,
    ) -> int:
        """Return the number of source text characters still needing work.

        The same pending-row predicate used by translation is applied here.
        For an existing destination row, only source columns whose destination
        value is empty are included in the estimate.  ``LEN`` is used instead
        of ``DATALENGTH`` so the result is in characters rather than bytes.
        """

        if not plan.text_column_names:
            return 0

        length_terms = [
            (
                "CASE WHEN "
                f"{self._missing_target_column_condition(column_name)} "
                "THEN COALESCE(LEN(CAST(src."
                f"{quote_identifier(column_name)} AS NVARCHAR(MAX))), 0) "
                "ELSE 0 END"
            )
            for column_name in plan.text_column_names
        ]
        select_expression = f"COALESCE(SUM({' + '.join(length_terms)}), 0)"
        sql = self._pending_rows_sql(plan, select_expression, order_by=False)
        cursor = self._cursor(self.read_connection)
        try:
            row = cursor.execute(
                sql,
                target_language_id,
                source_language_id,
            ).fetchone()
            if not row or row[0] is None:
                return 0
            return max(int(row[0]), 0)
        finally:
            cursor.close()

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
        entity_key_column_name = required(plan.table.entity_key_column_name)
        last_entity_key_value: object | None = None

        while True:
            sql = self._missing_rows_sql(
                plan.table,
                f"TOP ({max(batch_size, 1)}) {select_columns}",
                order_by=True,
                after_entity_key=last_entity_key_value is not None,
            )
            cursor = self._cursor(self.read_connection)
            params: list[object] = [source_language_id, target_language_id]
            if last_entity_key_value is not None:
                params.append(last_entity_key_value)

            try:
                cursor.execute(sql, *params)
                rows = cursor.fetchall()
                if not rows:
                    return
                column_names = [column[0] for column in cursor.description]
            finally:
                cursor.close()

            for row in rows:
                source_row = dict(zip(column_names, row, strict=False))
                last_entity_key_value = source_row.get(entity_key_column_name)
                yield source_row

    def iter_pending_source_rows(
        self,
        plan: TableTranslationPlan,
        *,
        source_language_id: int,
        target_language_id: int,
        batch_size: int,
    ) -> Iterator[dict[str, object]]:
        select_columns = self._pending_select_columns(plan)
        last_key_values: tuple[object, ...] | None = None

        while True:
            sql = self._pending_rows_sql(
                plan,
                f"TOP ({max(batch_size, 1)}) {select_columns}",
                order_by=True,
                after_key_values=last_key_values,
            )
            cursor = self._cursor(self.read_connection)
            params: list[object] = [target_language_id, source_language_id]
            if last_key_values is not None:
                params.extend(self._after_key_params(plan.table, last_key_values))

            try:
                cursor.execute(sql, *params)
                rows = cursor.fetchall()
                if not rows:
                    return
                column_names = [column[0] for column in cursor.description]
            finally:
                cursor.close()

            for row in rows:
                source_row = dict(zip(column_names, row, strict=False))
                last_key_values = self._key_values_from_row(plan.table, source_row)
                yield source_row

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
        cursor = self._cursor(self.write_connection)
        try:
            cursor.execute(sql, *values)
        finally:
            cursor.close()

    def update_translation_columns(
        self,
        plan: TableTranslationPlan,
        *,
        entity_key_value: object | None = None,
        entity_key_values: dict[str, object] | None = None,
        translated_values: dict[str, object],
        target_language_id: int,
    ) -> int:
        if not translated_values:
            return 0

        table_name = quote_table(plan.table.schema_name, plan.table.table_name)
        key_values = self._normalize_key_values(
            plan.table,
            entity_key_value=entity_key_value,
            entity_key_values=entity_key_values,
        )
        key_condition = " AND ".join(
            f"{quote_identifier(column_name)} = ?"
            for column_name, _value in key_values
        )
        language_column = quote_identifier(required(plan.table.language_column_name))
        cursor = self._cursor(self.write_connection)
        updated_columns = 0
        try:
            for column_name, translated_value in translated_values.items():
                column = quote_identifier(column_name)
                sql = f"""
UPDATE {table_name}
SET {column} = ?
WHERE
    {key_condition}
    AND {language_column} = ?
    AND {self._missing_text_value_condition(column_name)}
"""
                cursor.execute(
                    sql,
                    translated_value,
                    *(value for _column_name, value in key_values),
                    target_language_id,
                )
                rowcount = getattr(cursor, "rowcount", -1)
                if rowcount != 0:
                    updated_columns += 1
            return updated_columns
        finally:
            cursor.close()

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
        cursor = self._cursor(self.write_connection)
        try:
            row = cursor.execute(
                sql,
                entity_key_value,
                target_language_id,
            ).fetchone()
            return row is not None
        finally:
            cursor.close()

    def _cursor(self, connection: object) -> object:
        cursor = connection.cursor()
        if self.command_timeout_seconds is not None:
            try:
                cursor.timeout = max(int(self.command_timeout_seconds), 1)
            except Exception:
                pass
        return cursor

    def _missing_rows_sql(
        self,
        table: LocalizeTable,
        select_expression: str,
        *,
        order_by: bool,
        after_entity_key: bool = False,
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
        if after_entity_key:
            sql += f"    AND src.{entity_key_column} > ?\n"
        if order_by:
            sql += f"ORDER BY src.{entity_key_column}"
        return sql

    def _pending_rows_sql(
        self,
        plan: TableTranslationPlan,
        select_expression: str,
        *,
        order_by: bool,
        after_key_values: tuple[object, ...] | None = None,
    ) -> str:
        table = plan.table
        table_name = quote_table(table.schema_name, table.table_name)
        language_column = quote_identifier(required(table.language_column_name))
        join_condition = self._key_join_condition(table, "dst", "src")
        pending_conditions = [
            f"dst.{language_column} IS NULL",
            *(
                self._missing_target_column_condition(column_name)
                for column_name in plan.text_column_names
            ),
        ]
        sql = f"""
SELECT {select_expression}
FROM {table_name} AS src
LEFT JOIN {table_name} AS dst
    ON {join_condition}
    AND dst.{language_column} = ?
WHERE
    src.{language_column} = ?
    AND (
        {" OR ".join(pending_conditions)}
    )
"""
        if after_key_values is not None:
            sql += f"    AND ({self._after_key_condition(table)})\n"
        if order_by:
            sql += f"ORDER BY {self._order_by_columns(table, 'src')}"
        return sql

    def _pending_select_columns(self, plan: TableTranslationPlan) -> str:
        table = plan.table
        language_column = quote_identifier(required(table.language_column_name))
        source_columns = [
            f"src.{quote_identifier(column_name)} AS {quote_identifier(column_name)}"
            for column_name in plan.source_column_names
        ]
        target_status_columns = [
            (
                f"CASE WHEN dst.{language_column} IS NULL THEN 0 ELSE 1 END "
                f"AS {quote_identifier(TARGET_EXISTS_COLUMN_NAME)}"
            )
        ]
        target_text_columns = [
            (
                f"dst.{quote_identifier(column_name)} "
                f"AS {quote_identifier(target_value_column_name(column_name))}"
            )
            for column_name in plan.text_column_names
        ]
        return ", ".join(
            [
                *source_columns,
                *target_status_columns,
                *target_text_columns,
            ]
        )

    def _missing_target_column_condition(self, column_name: str) -> str:
        return (
            f"({self._missing_text_value_condition(column_name, table_alias='dst')} "
            f"AND {self._present_text_value_condition(column_name, table_alias='src')})"
        )

    def _missing_text_value_condition(
        self,
        column_name: str,
        *,
        table_alias: str | None = None,
    ) -> str:
        column = quote_identifier(column_name)
        if table_alias:
            column = f"{table_alias}.{column}"
        return f"NULLIF(LTRIM(RTRIM(CAST({column} AS NVARCHAR(MAX)))), N'') IS NULL"

    def _present_text_value_condition(
        self,
        column_name: str,
        *,
        table_alias: str | None = None,
    ) -> str:
        column = quote_identifier(column_name)
        if table_alias:
            column = f"{table_alias}.{column}"
        return f"NULLIF(LTRIM(RTRIM(CAST({column} AS NVARCHAR(MAX)))), N'') IS NOT NULL"

    def _key_column_names(self, table: LocalizeTable) -> tuple[str, ...]:
        key_column_names = table.key_column_names
        if not key_column_names:
            return (required(table.entity_key_column_name),)
        return key_column_names

    def _key_join_condition(
        self,
        table: LocalizeTable,
        left_alias: str,
        right_alias: str,
    ) -> str:
        return " AND ".join(
            (
                f"{left_alias}.{quote_identifier(column_name)} = "
                f"{right_alias}.{quote_identifier(column_name)}"
            )
            for column_name in self._key_column_names(table)
        )

    def _order_by_columns(self, table: LocalizeTable, table_alias: str) -> str:
        return ", ".join(
            f"{table_alias}.{quote_identifier(column_name)}"
            for column_name in self._key_column_names(table)
        )

    def _after_key_condition(self, table: LocalizeTable) -> str:
        key_columns = self._key_column_names(table)
        conditions: list[str] = []
        for column_index, column_name in enumerate(key_columns):
            equality_conditions = [
                f"src.{quote_identifier(previous_column)} = ?"
                for previous_column in key_columns[:column_index]
            ]
            greater_condition = f"src.{quote_identifier(column_name)} > ?"
            conditions.append(
                "(" + " AND ".join([*equality_conditions, greater_condition]) + ")"
            )
        return " OR ".join(conditions)

    def _after_key_params(
        self,
        table: LocalizeTable,
        key_values: tuple[object, ...],
    ) -> list[object]:
        key_columns = self._key_column_names(table)
        if len(key_values) != len(key_columns):
            raise ValueError("Entity key value count does not match table metadata.")

        params: list[object] = []
        for value_index, value in enumerate(key_values):
            params.extend(key_values[:value_index])
            params.append(value)
        return params

    def _key_values_from_row(
        self,
        table: LocalizeTable,
        row: dict[str, object],
    ) -> tuple[object, ...]:
        return tuple(
            row.get(column_name)
            for column_name in self._key_column_names(table)
        )

    def _normalize_key_values(
        self,
        table: LocalizeTable,
        *,
        entity_key_value: object | None,
        entity_key_values: dict[str, object] | None,
    ) -> tuple[tuple[str, object], ...]:
        key_columns = self._key_column_names(table)
        if entity_key_values is not None:
            return tuple(
                (column_name, entity_key_values[column_name])
                for column_name in key_columns
            )
        if len(key_columns) == 1:
            return ((key_columns[0], entity_key_value),)
        raise ValueError("Composite entity key values are required.")

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
        raise ValueError(f"Invalid insert mode: {column_plan.mode}")


def required(value: str | None) -> str:
    if value is None:
        raise ValueError("Table metadata is incomplete.")
    return value
