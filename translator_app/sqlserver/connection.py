from __future__ import annotations


def connect(connection_string: str, *, autocommit: bool) -> object:
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError(
            "پکیج pyodbc نصب نیست. ابتدا `pip install -r requirements.txt` را اجرا کنید."
        ) from exc

    return pyodbc.connect(connection_string, timeout=30, autocommit=autocommit)
