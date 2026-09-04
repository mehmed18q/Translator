from __future__ import annotations


RESOURCE_SCHEMA_NAME = "dbo"
RESOURCE_TABLE_NAME = "Resource"
RESOURCE_KEY_COLUMN_NAME = "Key"
RESOURCE_VALUE_COLUMN_NAME = "Value"
SYSTEM_MESSAGES_SCHEMA_NAME = "dbo"
SYSTEM_MESSAGES_TABLE_NAME = "SystemMessages"
SYSTEM_MESSAGES_STATE_COLUMN_NAME = "SystemMessageStateId"
SYSTEM_MESSAGES_KEY_COLUMN_NAME = "MessageKey"
SYSTEM_MESSAGES_VALUE_COLUMN_NAME = "Value"


def is_resource_table(schema_name: str, table_name: str) -> bool:
    return (
        schema_name.casefold() == RESOURCE_SCHEMA_NAME.casefold()
        and table_name.casefold() == RESOURCE_TABLE_NAME.casefold()
    )


def is_system_messages_table(schema_name: str, table_name: str) -> bool:
    return (
        schema_name.casefold() == SYSTEM_MESSAGES_SCHEMA_NAME.casefold()
        and table_name.casefold() == SYSTEM_MESSAGES_TABLE_NAME.casefold()
    )


def is_special_translation_table(schema_name: str, table_name: str) -> bool:
    return is_resource_table(schema_name, table_name) or is_system_messages_table(
        schema_name,
        table_name,
    )


def is_special_translatable_column(
    schema_name: str,
    table_name: str,
    column_name: str,
) -> bool:
    normalized_column_name = column_name.casefold()
    return (
        (
            is_resource_table(schema_name, table_name)
            and normalized_column_name == RESOURCE_VALUE_COLUMN_NAME.casefold()
        )
        or (
            is_system_messages_table(schema_name, table_name)
            and normalized_column_name == SYSTEM_MESSAGES_VALUE_COLUMN_NAME.casefold()
        )
    )
