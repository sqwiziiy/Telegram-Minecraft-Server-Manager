"""Editing the gameplay profile on existing servers must be non-destructive."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")

from scripts.manage_servers import run_wizard


class EditExistingSoftwareTests(unittest.TestCase):
    def test_change_existing_local_server_to_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "bot"
            project.mkdir()
            paper = Path(tmp) / "paper"
            paper.mkdir()
            (paper / "plugins").mkdir()
            original = {"servers": [
                {
                    "id": "paper-test", "name": "Paper Test",
                    "server_dir": str(paper),
                    "rcon_port": 25576,
                    "auto_stop_seconds": 120,
                    "backup_retention_max_count": 3,
                }
            ]}
            (project / "servers.json").write_text(json.dumps(original), encoding="utf-8")
            with (
                patch("builtins.input", side_effect=["3", "1", "", "да"]),
                patch("builtins.print"),
                patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": ""}),
            ):
                self.assertTrue(run_wizard(project))
            modified = json.loads((project / "servers.json").read_text(encoding="utf-8"))
            self.assertEqual(modified["servers"][0], {
                **original["servers"][0], "server_software": "plugins",
            })
            copies = list((project / "config_backups").glob("servers.json.bak-*"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(json.loads(copies[0].read_text()), original)

    def test_no_effect_when_type_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "bot"
            project.mkdir()
            mc = Path(tmp) / "fabric"
            mc.mkdir()
            (mc / "mods").mkdir()
            config = {"servers": [{"id": "fabric", "name": "Fabric",
                                   "server_dir": str(mc), "server_software": "mods"}]}
            filename = project / "servers.json"
            before = json.dumps(config)
            filename.write_text(before, encoding="utf-8")
            with (
                patch("builtins.input", side_effect=["3", "1", ""]),
                patch("builtins.print"),
                patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": ""}),
            ):
                self.assertFalse(run_wizard(project))
            self.assertEqual(filename.read_text(), before)
            self.assertFalse((project / "config_backups").exists())

    def test_update_remote_profile_without_changing_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "bot"
            project.mkdir()
            key = Path(tmp) / "id_ed25519"
            known_hosts = Path(tmp) / "known_hosts"
            key.write_text("fake key", encoding="utf-8")
            known_hosts.write_text("fake host", encoding="utf-8")
            original = {
                "servers": [{
                    "id": "paper-remote", "name": "Remote Paper",
                    "type": "ssh",
                    "server_dir": "/srv/minecraft/paper",
                    "ssh": {
                        "host": "example.local", "user": "minecraft",
                        "port": 22, "key_file": str(key),
                        "known_hosts": str(known_hosts),
                    },
                    "rcon_port": 25575,
                }]
            }
            (project / "servers.json").write_text(json.dumps(original), encoding="utf-8")
            async def fake_probe(self, action, **kwargs):
                self_actions = {"probe": {"has_mods": False, "has_plugins": True}}
                return self_actions[action]
            with (
                patch("builtins.input", side_effect=["3", "1", "", "да"]),
                patch("builtins.print"),
                patch("services.remote_ssh.SSHRemote.request", fake_probe),
                patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": ""}),
            ):
                self.assertTrue(run_wizard(project))
            saved = json.loads((project / "servers.json").read_text(encoding="utf-8"))["servers"][0]
            self.assertEqual(saved["server_software"], "plugins")
            self.assertEqual(saved["ssh"], original["servers"][0]["ssh"])
            self.assertEqual(saved["server_dir"], original["servers"][0]["server_dir"])
            self.assertEqual(saved["rcon_port"], 25575)


if __name__ == "__main__":
    unittest.main()
