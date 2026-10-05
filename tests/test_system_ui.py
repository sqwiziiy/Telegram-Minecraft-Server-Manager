import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from handlers import system  # noqa: E402
from keyboards.inline import back_to_server_keyboard, logs_keyboard  # noqa: E402


class SystemUiTests(unittest.TestCase):
    def test_logs_keyboard_has_refresh_and_back(self) -> None:
        keyboard = logs_keyboard("storm-survival")
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertEqual(
            callbacks,
            ["logs:storm-survival", "sv:storm-survival"],
        )

    def test_backup_result_keyboard_returns_to_server(self) -> None:
        keyboard = back_to_server_keyboard("storm-survival")
        self.assertEqual(
            keyboard.inline_keyboard[0][0].callback_data,
            "sv:storm-survival",
        )

    def test_host_disk_uses_configured_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_usage = object()
            with (
                patch.object(system, "HOST_DISK_PATH", tmp),
                patch.object(system.psutil, "cpu_percent", return_value=1.0),
                patch.object(system.psutil, "virtual_memory", return_value=object()),
                patch.object(system.psutil, "disk_usage", return_value=fake_usage) as disk_usage,
            ):
                info = system._collect_host_info()

            self.assertEqual(info["disk_path"], tmp)
            self.assertIs(info["disk"], fake_usage)
            disk_usage.assert_called_once_with(tmp)

    def test_missing_host_disk_path_falls_back_to_root(self) -> None:
        missing = str(Path("/definitely/not/a/real/path/for/mc-manager"))
        with (
            patch.object(system, "HOST_DISK_PATH", missing),
            patch.object(system.psutil, "cpu_percent", return_value=1.0),
            patch.object(system.psutil, "virtual_memory", return_value=object()),
            patch.object(system.psutil, "disk_usage", return_value=object()) as disk_usage,
        ):
            info = system._collect_host_info()

        self.assertEqual(info["disk_path"], "/")
        disk_usage.assert_called_once_with("/")


if __name__ == "__main__":
    unittest.main()
