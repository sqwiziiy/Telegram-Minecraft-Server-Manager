import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.auto_backup import AutoBackupManager  # noqa: E402
from services.server_process import ServerStatus  # noqa: E402


def fake_server(running=False):
    manager = SimpleNamespace()
    manager.status = AsyncMock(return_value=ServerStatus(running=running))

    async def run_if_stopped(action):
        if (await manager.status()).running:
            return False, None
        return True, await action()

    manager.run_if_stopped = AsyncMock(side_effect=run_if_stopped)
    return SimpleNamespace(
        server_id="storm",
        server_name="Storm",
        manager=manager,
    )


class AutoBackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setting_and_disabling_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            server = fake_server()
            manager = AutoBackupManager(path)
            await manager.set_interval(server, 86400)
            self.assertEqual(manager.status(server)["interval_seconds"], 86400)
            self.assertFalse(manager.status(server)["pending"])

            restored = AutoBackupManager(path)
            self.assertEqual(restored.status(server)["interval_seconds"], 86400)
            await restored.set_interval(server, 0)
            self.assertFalse(AutoBackupManager(path).status(server)["enabled"])

    async def test_running_server_defers_one_backup_until_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            server = fake_server(running=True)
            manager = AutoBackupManager(path)
            await manager.set_interval(server, 21600)
            manager._schedules[server.server_id].next_due_at = time.time() - 3600

            with (
                patch("services.auto_backup.event_history.record", new_callable=AsyncMock),
                patch("services.auto_backup.create_backup", new_callable=AsyncMock, return_value="✅ ok") as backup,
            ):
                await manager.check_server(server)
                self.assertTrue(manager.status(server)["pending"])
                backup.assert_not_awaited()

                recovered = AutoBackupManager(path)
                self.assertTrue(recovered.status(server)["pending"])
                server.manager.status.return_value = ServerStatus(running=False)
                await recovered.check_server(server)
                backup.assert_awaited_once_with(server)
                self.assertFalse(recovered.status(server)["pending"])
                self.assertGreater(recovered.status(server)["next_due_at"], time.time())

                await recovered.check_server(server)
                backup.assert_awaited_once_with(server)

    async def test_overdue_after_restart_runs_when_server_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            server = fake_server()
            manager = AutoBackupManager(path)
            await manager.set_interval(server, 21600)
            manager._schedules[server.server_id].next_due_at = time.time() - 10
            await manager._persist()

            recovered = AutoBackupManager(path)
            with (
                patch("services.auto_backup.event_history.record", new_callable=AsyncMock),
                patch("services.auto_backup.create_backup", new_callable=AsyncMock, return_value="✅ ok") as backup,
            ):
                await recovered.check_server(server)
                backup.assert_awaited_once_with(server)
                self.assertFalse(recovered.status(server)["pending"])

    async def test_failed_backup_is_pending_with_retry_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = fake_server()
            manager = AutoBackupManager(Path(tmp) / "state.json")
            await manager.set_interval(server, 86400)
            manager._schedules[server.server_id].next_due_at = time.time() - 1
            with (
                patch("services.auto_backup.event_history.record", new_callable=AsyncMock),
                patch("services.auto_backup.create_backup", new_callable=AsyncMock, return_value="❌ failed") as backup,
            ):
                await manager.check_server(server)
                self.assertTrue(manager.status(server)["pending"])
                self.assertTrue(AutoBackupManager(manager.state_file).status(server)["pending"])
                await manager.check_server(server)
                backup.assert_awaited_once_with(server)

    async def test_invalid_interval_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoBackupManager(Path(tmp) / "state.json")
            with self.assertRaises(ValueError):
                await manager.set_interval(fake_server(), 1)

    async def test_scheduler_shutdown_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = fake_server()
            manager = AutoBackupManager(Path(tmp) / "state.json")
            manager.start([server])
            await manager.shutdown()
            self.assertIsNone(manager._task)


if __name__ == "__main__":
    unittest.main()
