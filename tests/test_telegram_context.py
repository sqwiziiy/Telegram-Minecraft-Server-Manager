import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from services.telegram_context import resolve_server, safe_edit_text  # noqa: E402


class TelegramContextTests(unittest.TestCase):
    def test_stale_or_crafted_server_id_resolves_to_none(self) -> None:
        self.assertIsNone(resolve_server("removed-server"))
        self.assertIsNone(resolve_server("storm-survival:injected"))
        self.assertIsNone(resolve_server(None))


class TelegramEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_edit_ignores_message_not_modified(self) -> None:
        message = SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=None,
                    message="Bad Request: message is not modified",
                )
            )
        )

        result = await safe_edit_text(message, "unchanged")

        self.assertIsNone(result)
        message.edit_text.assert_awaited_once_with("unchanged")

    async def test_safe_edit_reraises_other_bad_requests(self) -> None:
        message = SimpleNamespace(
            edit_text=AsyncMock(
                side_effect=TelegramBadRequest(
                    method=None,
                    message="Bad Request: message to edit not found",
                )
            )
        )

        with self.assertRaises(TelegramBadRequest):
            await safe_edit_text(message, "changed")


if __name__ == "__main__":
    unittest.main()
