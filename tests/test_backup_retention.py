import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.backup import create_backup  # noqa: E402
from services.backup_retention import prune_auto_backups  # noqa: E402


def make_archive(directory: Path, date: str, size: int) -> Path:
    path = directory / f"world_auto_backup_{date}.zip"
    path.write_bytes(b"x" * size)
    return path


class BackupRetentionTests(unittest.TestCase):
    def test_disabled_limits_never_delete_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = make_archive(root, "20260101_100000_000000", 40)
            latest = make_archive(root, "20260102_100000_000000", 60)
            result = prune_auto_backups(root, latest)
            self.assertEqual(result.deleted_count, 0)
            self.assertTrue(old.exists())
            self.assertTrue(latest.exists())

    def test_count_prunes_oldest_and_ignores_manual_and_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = make_archive(root, "20260101_100000_000000", 40)
            second = make_archive(root, "20260102_100000_000000", 60)
            latest = make_archive(root, "20260103_100000_000000", 80)
            manual = root / "world_backup_20251225_110000_000000.zip"
            manual.write_bytes(b"manual")
            other = root / "world_auto_backup_UNRECOGNIZED.zip"
            other.write_bytes(b"other")
            result = prune_auto_backups(root, latest, max_count=2)
            self.assertEqual(result.deleted_count, 1)
            self.assertEqual(result.freed_bytes, 40)
            self.assertTrue(result.within_limits)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(latest.exists())
            self.assertTrue(manual.exists())
            self.assertTrue(other.exists())

    def test_size_limit_deletes_oldest_until_under_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = make_archive(root, "20260101_100000_000000", 700)
            second = make_archive(root, "20260102_100000_000000", 700)
            latest = make_archive(root, "20260103_100000_000000", 700)
            result = prune_auto_backups(root, latest, max_gb=1500 / 1024**3)
            self.assertEqual(result.deleted_count, 1)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(latest.exists())
            self.assertTrue(result.within_limits)

    def test_both_limits_must_be_met(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = make_archive(root, "20260101_100000_000000", 100)
            second = make_archive(root, "20260102_100000_000000", 100)
            latest = make_archive(root, "20260103_100000_000000", 100)
            result = prune_auto_backups(root, latest, max_count=2, max_gb=150 / 1024**3)
            self.assertEqual(result.deleted_count, 2)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue(latest.exists())

    def test_fresh_backup_is_never_deleted_if_too_large(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = make_archive(root, "20260101_100000_000000", 100)
            latest = make_archive(root, "20260102_100000_000000", 500)
            result = prune_auto_backups(root, latest, max_gb=200 / 1024**3)
            self.assertEqual(result.deleted_count, 1)
            self.assertFalse(old.exists())
            self.assertTrue(latest.exists())
            self.assertFalse(result.within_limits)

    def test_symlinks_and_unrelated_archives_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside.zip"
            outside.write_bytes(b"keep")
            link = root / "world_auto_backup_20260101_100000_000000.zip"
            link.symlink_to(outside)
            latest = make_archive(root, "20260103_100000_000000", 20)
            result = prune_auto_backups(root, latest, max_count=1)
            self.assertEqual(result.deleted_count, 0)
            self.assertTrue(link.is_symlink())
            self.assertEqual(outside.read_bytes(), b"keep")

    def test_missing_fresh_archive_aborts_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = make_archive(root, "20260101_100000_000000", 20)
            with self.assertRaises(RuntimeError):
                prune_auto_backups(root, root / "missing.zip", max_count=1)
            self.assertTrue(old.exists())


class BackupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_automatic_archives_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            world = root / "world"
            world.mkdir()
            (world / "level.dat").write_bytes(b"test world data")
            backups = root / "backups"
            backups.mkdir()
            old = make_archive(backups, "20260101_100000_000000", 300)
            manual = backups / "world_backup_20260101_100000_000000.zip"
            manual.write_bytes(b"manual backup")
            server = SimpleNamespace(
                server_id="storm",
                world_dir=str(world),
                backup_dir=str(backups),
                backup_retention_max_count=1,
                backup_retention_max_gb=0.0,
            )
            with patch("services.backup._prepare_live_backup", new_callable=AsyncMock, return_value=(False, None)):
                result = await create_backup(server, automatic=True)
            self.assertTrue(result.startswith("✅"), result)
            self.assertFalse(old.exists())
            self.assertTrue(manual.exists())
            auto_files = list(backups.glob("world_auto_backup_*.zip"))
            self.assertEqual(len(auto_files), 1)
            self.assertGreater(auto_files[0].stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
