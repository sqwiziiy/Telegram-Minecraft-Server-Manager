import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.auto_stop import (  # noqa: E402
    AutoStopManager,
    is_server_ready_line,
    parse_online_count,
)
from services.server_process import ServerStatus  # noqa: E402


def _server(*, list_response: str = "There are 0 of a max of 20 players online: "):
    manager = SimpleNamespace(
        status=AsyncMock(
            return_value=ServerStatus(
                running=True,
                pid=1234,
                uptime_seconds=60,
                memory_mb=512,
            )
        ),
        stop=AsyncMock(return_value="stopped"),
    )
    return SimpleNamespace(
        server_id="storm-survival",
        server_name="Storm Survival",
        auto_stop_seconds=0,
        manager=manager,
        rcon_configured=True,
        rcon=AsyncMock(return_value=list_response),
    )


class AutoStopParsingTests(unittest.TestCase):
    def test_parse_online_count(self) -> None:
        self.assertEqual(
            parse_online_count("There are 0 of a max of 20 players online: "),
            0,
        )
        self.assertEqual(
            parse_online_count("There are 2 of a max of 20 players online: Alex, Bea"),
            2,
        )
        self.assertIsNone(parse_online_count("RCON unavailable"))

    def test_server_ready_marker(self) -> None:
        self.assertTrue(
            is_server_ready_line(
                '[Server thread/INFO]: Done (2.345s)! For help, type "help"'
            )
        )
        self.assertFalse(
            is_server_ready_line(
                "[Server thread/INFO]: Alex joined the game"
            )
        )


class AutoStopManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_leave_arms_timer_and_join_cancels_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoStopManager(Path(tmp) / "state.json")
            server = _server()
            try:
                await manager.set_timeout(server, 120)
                self.assertTrue(manager.status(server)["pending"])

                await manager.handle_line(
                    server,
                    "[Server thread/INFO]: Alex joined the game",
                )
                self.assertFalse(manager.status(server)["pending"])

                await manager.handle_line(
                    server,
                    "[Server thread/INFO]: Alex left the game",
                )
                self.assertTrue(manager.status(server)["pending"])

                await manager.handle_line(
                    server,
                    "[Server thread/INFO]: Alex joined the game",
                )
                self.assertFalse(manager.status(server)["pending"])
            finally:
                await manager.shutdown()

    async def test_empty_server_is_stopped_after_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoStopManager(Path(tmp) / "state.json")
            server = _server(
                list_response="There are 0 of a max of 20 players online: "
            )

            await manager._verify_and_stop(server, 120)

            server.rcon.assert_awaited_once_with("list")
            server.manager.stop.assert_awaited_once()

    async def test_online_player_prevents_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoStopManager(Path(tmp) / "state.json")
            server = _server(
                list_response="There are 1 of a max of 20 players online: Alex"
            )

            await manager._verify_and_stop(server, 120)

            server.manager.stop.assert_not_awaited()

    async def test_unparseable_rcon_response_fails_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoStopManager(Path(tmp) / "state.json")
            server = _server(list_response="unexpected response")

            await manager._verify_and_stop(server, 120)

            server.manager.stop.assert_not_awaited()

    async def test_timeout_override_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            first = AutoStopManager(state_file)
            server = _server()
            try:
                await first.set_timeout(server, 120)
                self.assertEqual(first.timeout_seconds(server), 120)
            finally:
                await first.shutdown()

            second = AutoStopManager(state_file)
            try:
                self.assertEqual(second.timeout_seconds(server), 120)
            finally:
                await second.shutdown()


if __name__ == "__main__":
    unittest.main()
