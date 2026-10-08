import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")

from scripts.manage_servers import run_wizard  # noqa: E402
from services.remote_ssh import RemoteServerProcessManager, SSHSettings  # noqa: E402


class SSHWizardTests(unittest.TestCase):
    def test_ssh_connection_requires_known_hosts_and_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key = root / "id"
            known = root / "known_hosts"
            key.write_text("not-a-real-key", encoding="utf-8")
            settings = SSHSettings("remote.example", "mc", key_file=str(key), known_hosts=str(known))
            with self.assertRaises(FileNotFoundError):
                settings.validate()
            known.write_text("hashed-example", encoding="utf-8")
            settings.validate()

    def test_remote_wizard_saves_config_and_updates_remote_properties(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "bot"
            project.mkdir()
            local = root / "local"
            local.mkdir()
            key = root / "id_ed25519"
            known = root / "known_hosts"
            key.write_text("fake-for-test", encoding="utf-8")
            known.write_text("fake-host-key", encoding="utf-8")
            (project / ".env").write_text('BOT_TOKEN="already-present"\n', encoding="utf-8")
            original = {"servers": [{
                "id": "storm-survival", "name": "Storm Survival", "server_dir": str(local),
                "rcon_port": 25575,
            }]}
            (project / "servers.json").write_text(json.dumps(original), encoding="utf-8")
            updates = []
            remote_properties = "server-port=25565\nrcon.port=25575\nrcon.password=existing\nmax-players=4\n"
            async def fake_request(self, action, **params):
                if action == "probe":
                    return {"properties": remote_properties, "launchers": ["start-server.sh"]}
                if action == "port_available":
                    return True
                if action == "configure_properties":
                    updates.append(params)
                    return True
                raise AssertionError(action)
            inputs = [
                "2", "remote.example", "mc", "", str(key), str(known),
                "/home/mc/test-server", "", "", "", "25566", "25576",
                "да", "да",
            ]
            with (
                patch("services.remote_ssh.SSHRemote.request", fake_request),
                patch("builtins.input", side_effect=inputs),
                patch("sys.stdout", new_callable=io.StringIO),
                patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": ""}),
            ):
                self.assertTrue(run_wizard(project))

            data = json.loads((project / "servers.json").read_text(encoding="utf-8"))
            self.assertEqual(data["servers"][0], original["servers"][0])
            added = data["servers"][1]
            self.assertEqual(added["id"], "test-server")
            self.assertEqual(added["type"], "ssh")
            self.assertEqual(added["ssh"]["host"], "remote.example")
            self.assertEqual(added["ssh"]["user"], "mc")
            self.assertEqual(added["minecraft_port"], 25566)
            self.assertEqual(added["rcon_port"], 25576)
            self.assertIn('TEST_SERVER_RCON_PASSWORD="existing"',
                          (project / ".env").read_text(encoding="utf-8"))
            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0]["expected_properties"], remote_properties)
            self.assertIn("enable-rcon=true", updates[0]["content"])
            self.assertIn("max-players=4", updates[0]["content"])
            folder = project / "config_backups"
            self.assertEqual(len(list(folder.glob("test-server-server.properties.bak-*"))), 1)
            self.assertEqual(len(list(folder.glob("servers.json.bak-*"))), 1)
            self.assertEqual(len(list(folder.glob(".env.bak-*"))), 1)

    def test_remote_wizard_cancel_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "bot"
            project.mkdir()
            key = root / "key"
            known = root / "known_hosts"
            key.write_text("key")
            known.write_text("host")
            (project / "servers.json").write_text('{"servers":[]}', encoding="utf-8")
            (project / ".env").write_text('BOT_TOKEN="same"', encoding="utf-8")
            async def fake_request(self, action, **params):
                if action == "probe":
                    return {"properties": "", "launchers": ["start.sh"]}
                if action == "port_available":
                    return True
                raise AssertionError("Cancelled wizard must not make remote changes")
            choices = [
                "2", "remote.example", "mc", "", str(key), str(known),
                "/srv/minecraft", "", "", "", "", "", "", "нет",
            ]
            with (
                patch("services.remote_ssh.SSHRemote.request", fake_request),
                patch("builtins.input", side_effect=choices),
                patch("sys.stdout", new_callable=io.StringIO),
                patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": ""}),
            ):
                self.assertFalse(run_wizard(project))
            self.assertEqual((project / "servers.json").read_text(), '{"servers":[]}')
            self.assertEqual((project / ".env").read_text(), 'BOT_TOKEN="same"')
            self.assertFalse((project / "config_backups").exists())


class RemoteManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_uses_ssh_data_not_local_files(self):
        fake = SimpleNamespace(
            config={"server_dir": "/remote/minecraft", "output_log": "/remote/minecraft/logs/out.log"},
            request=AsyncMock(return_value={
                "running": True, "pid": 9123, "uptime_seconds": 300, "memory_mb": 300.5
            }),
        )
        manager = RemoteServerProcessManager(fake)
        status = await manager.status()
        self.assertTrue(status.running)
        self.assertEqual(status.pid, 9123)
        self.assertEqual(status.uptime_seconds, 300)
        fake.request.assert_awaited_once_with("status", timeout=25)

    async def test_connection_failure_cannot_be_seen_as_stopped(self):
        fake = SimpleNamespace(
            config={"server_dir": "/remote/server", "output_log": "/remote/server/out.log"},
            request=AsyncMock(side_effect=ConnectionError("SSH offline")),
        )
        manager = RemoteServerProcessManager(fake)
        with self.assertRaises(ConnectionError):
            await manager.run_if_stopped(AsyncMock())


if __name__ == "__main__":
    unittest.main()
