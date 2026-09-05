from __future__ import annotations

from collections.abc import Iterable


REQUIRED_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


def installed_odbc_drivers() -> tuple[str, ...]:
    pyodbc = _import_pyodbc()
    return tuple(str(driver) for driver in pyodbc.drivers())


def is_odbc_driver_available(
    installed_drivers: Iterable[str],
    required_driver: str = REQUIRED_ODBC_DRIVER,
) -> bool:
    required_name = required_driver.strip().casefold()
    return any(driver.strip().casefold() == required_name for driver in installed_drivers)


def connect(connection_string: str, *, autocommit: bool) -> object:
    pyodbc = _import_pyodbc()
    return pyodbc.connect(connection_string, timeout=30, autocommit=autocommit)


def _import_pyodbc() -> object:
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
    return pyodbc
