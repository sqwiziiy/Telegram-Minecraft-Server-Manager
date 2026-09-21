import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove  # noqa: E402
from keyboards.inline import mods_list_keyboard, system_keyboard  # noqa: E402
from keyboards.main_menu import get_main_menu  # noqa: E402
from services.access_control import access_control  # noqa: E402


class AccessUiTests(unittest.TestCase):
    def test_operator_ui_hides_privileged_actions(self) -> None:
        allowed = {
            "server.status",
            "server.start",
            "server.stop",
            "server.restart",
            "mods.view",
        }

        with patch.object(
            access_control,
            "can",
            side_effect=lambda user_id, permission: user_id == 222 and permission in allowed,
        ):
            main_menu = get_main_menu(222)
            self.assertIsInstance(main_menu, ReplyKeyboardMarkup)
            main_texts = {
                button.text
                for row in main_menu.keyboard
                for button in row
            }
            self.assertIn("📊 Статус", main_texts)
            self.assertIn("⚙️ Система", main_texts)
            self.assertIn("🧩 Моды", main_texts)
            self.assertNotIn("💻 Консоль", main_texts)

            system_menu = system_keyboard(222)
            callbacks = {
                button.callback_data
                for row in system_menu.inline_keyboard
                for button in row
            }
            self.assertIn("confirm_start", callbacks)
            self.assertIn("confirm_stop", callbacks)
            self.assertIn("confirm_restart", callbacks)
            self.assertIn("refresh_system", callbacks)
            self.assertNotIn("server_logs", callbacks)
            self.assertNotIn("create_backup", callbacks)

            self.assertIsNone(mods_list_keyboard(5, 222))

    def test_user_without_visible_permissions_has_keyboard_removed(self) -> None:
        with patch.object(access_control, "can", return_value=False):
            menu = get_main_menu(222)

        self.assertIsInstance(menu, ReplyKeyboardRemove)


if __name__ == "__main__":
    unittest.main()
