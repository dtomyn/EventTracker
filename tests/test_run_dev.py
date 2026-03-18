from __future__ import annotations

import atexit
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_dev


class TestRunDev(unittest.TestCase):
    def test_load_settings_uses_override_false_and_respects_shell_values(self) -> None:
        args = run_dev.argparse.Namespace(host="", port=None, reload=False)

        with patch.dict(
            os.environ,
            {
                "EVENTTRACKER_HOST": "0.0.0.0",
                "EVENTTRACKER_PORT": "4000",
            },
            clear=True,
        ):
            with patch("scripts.run_dev.load_dotenv") as load_dotenv_mock:
                host, port = run_dev.load_settings(args)

        self.assertEqual(host, "0.0.0.0")
        self.assertEqual(port, 4000)
        self.assertFalse(load_dotenv_mock.call_args.kwargs["override"])

    def test_load_settings_allows_cli_overrides(self) -> None:
        args = run_dev.argparse.Namespace(host="127.0.0.2", port=4010, reload=False)

        with patch.dict(
            os.environ,
            {
                "EVENTTRACKER_HOST": "0.0.0.0",
                "EVENTTRACKER_PORT": "4000",
            },
            clear=True,
        ):
            with patch("scripts.run_dev.load_dotenv"):
                host, port = run_dev.load_settings(args)

        self.assertEqual(host, "127.0.0.2")
        self.assertEqual(port, 4010)

    def test_prepare_reload_session_stops_prior_windows_reloader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / run_dev.RELOAD_PID_FILE
            pid_path.write_text("1234", encoding="utf-8")

            with patch("scripts.run_dev.sys.platform", "win32"):
                with patch("scripts.run_dev._reload_pid_path", return_value=pid_path):
                    with patch("scripts.run_dev.os.getpid", return_value=5678):
                        with patch(
                            "scripts.run_dev._is_process_running", return_value=True
                        ):
                            with patch(
                                "scripts.run_dev._terminate_process_tree"
                            ) as terminate_mock:
                                with patch.object(atexit, "register") as register_mock:
                                    run_dev._prepare_reload_session()

                                    terminate_mock.assert_called_once_with(1234)
                                    self.assertEqual(
                                        pid_path.read_text(encoding="utf-8"), "5678"
                                    )
                                    register_mock.assert_called_once()

    def test_ensure_port_available_raises_clear_error(self) -> None:
        with patch("scripts.run_dev.socket.socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.bind.side_effect = (
                OSError("in use")
            )

            with self.assertRaises(SystemExit) as context:
                run_dev._ensure_port_available("127.0.0.1", 35231)

        self.assertIn("The port is already in use", str(context.exception))
