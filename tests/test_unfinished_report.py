from __future__ import annotations

import unittest

from translator_app.unfinished_report import format_unfinished_report


class UnfinishedReportTests(unittest.TestCase):
    def test_formats_deduplicates_and_sorts_records_for_people(self) -> None:
        records = [
            "table=dbo.ZLocalize | row=Id=9 | status=failed | reason=API error | language=en",
            "table=dbo.ALocalize | rows=3 | status=stopped | reason=user stopped | language=fa",
            "table=dbo.ALocalize | row=Id=2 | status=timed out | reason=request timed out | language=fa",
            "table=dbo.ALocalize | row=Id=2 | status=timed out | reason=request timed out | language=fa",
        ]

        report = format_unfinished_report(
            records,
            operation_name="Database translation",
        )

        self.assertIn("END-OF-RUN UNFINISHED RECORDS REPORT", report)
        self.assertIn("Total     : 3", report)
        self.assertIn("failed=1, stopped=1, timed out=1", report)
        self.assertLess(report.index("Record          : Id=2"), report.index("Affected rows   : 3"))
        self.assertLess(report.index("dbo.ALocalize"), report.index("dbo.ZLocalize"))
        self.assertIn("Language        : fa", report)
        self.assertIn("Status          : TIMED OUT", report)

    def test_empty_report_explicitly_confirms_completion(self) -> None:
        report = format_unfinished_report(
            [],
            operation_name="RESX translation",
        )

        self.assertIn("Total     : 0", report)
        self.assertIn("No failed, stopped, skipped, or unprocessed items", report)

    def test_wraps_long_reasons_on_indented_lines(self) -> None:
        report = format_unfinished_report(
            [
                "file=Resources.resx | key=Welcome | status=failed | reason="
                + "very long explanation " * 12
            ],
            operation_name="RESX translation",
        )

        reason_lines = [line for line in report.splitlines() if "very long" in line]
        self.assertGreater(len(reason_lines), 1)
        self.assertTrue(all(len(line) <= 88 for line in reason_lines))


if __name__ == "__main__":
    unittest.main()
