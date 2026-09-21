from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from translator_app.config import RetrySettings, SqlServerConnectionSettings
from translator_app.languages import get_language
from translator_app.scheduled_job import (
    JobAlreadyRunning,
    JobFileLock,
    ScheduledJobConfig,
    build_job_config,
    build_parser,
    run_scheduled_job,
)
from translator_app.service import TranslationSummary
from translator_app.cleanup_service import CleanupSummary


class ScheduledJobTests(unittest.TestCase):
    def test_builds_noninteractive_config_with_target_queue_and_table(self) -> None:
        args = build_parser().parse_args(
            [
                "--connection-string",
                "DRIVER=test;SERVER=test;DATABASE=test",
                "--source-language-id",
                "1",
                "--target-language-ids",
                "2,3,2",
                "--cleanup-language-ids",
                "3",
                "--table",
                "dbo.SampleLocalize",
                "--dry-run",
            ]
        )

        config = build_job_config(args)

        self.assertEqual(config.source_language.code, "fa")
        self.assertEqual(tuple(item.code for item in config.target_languages), ("en", "ar"))
        self.assertEqual(tuple(item.code for item in config.cleanup_languages), ("ar",))
        self.assertEqual((config.schema_name, config.table_name), ("dbo", "SampleLocalize"))
        self.assertTrue(config.dry_run)

    def test_lock_prevents_a_second_process_lock_on_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "job.lock"
            with JobFileLock(path):
                with self.assertRaises(JobAlreadyRunning):
                    with JobFileLock(path, timeout_seconds=0):
                        pass

    def test_translation_finishes_before_cleanup(self) -> None:
        config = ScheduledJobConfig(
            connection=SqlServerConnectionSettings(
                connection_string="unused",
                driver="ODBC Driver 18 for SQL Server",
                server="",
                database="",
                username=None,
                password=None,
                trusted_connection=False,
                encrypt=True,
                trust_server_certificate=True,
            ),
            source_language=get_language(1),
            target_languages=(get_language(2),),
            cleanup_languages=(get_language(2),),
            schema_name=None,
            table_name=None,
            dry_run=True,
            batch_size=10,
            progress_every=1,
            translator_provider="auto",
            request_timeout_seconds=1,
            request_delay_seconds=0,
            libretranslate_url=None,
            libretranslate_api_key=None,
            log_dir=Path("logs"),
            retry=RetrySettings(attempts=1, initial_delay_seconds=0, backoff_factor=1),
            lock_file=Path(tempfile.gettempdir()) / "translator-scheduled-job-test.lock",
        )
        events: list[str] = []

        def translation(_config: ScheduledJobConfig, _logger: logging.Logger) -> TranslationSummary:
            events.append("translation")
            return TranslationSummary()

        def cleanup(_config: ScheduledJobConfig, _logger: logging.Logger) -> CleanupSummary:
            events.append("cleanup")
            return CleanupSummary()

        with patch("translator_app.scheduled_job._run_translation", translation), patch(
            "translator_app.scheduled_job._run_cleanup", cleanup
        ):
            run_scheduled_job(config, logger=logging.getLogger("scheduled-job-test"))

        self.assertEqual(events, ["translation", "cleanup"])


if __name__ == "__main__":
    unittest.main()
