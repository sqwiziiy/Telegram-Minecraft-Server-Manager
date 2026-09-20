import asyncio
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services import rcon  # noqa: E402
from services import server_process  # noqa: E402


class ServerProcessManagerTests(unittest.TestCase):
    def _manager(self, server_dir: str, command: str) -> server_process.ServerProcessManager:
        with (
            patch.object(server_process, "SERVER_DIR", server_dir),
            patch.object(server_process, "SERVER_START_COMMAND", command),
            patch.object(
                server_process,
                "SERVER_PID_FILE",
                str(Path(server_dir) / ".manager.pid"),
            ),
            patch.object(
                server_process,
                "SERVER_OUTPUT_LOG",
                str(Path(server_dir) / "manager.log"),
            ),
        ):
            return server_process.ServerProcessManager()

    def test_start_sh_is_executed_through_bash_without_shell_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "start.sh"
            script.write_text("#!/usr/bin/env bash\nexec sleep 1\n", encoding="utf-8")

            manager = self._manager(tmp, "./start.sh --demo")

            self.assertEqual(
                manager._build_argv(),
                ["/bin/bash", str(script), "--demo"],
            )

    def test_direct_java_command_is_split_into_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(
                tmp,
                "java -Xms1G -Xmx2G -jar fabric-server-launch.jar nogui",
            )

            self.assertEqual(
                manager._build_argv(),
                [
                    "java",
                    "-Xms1G",
                    "-Xmx2G",
                    "-jar",
                    "fabric-server-launch.jar",
                    "nogui",
                ],
            )

    def test_stale_pid_record_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self._manager(tmp, "java -jar server.jar nogui")
            manager.pid_file.write_text(
                json.dumps({"pid": 999999999, "create_time": 1.0}),
                encoding="utf-8",
            )

            self.assertIsNone(manager._get_managed_process())
            self.assertFalse(manager.pid_file.exists())

    def test_uncertain_rcon_stop_response_gets_grace_period(self) -> None:
        self.assertTrue(
            server_process.ServerProcessManager._should_wait_after_rcon_stop(
                "❌ Сервер вернул некорректный RCON-ответ."
            )
        )
        self.assertTrue(
            server_process.ServerProcessManager._should_wait_after_rcon_stop(
                "❌ Таймаут ожидания ответа RCON."
            )
        )
        self.assertFalse(
            server_process.ServerProcessManager._should_wait_after_rcon_stop(
                "❌ RCON недоступен (127.0.0.1:25575)"
            )
        )


class RconPacketTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_packet_round_trip(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(rcon._pack(7, 2, "list"))
        reader.feed_eof()

        req_id, pkt_type, payload = await rcon._read_packet(reader)

        self.assertEqual((req_id, pkt_type, payload), (7, 2, "list"))

    async def test_oversized_packet_is_rejected_before_payload_read(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack("<i", rcon._MAX_PACKET_BYTES + 1))
        reader.feed_eof()

        with self.assertRaises(ValueError):
            await rcon._read_packet(reader)


if __name__ == "__main__":
    unittest.main()
