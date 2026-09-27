import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services import control_api  # noqa: E402
from services.server_process import ServerStatus  # noqa: E402


class ControlApiIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_includes_server_identity(self) -> None:
        status = ServerStatus(
            running=True,
            pid=1234,
            uptime_seconds=90,
            memory_mb=512.5,
        )

        with (
            patch.object(control_api, "SERVER_ID", "storm-survival"),
            patch.object(control_api, "SERVER_NAME", "Storm Survival"),
            patch.object(
                control_api.server_process_manager,
                "status",
                new=AsyncMock(return_value=status),
            ),
            patch.object(
                control_api,
                "send_rcon_command",
                new=AsyncMock(return_value="There are 1 of a max of 4 players online: Steve"),
            ),
        ):
            result = await control_api.minecraft_status()

        self.assertEqual(result["server_id"], "storm-survival")
        self.assertEqual(result["server_name"], "Storm Survival")
        self.assertTrue(result["running"])
        self.assertEqual(result["players"], "There are 1 of a max of 4 players online: Steve")

    async def test_logs_include_server_identity(self) -> None:
        with (
            patch.object(control_api, "SERVER_ID", "storm-survival"),
            patch.object(control_api, "SERVER_NAME", "Storm Survival"),
            patch.object(
                control_api.server_process_manager,
                "tail_output",
                new=AsyncMock(return_value="latest line"),
            ),
        ):
            result = await control_api.minecraft_logs(lines=10)

        self.assertEqual(
            result,
            {
                "server_id": "storm-survival",
                "server_name": "Storm Survival",
                "lines": 10,
                "content": "latest line",
            },
        )

    async def test_actions_include_server_identity(self) -> None:
        status = ServerStatus(running=False)

        with (
            patch.object(control_api, "SERVER_ID", "storm-survival"),
            patch.object(control_api, "SERVER_NAME", "Storm Survival"),
            patch.object(
                control_api.server_process_manager,
                "start",
                new=AsyncMock(return_value="started"),
            ),
            patch.object(
                control_api.server_process_manager,
                "status",
                new=AsyncMock(return_value=status),
            ),
        ):
            result = await control_api._action_result("start")

        self.assertEqual(result["server_id"], "storm-survival")
        self.assertEqual(result["server_name"], "Storm Survival")
        self.assertEqual(result["result"], "started")


if __name__ == "__main__":
    unittest.main()
