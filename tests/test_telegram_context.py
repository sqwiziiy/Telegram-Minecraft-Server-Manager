import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.telegram_context import resolve_server  # noqa: E402


class TelegramContextTests(unittest.TestCase):
    def test_stale_or_crafted_server_id_resolves_to_none(self) -> None:
        self.assertIsNone(resolve_server("removed-server"))
        self.assertIsNone(resolve_server("storm-survival:injected"))
        self.assertIsNone(resolve_server(None))


if __name__ == "__main__":
    unittest.main()
