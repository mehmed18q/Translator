from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from translator_app.special_tables import is_special_translatable_column
from translator_app.translatable_columns import is_translatable_column


TEXT_SQL_TYPES = {"nvarchar", "varchar", "nchar", "char", "text", "ntext"}
ROWVERSION_SQL_TYPES = {"timestamp", "rowversion"}


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    max_length: int | None
    is_nullable: bool
    is_identity: bool
    is_computed: bool
    has_default: bool
    is_primary_key: bool

    @property
    def normalized_type(self) -> str:
        return self.data_type.lower()

    @property
    def is_text(self) -> bool:
        return self.normalized_type in TEXT_SQL_TYPES

    @property
    def is_rowversion(self) -> bool:
        return self.normalized_type in ROWVERSION_SQL_TYPES


@dataclass(frozen=True)
class ForeignKeyInfo:
    name: str
    parent_column_name: str
    referenced_schema_name: str
    referenced_table_name: str
    referenced_column_name: str


@dataclass(frozen=True)
class LocalizeTable:
    schema_name: str
    table_name: str
    object_id: int
    columns: tuple[ColumnInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...]
    language_column_name: str | None
    entity_key_column_name: str | None
    referenced_table_name: str | None
    entity_key_column_names: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def get_column(self, column_name: str) -> ColumnInfo:
        for column in self.columns:
            if column.name.casefold() == column_name.casefold():
                return column
        raise KeyError(column_name)

    @property
    def key_column_names(self) -> tuple[str, ...]:
        if self.entity_key_column_names:
            return self.entity_key_column_names
        if self.entity_key_column_name:
            return (self.entity_key_column_name,)
        return ()

    def text_columns(self) -> tuple[ColumnInfo, ...]:
        excluded = {
            (self.language_column_name or "").casefold(),
            *(column_name.casefold() for column_name in self.key_column_names),
        }
        return tuple(
            column
            for column in self.columns
            if column.is_text
            and not column.is_computed
            and (
                is_translatable_column(column.name)
                or is_special_translatable_column(
                    self.schema_name,
                    self.table_name,
                    column.name,
                )
            )
            and column.name.casefold() not in excluded
        )


InsertValueMode = Literal[
    "copy_from_source",
    "target_language",
    "translated_text",
    "generated_uuid",
]


@dataclass(frozen=True)
class InsertColumnPlan:
    column: ColumnInfo
    mode: InsertValueMode


@dataclass(frozen=True)
class TableTranslationPlan:
    table: LocalizeTable
    insert_columns: tuple[InsertColumnPlan, ...]
    text_column_names: tuple[str, ...]
    source_column_names: tuple[str, ...]


def remove_localize_suffix(table_name: str) -> str:
    lower_name = table_name.lower()
    if lower_name.endswith("localizes"):
        return table_name[: -len("Localizes")]
    if lower_name.endswith("localize"):
        return table_name[: -len("Localize")]
    return table_name


def build_table_translation_plan(table: LocalizeTable) -> TableTranslationPlan:
    if not table.language_column_name:
        raise ValueError("LanguageId/LangId column was not found.")
    if not table.key_column_names:
        raise ValueError("Main entity foreign key column was not found.")

    text_columns = table.text_columns()
    if not text_columns:
        raise ValueError("No allow-listed translatable text column was found.")

    language_key = table.language_column_name.casefold()
    entity_keys = {column_name.casefold() for column_name in table.key_column_names}
    text_keys = {column.name.casefold() for column in text_columns}

    insert_columns: list[InsertColumnPlan] = []
    generated_columns: set[str] = set()

    for column in table.columns:
        column_key = column.name.casefold()

        if column.is_identity or column.is_computed or column.is_rowversion:
            continue

        if column_key == language_key:
            insert_columns.append(InsertColumnPlan(column, "target_language"))
            continue

        if column_key in entity_keys:
            insert_columns.append(InsertColumnPlan(column, "copy_from_source"))
            continue

        if column_key in text_keys:
            insert_columns.append(InsertColumnPlan(column, "translated_text"))
            continue

        if column.is_primary_key:
            if column.has_default or column.is_nullable:
                continue
            if column.normalized_type == "uniqueidentifier":
                insert_columns.append(InsertColumnPlan(column, "generated_uuid"))
                generated_columns.add(column.name.casefold())
                continue
            raise ValueError(
                "Unsupported non-identity primary key without a default value: "
                f"{column.name}"
            )

        insert_columns.append(InsertColumnPlan(column, "copy_from_source"))

    source_columns: list[str] = []
    for column_plan in insert_columns:
        column_name = column_plan.column.name
        if column_plan.mode in {"copy_from_source", "translated_text"}:
            source_columns.append(column_name)

    source_column_keys = {column_name.casefold() for column_name in source_columns}
    for entity_key_column_name in table.key_column_names:
        if entity_key_column_name.casefold() not in source_column_keys:
            source_columns.append(entity_key_column_name)
            source_column_keys.add(entity_key_column_name.casefold())

    deduped_source_columns: list[str] = []
    seen_source_columns: set[str] = set()
    for column_name in source_columns:
        normalized_name = column_name.casefold()
        if normalized_name in seen_source_columns or normalized_name in generated_columns:
            continue
        seen_source_columns.add(normalized_name)
        deduped_source_columns.append(column_name)

    return TableTranslationPlan(
        table=table,
        insert_columns=tuple(insert_columns),
        text_column_names=tuple(column.name for column in text_columns),
        source_column_names=tuple(deduped_source_columns),
    )
