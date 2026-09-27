import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services import control_api  # noqa: E402
from services.server_process import ServerStatus  # noqa: E402


def _server(
    *,
    server_id: str = "storm-survival",
    server_name: str = "Storm Survival",
    running: bool = True,
    rcon_configured: bool = True,
):
    manager = SimpleNamespace(
        status=AsyncMock(
            return_value=ServerStatus(
                running=running,
                pid=1234 if running else None,
                uptime_seconds=90 if running else 0,
                memory_mb=512.5 if running else 0.0,
            )
        ),
        tail_output=AsyncMock(return_value="latest line"),
        start=AsyncMock(return_value="started"),
        stop=AsyncMock(return_value="stopped"),
        restart=AsyncMock(return_value="started"),
    )
    return SimpleNamespace(
        server_id=server_id,
        server_name=server_name,
        manager=manager,
        rcon_configured=rcon_configured,
        rcon=AsyncMock(
            side_effect=lambda command: (
                "There are 1 of a max of 4 players online: Steve"
                if command == "list"
                else f"ok:{command}"
            )
        ),
    )


class ControlApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_servers_returns_all_configured_servers(self) -> None:
        storm = _server()
        create = _server(
            server_id="create-coop",
            server_name="Create Coop",
            running=False,
            rcon_configured=False,
        )

        with patch.object(
            control_api.server_registry,
            "list",
            return_value=[storm, create],
        ):
            result = await control_api.minecraft_list_servers()

        self.assertEqual(len(result["servers"]), 2)
        self.assertEqual(result["servers"][0]["server_id"], "storm-survival")
        self.assertTrue(result["servers"][0]["running"])
        self.assertEqual(result["servers"][1]["server_id"], "create-coop")
        self.assertFalse(result["servers"][1]["running"])

    async def test_status_includes_identity_and_players(self) -> None:
        storm = _server()

        with patch.object(
            control_api.server_registry,
            "get",
            return_value=storm,
        ):
            result = await control_api.minecraft_status("storm-survival")

        self.assertEqual(result["server_id"], "storm-survival")
        self.assertEqual(result["server_name"], "Storm Survival")
        self.assertTrue(result["running"])
        self.assertTrue(result["rcon_configured"])
        self.assertEqual(
            result["players"],
            "There are 1 of a max of 4 players online: Steve",
        )

    async def test_logs_include_selected_server_identity(self) -> None:
        storm = _server()

        with patch.object(
            control_api.server_registry,
            "get",
            return_value=storm,
        ):
            result = await control_api.minecraft_logs("storm-survival", lines=10)

        self.assertEqual(
            result,
            {
                "server_id": "storm-survival",
                "server_name": "Storm Survival",
                "lines": 10,
                "content": "latest line",
            },
        )

    async def test_action_targets_selected_server(self) -> None:
        storm = _server()

        result = await control_api._action_result("start", storm)

        self.assertEqual(result["server_id"], "storm-survival")
        self.assertEqual(result["server_name"], "Storm Survival")
        self.assertEqual(result["result"], "started")
        storm.manager.start.assert_awaited_once()

    async def test_rcon_targets_selected_server(self) -> None:
        storm = _server()

        with patch.object(
            control_api.server_registry,
            "get",
            return_value=storm,
        ):
            result = await control_api.minecraft_rcon(
                control_api.RconCommandRequest(command="say hello"),
                "storm-survival",
            )

        self.assertEqual(result["server_id"], "storm-survival")
        self.assertEqual(result["command"], "say hello")
        self.assertEqual(result["response"], "ok:say hello")
        storm.rcon.assert_awaited_once_with("say hello")

    async def test_rcon_rejects_unconfigured_server(self) -> None:
        server = _server(rcon_configured=False)

        with patch.object(
            control_api.server_registry,
            "get",
            return_value=server,
        ):
            with self.assertRaises(control_api.HTTPException) as ctx:
                await control_api.minecraft_rcon(
                    control_api.RconCommandRequest(command="list"),
                    "storm-survival",
                )

        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
