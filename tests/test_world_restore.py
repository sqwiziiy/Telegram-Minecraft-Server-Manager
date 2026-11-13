"""World restoration tests use only temporary Minecraft worlds and ZIPs."""
import asyncio
import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services import remote_agent  # noqa: E402
from services.restore import restore_world  # noqa: E402
from services.access_control import access_control  # noqa: E402
from keyboards.inline import backup_info_keyboard, backup_restore_confirm_keyboard  # noqa: E402
from handlers import system  # noqa: E402


class RestoreFilesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.world = self.root / "world"
        self.world.mkdir()
        (self.world / "level.dat").write_bytes(b"original-level")
        (self.world / "region").mkdir()
        (self.world / "region" / "r.0.0.mca").write_bytes(b"original-region")
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.source = self.backups / "world_backup_20261012_110000_000001.zip"
        self.config = {"world_dir": str(self.world), "backup_dir": str(self.backups)}

    def archive(self, entries=None, filename=None):
        filename = filename or self.source
        entries = entries if entries is not None else {
            "world/level.dat": b"restored-level",
            "world/region/r.0.0.mca": b"restored-region",
        }
        with zipfile.ZipFile(filename, "w", compression=zipfile.ZIP_STORED) as handle:
            for name, value in entries.items():
                handle.writestr(name, value)
        return filename

    def test_restores_and_preserves_original_and_source_zip(self):
        self.archive()
        result = remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"restored-level")
        self.assertEqual((self.world / "region" / "r.0.0.mca").read_bytes(), b"restored-region")
        self.assertEqual(result["restored"], self.source.name)
        self.assertEqual(result["warning"], "")
        self.assertTrue(self.source.is_file())
        protection = self.backups / result["protected"]
        self.assertRegex(protection.name, r"^world_pre_restore_\d{8}_\d{6}_\d{6}\.zip$")
        with zipfile.ZipFile(protection) as before:
            self.assertIsNone(before.testzip())
            self.assertEqual(before.read("world/level.dat"), b"original-level")
            self.assertEqual(before.read("world/region/r.0.0.mca"), b"original-region")
        self.assertFalse(list(self.root.glob(".world.restore-*")))

    def test_restore_legacy_zip_name_if_content_is_safe(self):
        self.archive(filename=self.backups / "old-world.zip")
        result = remote_agent.restore_world_files(self.config, "old-world.zip")
        self.assertEqual(result["restored"], "old-world.zip")
        self.assertEqual((self.world / "level.dat").read_bytes(), b"restored-level")

    def test_rejects_traversal_and_absolute_paths_without_touching_world(self):
        for unsafe in ("../escape", "/tmp/escape", "world/../escape", "world//nested"):
            with self.subTest(path=unsafe):
                self.archive({"world/level.dat": b"okay", unsafe: b"evil"})
                with self.assertRaises(ValueError):
                    remote_agent.restore_world_files(self.config, self.source.name)
                self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")
        self.assertEqual(list(self.backups.glob("world_pre_restore_*.zip")), [])

    def test_rejects_symlink_in_zip(self):
        symlink = zipfile.ZipInfo("world/region/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.source, "w") as handle:
            handle.writestr("world/level.dat", b"restored-level")
            handle.writestr(symlink, b"/etc/passwd")
        with self.assertRaises(ValueError):
            remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")

    def test_rejects_corrupt_crc_without_modifying_world(self):
        self.archive()
        contents = self.source.read_bytes().replace(b"restored-level", b"CORRUPTED-leve")
        self.source.write_bytes(contents)
        with self.assertRaises((zipfile.BadZipFile, OSError, ValueError)):
            remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")
        self.assertFalse(list(self.backups.glob("world_pre_restore_*.zip")))

    def test_rejects_wrong_world_directory_and_missing_level_dat(self):
        self.archive({"other/level.dat": b"stranger"})
        with self.assertRaises(ValueError):
            remote_agent.restore_world_files(self.config, self.source.name)
        self.archive({"world/minecraft.txt": b"stranger"})
        with self.assertRaises(ValueError):
            remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")

    def test_failed_atomic_swap_leaves_old_world_and_protection(self):
        self.archive()
        with patch.object(remote_agent, "_exchange_directories", side_effect=OSError("cannot swap")):
            with self.assertRaisesRegex(OSError, "cannot swap"):
                remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")
        self.assertEqual(len(list(self.backups.glob("world_pre_restore_*.zip"))), 1)
        self.assertFalse(list(self.root.glob(".world.restore-*")))

    def test_failed_protective_archive_never_changes_world(self):
        self.archive()
        with patch.object(remote_agent, "_protect_old_world", side_effect=OSError("disk stopped")):
            with self.assertRaisesRegex(OSError, "disk stopped"):
                remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")
        self.assertFalse(list(self.root.glob(".world.restore-*")))

    def test_insufficient_space_does_not_touch_world(self):
        self.archive()
        fake_usage = SimpleNamespace(free=1)
        with patch.object(remote_agent.shutil, "disk_usage", return_value=fake_usage):
            with self.assertRaises(OSError):
                remote_agent.restore_world_files(self.config, self.source.name)
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")
        self.assertFalse(list(self.backups.glob("world_pre_restore_*.zip")))

    def test_rejects_symlinked_world_and_source(self):
        self.archive()
        fake_archive = self.backups / "linked.zip"
        fake_archive.symlink_to(self.source)
        with self.assertRaises(FileNotFoundError):
            remote_agent.restore_world_files(self.config, fake_archive.name)
        with tempfile.TemporaryDirectory() as another:
            config = {"world_dir": str(Path(another) / "world"), "backup_dir": str(self.backups)}
            Path(config["world_dir"]).symlink_to(self.world, target_is_directory=True)
            with self.assertRaises(ValueError):
                remote_agent.restore_world_files(config, self.source.name)

    def test_remote_dispatch_refuses_online_server(self):
        self.archive()
        config = {
            **self.config, "server_dir": str(self.root),
            "pid_file": str(self.root / "managed.pid"),
            "rcon_port": 60001, "minecraft_port": 60002,
        }
        with patch.object(remote_agent, "get_status", return_value={"running": True}):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                remote_agent.dispatch({"action": "restore_world", "config": config,
                                       "filename": self.source.name})
        self.assertEqual((self.world / "level.dat").read_bytes(), b"original-level")


class RestoreUiTests(unittest.TestCase):
    def test_restore_permission_controls_archive_button(self):
        item = backup_info_keyboard("storm", 0, selected_index=1,
                                    token="0123456789", can_restore=True)
        callbacks = [x.callback_data for row in item.inline_keyboard for x in row]
        self.assertIn("br:storm:0:1:0123456789", callbacks)
        limited = backup_info_keyboard("storm", 0, selected_index=1,
                                       token="0123456789", can_restore=False)
        self.assertNotIn("br:storm:0:1:0123456789",
                         [x.callback_data for row in limited.inline_keyboard for x in row])
        button = backup_restore_confirm_keyboard("storm", 0, 1, "0123456789")
        self.assertEqual(button.inline_keyboard[0][0].callback_data,
                         "br_ok:storm:0:1:0123456789")

    def test_callback_data_stays_under_telegram_limit_for_long_server_id(self):
        sid = "s" * 32
        info = backup_info_keyboard(sid, 1234, selected_index=9999999,
                                    token="a" * 10, can_restore=True)
        confirm = backup_restore_confirm_keyboard(sid, 1234, 9999999, "a" * 10)
        for keyboard in (info, confirm):
            for row in keyboard.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64)

    def test_invalid_callback_fails_closed(self):
        for payload in ("br:storm:0:6:0123456789", "br:storm:x:0:0123456789",
                        "br:storm:0:1:../etc", "br:storm:0:1:bad"):
            self.assertIsNone(system._parse_restore_callback(payload, "br:"))


class RestoreCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_server_cannot_call_restore(self):
        manager = SimpleNamespace(run_if_stopped=AsyncMock(return_value=(False, None)))
        server = SimpleNamespace(server_id="test", manager=manager, ssh_remote=None)
        ran, data = await restore_world(server, "good.zip")
        self.assertFalse(ran)
        self.assertIsNone(data)

    async def test_local_offline_restore_calls_filesystem_core(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            world = root / "world"
            world.mkdir()
            (world / "level.dat").write_bytes(b"old")
            backups = root / "backups"
            backups.mkdir()
            archive_name = "world_backup_20261012_120000_000001.zip"
            with zipfile.ZipFile(backups / archive_name, "w") as z:
                z.writestr("world/level.dat", b"new")

            async def run_when_stopped(action):
                return True, await action()

            server = SimpleNamespace(
                server_id="local-test", ssh_remote=None,
                manager=SimpleNamespace(
                    server_dir=root,
                    run_if_stopped=AsyncMock(side_effect=run_when_stopped),
                ),
                world_dir=str(world), backup_dir=str(backups),
            )
            with patch("services.restore._assert_local_offline"):
                ran, info = await restore_world(server, archive_name)
            self.assertTrue(ran)
            self.assertEqual((world / "level.dat").read_bytes(), b"new")
            self.assertTrue((backups / info["protected"]).exists())

    async def test_cancellation_does_not_release_process_lock_during_restore(self):
        process_lock = asyncio.Lock()
        started = asyncio.Event()
        release = asyncio.Event()

        async def run_with_lock(action):
            async with process_lock:
                return True, await action()

        async def delayed_request(*args, **kwargs):
            started.set()
            await release.wait()
            return {"restored": "good.zip", "protected": "safety.zip", "warning": ""}

        remote = SimpleNamespace(request=AsyncMock(side_effect=delayed_request))
        manager = SimpleNamespace(run_if_stopped=AsyncMock(side_effect=run_with_lock))
        server = SimpleNamespace(server_id="test", manager=manager, ssh_remote=remote)
        task = asyncio.create_task(restore_world(server, "good.zip"))
        try:
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertTrue(process_lock.locked())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(process_lock.locked())

    async def test_ssh_restore_uses_remote_agent(self):
        async def perform(action):
            return True, await action()
        remote = SimpleNamespace(request=AsyncMock(return_value={
            "restored": "good.zip", "protected": "world_pre_restore_20261012_010000_000001.zip",
            "warning": "",
        }))
        manager = SimpleNamespace(run_if_stopped=AsyncMock(side_effect=perform))
        server = SimpleNamespace(server_id="test", manager=manager, ssh_remote=remote)
        ran, outcome = await restore_world(server, "good.zip")
        self.assertTrue(ran)
        self.assertEqual(outcome["restored"], "good.zip")
        remote.request.assert_awaited_once_with("restore_world", filename="good.zip", timeout=4 * 3600)


if __name__ == "__main__":
    unittest.main()
