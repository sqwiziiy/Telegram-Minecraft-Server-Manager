import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.event_history import EventHistory, parse_minecraft_event  # noqa: E402


class EventParsingTests(unittest.TestCase):
    def test_join_event_strips_minecraft_prefix(self) -> None:
        event = parse_minecraft_event(
            "[11:40:57] [Server thread/INFO]: MrMentality joined the game"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "join")
        self.assertEqual(event.timestamp, "11:40:57")
        self.assertEqual(event.player, "MrMentality")

    def test_death_event(self) -> None:
        event = parse_minecraft_event(
            "[11:43:36] [Server thread/INFO]: MrMentality was slain by Zombie"
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "death")
        self.assertEqual(event.detail, "MrMentality was slain by Zombie")

    def test_server_ready_is_an_event(self) -> None:
        event = parse_minecraft_event(
            '[11:43:36] [Server thread/INFO]: Done (2.123s)! For help, type "help"'
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.kind, "server_ready")

    def test_unrelated_line_is_ignored(self) -> None:
        self.assertIsNone(
            parse_minecraft_event("[11:43:36] [Server thread/INFO]: Preparing spawn area")
        )


class EventHistoryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _server(root: str):
        return SimpleNamespace(
            server_id="storm",
            server_name="Storm Survival",
            manager=SimpleNamespace(server_dir=Path(root)),
        )

    async def test_history_is_written_to_daily_file_and_survives_new_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._server(tmp)
            history = EventHistory()

            await history.record_line(
                server,
                "[10:00:00] [Server thread/INFO]: Alex joined the game",
            )
            await history.record_line(
                server,
                "[10:01:00] [Server thread/INFO]: Alex left the game",
            )

            path = history.today_path(server)
            self.assertTrue(path.is_file())
            content = path.read_text(encoding="utf-8")
            self.assertIn("[join] Alex зашёл на сервер", content)
            self.assertIn("[leave] Alex вышел с сервера", content)

            fresh_history = EventHistory()
            events = await fresh_history.recent(server, limit=20)
            self.assertEqual([event.kind for event in events], ["join", "leave"])
            self.assertEqual(events[-1].text, "Alex вышел с сервера")

    async def test_action_history_records_source_and_actor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._server(tmp)
            history = EventHistory()

            await history.record_action(
                server,
                action="start",
                result="started",
                source="Telegram",
                actor="Friend (@friend), id=222",
            )

            events = await history.recent(server)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].kind, "server_start")
            self.assertIn("Telegram", events[0].text)
            self.assertIn("Friend", events[0].text)

    async def test_no_state_change_action_is_not_recorded_as_start_or_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._server(tmp)
            history = EventHistory()

            result = await history.record_action(
                server,
                action="start",
                result="already_running",
                source="Telegram",
            )

            self.assertIsNone(result)
            self.assertEqual(await history.recent(server), [])

    async def test_render_escapes_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._server(tmp)
            history = EventHistory()
            await history.record(
                server,
                kind="chat",
                text="<Alex>: <script>alert(1)</script>",
            )

            text = history.render(await history.recent(server))
            self.assertIn("&lt;script&gt;", text)
            self.assertNotIn("<script>", text)


if __name__ == "__main__":
    unittest.main()
