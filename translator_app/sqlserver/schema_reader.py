from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from translator_app.models import (
    ColumnInfo,
    ForeignKeyInfo,
    LocalizeTable,
    remove_localize_suffix,
)
from translator_app.special_tables import (
    RESOURCE_KEY_COLUMN_NAME,
    RESOURCE_TABLE_NAME,
    is_resource_table,
)

LANGUAGE_COLUMN_CANDIDATES = ("LanguageId", "LangId")


class SqlServerSchemaReader:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def get_localize_tables(
        self, *, schema_name: str | None = None, table_name: str | None = None
    ) -> list[LocalizeTable]:
        columns_by_object = self._read_localize_columns(
            schema_name=schema_name,
            table_name=table_name,
        )
        foreign_keys_by_object = self._read_foreign_keys(
            schema_name=schema_name,
            table_name=table_name,
        )

        tables: list[LocalizeTable] = []
        for object_id, rows in columns_by_object.items():
            first_row = rows[0]
            columns = tuple(
                ColumnInfo(
                    name=row.column_name,
                    data_type=row.data_type,
                    max_length=row.max_length,
                    is_nullable=bool(row.is_nullable),
                    is_identity=bool(row.is_identity),
                    is_computed=bool(row.is_computed),
                    has_default=bool(row.has_default),
                    is_primary_key=bool(row.is_primary_key),
                )
                for row in rows
            )

            foreign_keys = tuple(foreign_keys_by_object.get(object_id, ()))
            language_column = find_language_column_name(columns)
            entity_key_column, referenced_table = find_entity_key_column(
                schema_name=first_row.schema_name,
                table_name=first_row.table_name,
                columns=columns,
                foreign_keys=foreign_keys,
            )

            tables.append(
                LocalizeTable(
                    schema_name=first_row.schema_name,
                    table_name=first_row.table_name,
                    object_id=object_id,
                    columns=columns,
                    foreign_keys=foreign_keys,
                    language_column_name=language_column,
                    entity_key_column_name=entity_key_column,
                    referenced_table_name=referenced_table,
                )
            )

        return sorted(tables, key=lambda item: item.display_name.casefold())

    def _read_localize_columns(
        self, *, schema_name: str | None, table_name: str | None
    ) -> dict[int, list[object]]:
        sql = """
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    t.object_id AS object_id,
    c.column_id AS column_id,
    c.name AS column_name,
    TYPE_NAME(c.system_type_id) AS data_type,
    c.max_length AS max_length,
    c.is_nullable AS is_nullable,
    c.is_identity AS is_identity,
    c.is_computed AS is_computed,
    CASE WHEN c.default_object_id <> 0 THEN 1 ELSE 0 END AS has_default,
    CASE WHEN pk.column_id IS NULL THEN 0 ELSE 1 END AS is_primary_key
FROM sys.tables AS t
INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
INNER JOIN sys.columns AS c ON c.object_id = t.object_id
LEFT JOIN (
    SELECT ic.object_id, ic.column_id
    FROM sys.indexes AS i
    INNER JOIN sys.index_columns AS ic
        ON ic.object_id = i.object_id
        AND ic.index_id = i.index_id
    WHERE i.is_primary_key = 1
) AS pk
    ON pk.object_id = t.object_id
    AND pk.column_id = c.column_id
WHERE
    t.is_ms_shipped = 0
    AND (
        LOWER(t.name) LIKE '%localize'
        OR LOWER(t.name) LIKE '%localizes'
        OR (LOWER(s.name) = 'dbo' AND LOWER(t.name) = 'resource')
    )
"""
        params: list[object] = []
        if schema_name:
            sql += "    AND s.name = ?\n"
            params.append(schema_name)
        if table_name:
            sql += "    AND t.name = ?\n"
            params.append(table_name)
        sql += "ORDER BY s.name, t.name, c.column_id"

        cursor = self.connection.cursor()
        rows = cursor.execute(sql, *params).fetchall()
        grouped: dict[int, list[object]] = defaultdict(list)
        for row in rows:
            grouped[int(row.object_id)].append(row)
        return dict(grouped)

    def _read_foreign_keys(
        self, *, schema_name: str | None, table_name: str | None
    ) -> dict[int, list[ForeignKeyInfo]]:
        sql = """
SELECT
    pt.object_id AS parent_object_id,
    fk.name AS foreign_key_name,
    pc.name AS parent_column_name,
    rs.name AS referenced_schema_name,
    rt.name AS referenced_table_name,
    rc.name AS referenced_column_name
FROM sys.foreign_key_columns AS fkc
INNER JOIN sys.foreign_keys AS fk
    ON fk.object_id = fkc.constraint_object_id
INNER JOIN sys.tables AS pt
    ON pt.object_id = fkc.parent_object_id
INNER JOIN sys.schemas AS ps
    ON ps.schema_id = pt.schema_id
INNER JOIN sys.columns AS pc
    ON pc.object_id = pt.object_id
    AND pc.column_id = fkc.parent_column_id
INNER JOIN sys.tables AS rt
    ON rt.object_id = fkc.referenced_object_id
INNER JOIN sys.schemas AS rs
    ON rs.schema_id = rt.schema_id
INNER JOIN sys.columns AS rc
    ON rc.object_id = rt.object_id
    AND rc.column_id = fkc.referenced_column_id
WHERE
    pt.is_ms_shipped = 0
    AND (
        LOWER(pt.name) LIKE '%localize'
        OR LOWER(pt.name) LIKE '%localizes'
        OR (LOWER(ps.name) = 'dbo' AND LOWER(pt.name) = 'resource')
    )
"""
        params: list[object] = []
        if schema_name:
            sql += "    AND ps.name = ?\n"
            params.append(schema_name)
        if table_name:
            sql += "    AND pt.name = ?\n"
            params.append(table_name)
        sql += "ORDER BY pt.object_id, fk.name, fkc.constraint_column_id"

        cursor = self.connection.cursor()
        rows = cursor.execute(sql, *params).fetchall()
        grouped: dict[int, list[ForeignKeyInfo]] = defaultdict(list)
        for row in rows:
            grouped[int(row.parent_object_id)].append(
                ForeignKeyInfo(
                    name=row.foreign_key_name,
                    parent_column_name=row.parent_column_name,
                    referenced_schema_name=row.referenced_schema_name,
                    referenced_table_name=row.referenced_table_name,
                    referenced_column_name=row.referenced_column_name,
                )
            )
        return dict(grouped)


def find_column_name(columns: Iterable[ColumnInfo], expected_name: str) -> str | None:
    for column in columns:
        if column.name.casefold() == expected_name.casefold():
            return column.name
    return None


def find_language_column_name(columns: Iterable[ColumnInfo]) -> str | None:
    for expected_name in LANGUAGE_COLUMN_CANDIDATES:
        found = find_column_name(columns, expected_name)
        if found:
            return found
    return None


def is_language_column_name(column_name: str) -> bool:
    normalized_name = column_name.casefold()
    return any(
        normalized_name == expected_name.casefold()
        for expected_name in LANGUAGE_COLUMN_CANDIDATES
    )


def find_entity_key_column(
    *,
    schema_name: str = "dbo",
    table_name: str,
    columns: tuple[ColumnInfo, ...],
    foreign_keys: tuple[ForeignKeyInfo, ...],
) -> tuple[str | None, str | None]:
    if is_resource_table(schema_name, table_name):
        return find_column_name(columns, RESOURCE_KEY_COLUMN_NAME), RESOURCE_TABLE_NAME

    base_table_name = remove_localize_suffix(table_name)
    fk_groups: dict[str, list[ForeignKeyInfo]] = defaultdict(list)
    for foreign_key in foreign_keys:
        fk_groups[foreign_key.name].append(foreign_key)

    single_column_foreign_keys = [
        items[0]
        for items in fk_groups.values()
        if len(items) == 1
        and not is_language_column_name(items[0].parent_column_name)
    ]

    for foreign_key in single_column_foreign_keys:
        if foreign_key.referenced_table_name.casefold() == base_table_name.casefold():
            return foreign_key.parent_column_name, foreign_key.referenced_table_name

    for foreign_key in single_column_foreign_keys:
        referenced_name = foreign_key.referenced_table_name.casefold()
        if not (
            referenced_name.endswith("localize")
            or referenced_name.endswith("localizes")
        ):
            return foreign_key.parent_column_name, foreign_key.referenced_table_name

    inferred_candidates = (
        f"{base_table_name}Id",
        f"{base_table_name}ID",
        "EntityId",
        "ItemId",
        "ParentId",
    )
    for candidate in inferred_candidates:
        found = find_column_name(columns, candidate)
        if found:
            return found, base_table_name

    for column in columns:
        column_name = column.name.casefold()
        if (
            column_name.endswith("id")
            and not is_language_column_name(column.name)
            and not column.is_identity
        ):
            return column.name, base_table_name

    return None, None
