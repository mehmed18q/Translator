from __future__ import annotations


RESOURCE_SCHEMA_NAME = "dbo"
RESOURCE_TABLE_NAME = "Resource"
RESOURCE_KEY_COLUMN_NAME = "Key"
RESOURCE_VALUE_COLUMN_NAME = "Value"


def is_resource_table(schema_name: str, table_name: str) -> bool:
    return (
        schema_name.casefold() == RESOURCE_SCHEMA_NAME.casefold()
        and table_name.casefold() == RESOURCE_TABLE_NAME.casefold()
    )


def is_special_translatable_column(
    schema_name: str,
    table_name: str,
    column_name: str,
) -> bool:
    return (
        is_resource_table(schema_name, table_name)
        and column_name.casefold() == RESOURCE_VALUE_COLUMN_NAME.casefold()
    )
