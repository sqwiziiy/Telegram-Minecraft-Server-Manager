import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from services.event_feed import EventFeed, parse_minecraft_event  # noqa: E402


class EventParsingTests(unittest.TestCase):
    def test_join_event_strips_minecraft_prefix(self) -> None:
        event = parse_minecraft_event(
            "[11:40:57] [Server thread/INFO]: MrMentality joined the game"
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "join")
        self.assertEqual(event.timestamp, "11:40")
        self.assertEqual(event.player, "MrMentality")

    def test_leave_event(self) -> None:
        event = parse_minecraft_event(
            "[Server thread/INFO]: Friend left the game"
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "leave")
        self.assertEqual(event.player, "Friend")

    def test_chat_event(self) -> None:
        event = parse_minecraft_event(
            "[22:51:10] [Server thread/INFO]: <Alex> hello <world>"
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "chat")
        self.assertEqual(event.player, "Alex")
        self.assertEqual(event.detail, "hello <world>")

    def test_death_event(self) -> None:
        event = parse_minecraft_event(
            "[11:43:36] [Server thread/INFO]: MrMentality was slain by Zombie"
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "death")
        self.assertEqual(event.player, "MrMentality")
        self.assertEqual(event.detail, "MrMentality was slain by Zombie")

    def test_unrelated_line_is_ignored(self) -> None:
        self.assertIsNone(
            parse_minecraft_event(
                "[11:43:36] [Server thread/INFO]: Done (2.123s)! For help, type \"help\""
            )
        )


class EventFeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_event_sends_message_second_event_edits_it(self) -> None:
        feed = EventFeed(max_events=10)
        bot = SimpleNamespace(
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=55)),
            edit_message_text=AsyncMock(),
        )

        await feed.publish_line(
            bot,
            server_id="storm",
            server_name="Storm Survival",
            line="[10:00:00] [Server thread/INFO]: Alex joined the game",
            user_ids=[123],
        )
        await feed.publish_line(
            bot,
            server_id="storm",
            server_name="Storm Survival",
            line="[10:01:00] [Server thread/INFO]: Alex left the game",
            user_ids=[123],
        )

        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        kwargs = bot.edit_message_text.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 123)
        self.assertEqual(kwargs["message_id"], 55)
        self.assertIn("Alex", kwargs["text"])
        self.assertIn("зашёл", kwargs["text"])
        self.assertIn("вышел", kwargs["text"])

    async def test_feed_keeps_only_last_ten_events(self) -> None:
        feed = EventFeed(max_events=10)

        for index in range(12):
            feed.add_line(
                "storm",
                f"[10:{index:02d}:00] [Server thread/INFO]: Player{index} joined the game",
            )

        text = feed.render("storm", "Storm Survival")
        self.assertNotIn("Player0", text)
        self.assertNotIn("Player1", text)
        self.assertIn("Player2", text)
        self.assertIn("Player11", text)
        self.assertIn("Последние 10 из 10 событий", text)

    async def test_html_is_escaped(self) -> None:
        feed = EventFeed()
        feed.add_line(
            "storm",
            "[10:00:00] [Server thread/INFO]: <Alex> <script>alert(1)</script>",
        )

        text = feed.render("storm", "Storm <Survival>")
        self.assertIn("Storm &lt;Survival&gt;", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertNotIn("<script>", text)

    async def test_deleted_feed_message_is_recreated(self) -> None:
        feed = EventFeed()
        bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=[
                    SimpleNamespace(message_id=55),
                    SimpleNamespace(message_id=77),
                ]
            ),
            edit_message_text=AsyncMock(),
        )

        await feed.publish_line(
            bot,
            server_id="storm",
            server_name="Storm Survival",
            line="[10:00:00] [Server thread/INFO]: Alex joined the game",
            user_ids=[123],
        )

        bot.edit_message_text.side_effect = TelegramBadRequest(
            method=None,
            message="Bad Request: message to edit not found",
        )

        await feed.publish_line(
            bot,
            server_id="storm",
            server_name="Storm Survival",
            line="[10:01:00] [Server thread/INFO]: Alex left the game",
            user_ids=[123],
        )

        self.assertEqual(bot.send_message.await_count, 2)
        self.assertEqual(feed._message_ids[(123, "storm")], 77)


if __name__ == "__main__":
    unittest.main()
