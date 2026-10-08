"""Tests for the standalone SSH-side agent (no SSH server required)."""
import os
import socket
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from services import remote_agent


class RemoteAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "server"
        self.root.mkdir()
        self.world = self.root / "world"
        self.world.mkdir()
        (self.world / "level.dat").write_bytes(b"level data")
        self.mods = self.root / "mods"
        self.mods.mkdir()
        self.config = {
            "server_dir": str(self.root), "start_command": "./start-server.sh",
            "pid_file": str(self.root / "managed.pid"),
            "output_log": str(self.root / "logs" / "manager-console.log"),
            "world_dir": str(self.world),
            "mods_dir": str(self.mods),
            "backup_dir": str(self.root / "backups"),
            "rcon_port": 60001,
            "minecraft_port": 60002,
            "rcon_password": "test-secret",
            "backup_retention_max_count": 1,
            "backup_retention_max_gb": 0,
        }

    def test_probe_properties_and_launcher(self):
        (self.root / "start-server.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.root / "server.properties").write_text("server-port=25565\n", encoding="utf-8")
        payload = remote_agent.dispatch({"action": "probe", "config": self.config})
        self.assertEqual(payload["properties"], "server-port=25565\n")
        self.assertIn("start-server.sh", payload["launchers"])

    def test_properties_write_requires_unchanged_old_text(self):
        original = "max-players=4\n"
        updated = "max-players=4\nenable-rcon=true\n"
        (self.root / "server.properties").write_text(original, encoding="utf-8")
        base = {"action": "configure_properties", "config": self.config, "content": updated}
        with self.assertRaisesRegex(RuntimeError, "changed"):
            remote_agent.dispatch({**base, "expected_properties": "stale"})
        self.assertEqual((self.root / "server.properties").read_text(), original)
        self.assertTrue(remote_agent.dispatch({**base, "expected_properties": original}))
        self.assertEqual((self.root / "server.properties").read_text(), updated)

    def test_auto_backups_prune_old_only_and_preserve_manual(self):
        manual = self.root / "backups" / "world_backup_20261008_123456_123456.zip"
        manual.parent.mkdir()
        manual.write_bytes(b"manual")
        first = remote_agent.dispatch({
            "action": "backup", "config": self.config, "automatic": True,
        })
        second = remote_agent.dispatch({
            "action": "backup", "config": self.config, "automatic": True,
        })
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(second["deleted_count"], 1)
        self.assertFalse(Path(first["path"]).exists())
        self.assertTrue(Path(second["path"]).exists())
        self.assertEqual(manual.read_bytes(), b"manual")
        with zipfile.ZipFile(second["path"]) as archive:
            self.assertEqual(archive.read("world/level.dat"), b"level data")

    def test_auto_backup_blocks_when_remote_is_running(self):
        with patch.object(remote_agent, "get_status", return_value={"running": True}):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                remote_agent.dispatch({
                    "action": "backup", "config": self.config, "automatic": True,
                })
        self.assertFalse((self.root / "backups").exists())

    def test_mods_are_confined_and_cannot_follow_symlinks(self):
        (self.mods / "good.jar").write_bytes(b"valid")
        outside = Path(self.tmp.name) / "outside.jar"
        outside.write_bytes(b"outside")
        (self.mods / "bad.jar").symlink_to(outside)
        self.assertEqual(remote_agent.list_mods(self.config), ["good.jar"])
        with self.assertRaises(ValueError):
            remote_agent.remove_mod(self.config, "../outside.jar")
        with self.assertRaises(ValueError):
            remote_agent.remove_mod(self.config, "bad.jar")
        self.assertTrue(remote_agent.remove_mod(self.config, "good.jar"))
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_mod_upload_commits_atomically_without_overwriting(self):
        temporary_name = ".upload-" + "a" * 24
        (self.mods / temporary_name).write_bytes(b"new jar")
        self.assertEqual(remote_agent.commit_mod(self.config, "safe.jar", temporary_name), "safe.jar")
        self.assertEqual((self.mods / "safe.jar").read_bytes(), b"new jar")
        (self.mods / temporary_name).write_bytes(b"overwrite")
        with self.assertRaises(FileExistsError):
            remote_agent.commit_mod(self.config, "safe.jar", temporary_name)
        self.assertEqual((self.mods / "safe.jar").read_bytes(), b"new jar")

    def test_remote_file_browse_read_and_traversal_guard(self):
        (self.root / "server.properties").write_text(
            "rcon.password=hidden-secret\nmax-players=8\n", encoding="utf-8"
        )
        self.assertTrue(remote_agent.dispatch({
            "action": "list_files", "config": self.config, "path": ".",
        })["entries"])
        content = remote_agent.dispatch({
            "action": "read_file", "config": self.config,
            "path": "server.properties", "max_bytes": 10000,
        })["content"]
        self.assertIn("max-players=8", content)
        with self.assertRaises(ValueError):
            remote_agent.dispatch({"action": "read_file", "config": self.config,
                                   "path": "../other", "max_bytes": 1000})
        with self.assertRaises(PermissionError):
            (self.root / ".env").write_text("BOT_TOKEN=secret")
            remote_agent.dispatch({"action": "read_file", "config": self.config,
                                   "path": ".env", "max_bytes": 1000})

    def test_unmanaged_remote_game_port_is_not_reported_stopped(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            port = sock.getsockname()[1]
            self.config["minecraft_port"] = port
            with patch.object(remote_agent, "pid_record", return_value=None):
                state = remote_agent.get_status(self.config)
            self.assertTrue(state["running"])
            self.assertIsNone(state["pid"])


if __name__ == "__main__":
    unittest.main()
