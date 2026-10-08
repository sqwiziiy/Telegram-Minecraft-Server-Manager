import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from keyboards.inline import server_home_keyboard, auto_tasks_keyboard  # noqa: E402
from services.access_control import access_control  # noqa: E402
from handlers.system import action_result_message  # noqa: E402


class AccessUiTests(unittest.TestCase):
    def test_action_result_message_preserves_killed_warning(self) -> None:
        self.assertIn("принудительно", action_result_message("killed"))
        self.assertIn("Готово", action_result_message("unexpected"))

    def test_server_home_hides_unpermitted_actions(self) -> None:
        server = SimpleNamespace(server_id="storm", server_name="Storm")
        allowed = {"server.status", "mods.view", "events.view"}
        with patch.object(access_control, "can_server", side_effect=lambda uid, sid, perm: perm in allowed):
            keyboard = server_home_keyboard(server, 222)
        callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}
        self.assertIn("status:storm", callbacks)
        self.assertIn("mods:storm", callbacks)
        self.assertIn("events:storm", callbacks)
        self.assertNotIn("manage:storm", callbacks)
        self.assertNotIn("console:storm", callbacks)
        self.assertNotIn("tasks:storm", callbacks)

    def test_operator_sees_auto_stop_but_not_scheduled_backups(self) -> None:
        server = SimpleNamespace(server_id="storm", server_name="Storm")
        with patch.object(access_control, "can_server", side_effect=lambda uid, sid, perm: perm == "server.autostop"):
            home = server_home_keyboard(server, 222)
            tasks = auto_tasks_keyboard("storm", 222)

        home_callbacks = {button.callback_data for row in home.inline_keyboard for button in row}
        task_callbacks = {button.callback_data for row in tasks.inline_keyboard for button in row}
        self.assertIn("tasks:storm", home_callbacks)
        self.assertIn("autostop:storm", task_callbacks)
        self.assertNotIn("autobackup:storm", task_callbacks)


if __name__ == "__main__":
    unittest.main()
