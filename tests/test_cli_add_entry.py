from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from scripts import add_entry as add_entry_script
import app.db


class TestCliAddEntryScript(unittest.TestCase):
    def test_main_creates_entry_end_to_end(self) -> None:
        fd, path = tempfile.mkstemp(prefix="eventtracker_test_", suffix=".db")
        os.close(fd)
        try:
            os.environ["EVENTTRACKER_DB_PATH"] = path

            args = add_entry_script.argparse.Namespace(
                group_id="default",
                title="Test Entry",
                year=2021,
                month=7,
                day=14,
                final_text="This is a test summary.",
                tags="tag1, tag2",
                source_url=None,
                links_json=None,
                link=None,
                dry_run=False,
            )

            with patch("scripts.add_entry.parse_args", return_value=args):
                with patch("scripts.add_entry.load_dotenv"):
                    # initialize schema for the temp DB
                    app.db.init_db()
                    # run the CLI main to insert the entry
                    add_entry_script.main()

            # verify an entry exists with expected values
            with app.db.connection_context() as conn:
                row = conn.execute("SELECT * FROM entries LIMIT 1").fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row["title"], "Test Entry")
            self.assertEqual(row["final_text"], "This is a test summary.")
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    def test_main_validation_error_missing_required(self) -> None:
        args = add_entry_script.argparse.Namespace(
            group_id=1,
            title="",
            year=2021,
            month=7,
            day=None,
            final_text="",
            tags="",
            source_url=None,
            links_json=None,
            link=None,
            dry_run=False,
        )

        with patch("scripts.add_entry.parse_args", return_value=args):
            with patch("scripts.add_entry.load_dotenv"):
                with self.assertRaises(SystemExit) as ctx:
                    add_entry_script.main()

        # SystemExit.code should be non-zero for validation failure
        code = getattr(ctx.exception, "code", None)
        self.assertTrue(code is None or code != 0)


if __name__ == "__main__":
    unittest.main()
