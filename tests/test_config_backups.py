import os
import tempfile
import unittest
from pathlib import Path

from scripts.config_backups import save_config_backup


class ConfigBackupTests(unittest.TestCase):
    def test_backups_live_in_private_directory_and_keep_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_dir = root / "config_backups"
            original = b"BOT_TOKEN=not-public\n"
            saved = save_config_backup(root / ".env", original, backup_dir)
            self.assertEqual(saved.parent, backup_dir)
            self.assertTrue(saved.name.startswith(".env.bak-"))
            self.assertEqual(saved.read_bytes(), original)
            self.assertEqual(backup_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_multiple_servers_have_distinct_properties_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshots = root / "config_backups"
            first = save_config_backup(root / "server.properties", b"first", snapshots, server_id="storm-survival")
            second = save_config_backup(root / "server.properties", b"second", snapshots, server_id="test-server")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")
            self.assertTrue(first.name.startswith("storm-survival-server.properties.bak-"))
            self.assertTrue(second.name.startswith("test-server-server.properties.bak-"))

    def test_symlink_backup_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            linked = root / "config_backups"
            linked.symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(OSError):
                save_config_backup(root / "users.json", b"{}", linked)
            self.assertEqual(list(elsewhere.iterdir()), [])

    def test_unexpected_sources_and_unsafe_server_id_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backups = root / "config_backups"
            with self.assertRaises(ValueError):
                save_config_backup(root / "outside.txt", b"x", backups)
            with self.assertRaises(ValueError):
                save_config_backup(root / "server.properties", b"x", backups, server_id="../other")
            self.assertFalse(backups.exists())


if __name__ == "__main__":
    unittest.main()
