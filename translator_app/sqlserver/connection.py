from __future__ import annotations


def connect(connection_string: str, *, autocommit: bool) -> object:
    try:
        import pyodbc
    except ImportError as exc:
        message = str(exc)
        if "libodbc" in message:
            raise RuntimeError(
                "pyodbc is installed, but the system ODBC library was not found. "
                "On Linux, install unixODBC and Microsoft ODBC Driver for SQL Server. "
                "On Windows, install Microsoft ODBC Driver 18 for SQL Server."
            ) from exc
        raise RuntimeError(
            "pyodbc is not installed. Run `pip install -r requirements.txt` first."
        ) from exc

    return pyodbc.connect(connection_string, timeout=30, autocommit=autocommit)
