import tempfile
import unittest
from pathlib import Path


class BootstrapTests(unittest.TestCase):
    def test_reimported_source_keeps_one_item_without_fabricated_schedule(self):
        from academic_os.config import Settings
        from academic_os.core import Core
        from academic_os.db import Database

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data_dir=Path(directory))
            core = Core(Database(settings.database_path), settings)
            record = {"source_id": "moodle:essay", "title": "Essay", "due_at": "2026-10-20T15:59:00Z"}
            core.ingest("moodle", [record])
            core.ingest("moodle", [record])
            self.assertEqual(len(core.list_items()), 1)
            self.assertIsNone(core.list_items()[0]["scheduled_start"])


if __name__ == "__main__":
    unittest.main()
