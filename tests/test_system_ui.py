import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from handlers import system  # noqa: E402
from keyboards.inline import back_to_server_keyboard, logs_keyboard, auto_backup_keyboard, auto_stop_keyboard, auto_tasks_keyboard  # noqa: E402


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

    def test_auto_backup_keyboard_has_choices_and_navigation(self) -> None:
        keyboard = auto_backup_keyboard("storm", 86400)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        callbacks = {button.callback_data for button in buttons}
        self.assertIn("autobackup_set:86400:storm", callbacks)
        self.assertIn("autobackup_set:0:storm", callbacks)
        self.assertIn("tasks:storm", callbacks)
        self.assertTrue(next(
            button.text.startswith("✅") for button in buttons
            if button.callback_data == "autobackup_set:86400:storm"
        ))
        stop_buttons = [button.callback_data for row in auto_stop_keyboard("storm", 0).inline_keyboard for button in row]
        self.assertIn("tasks:storm", stop_buttons)

    def test_auto_task_refresh_controls_exist(self) -> None:
        backup_buttons = [b.callback_data for row in auto_backup_keyboard("storm", 21600).inline_keyboard for b in row]
        self.assertIn("autobackup:storm", backup_buttons)
        task_buttons = [b.callback_data for row in auto_tasks_keyboard("storm", 123456789).inline_keyboard for b in row]
        self.assertIn("tasks:storm", task_buttons)

    def test_auto_backup_ui_shows_active_and_last_success(self) -> None:
        server = SimpleNamespace(server_id="storm", server_name="Storm")
        status = {
            "enabled": True,
            "interval_seconds": 21600,
            "next_due_at": 1720000000.0,
            "pending": True,
            "running": True,
            "started_at": 1720000000.0,
            "retry_in_seconds": 0,
            "last_completed_at": 1719900000.0,
            "last_size_mb": 48.2,
            "last_failed_at": None,
        }
        with patch.object(system.auto_backup_manager, "status", return_value=status):
            body = system._auto_backup_text(server)
        self.assertIn("Создаётся бэкап", body)
        self.assertIn("48.2 МБ", body)

    def test_auto_backup_ui_distinguishes_error_from_waiting(self) -> None:
        status = {
            "enabled": True,
            "pending": True,
            "running": False,
            "last_failed_at": 1720000000.0,
            "retry_in_seconds": 299,
        }
        self.assertIn("ошибкой", system._auto_backup_status_line(status))
        status["last_failed_at"] = None
        self.assertIn("ожидает остановки", system._auto_backup_status_line(status))

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
