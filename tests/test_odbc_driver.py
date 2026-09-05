from __future__ import annotations

import unittest

from translator_app.sqlserver.connection import is_odbc_driver_available


class OdbcDriverAvailabilityTests(unittest.TestCase):
    def test_finds_required_driver_case_insensitively(self) -> None:
        self.assertTrue(
            is_odbc_driver_available(
                ["SQL Server", "odbc driver 18 for sql server"],
            )
        )

    def test_returns_false_when_required_driver_is_missing(self) -> None:
        self.assertFalse(
            is_odbc_driver_available(
                ["SQL Server", "ODBC Driver 17 for SQL Server"],
            )
        )


if __name__ == "__main__":
    unittest.main()
