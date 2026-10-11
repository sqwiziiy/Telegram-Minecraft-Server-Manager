"""Tests for local/SSH archive catalog, safety, and backup Telegram menus."""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.backup_catalog import backup_info, list_backups  # noqa: E402
from services import remote_agent  # noqa: E402
from handlers import system  # noqa: E402
from keyboards.inline import (  # noqa: E402
    BACKUP_PAGE_SIZE, addon_token, backup_info_keyboard, backup_list_keyboard,
    backup_menu_keyboard,
)
from services.access_control import access_control  # noqa: E402


def make_zip(path: Path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("world/level.dat", b"level data")
        archive.writestr("world/data/file.txt", b"hello")


class CatalogTests(unittest.TestCase):
    def test_filters_names_sorts_types_and_avoids_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manual = root / "world_backup_20261011_220000_000001.zip"
            auto = root / "world_auto_backup_20261012_010000_000001.zip"
            protected = root / "world_pre_restore_20261012_020000_000001.zip"
            for file in (manual, auto, protected):
                make_zip(file)
            (root / "evil.zip").write_bytes(b"ignored")
            (root / "world_backup_20261012_030000_000001.zip").symlink_to(manual)
            entries = list_backups(tmp)
            self.assertEqual([e["name"] for e in entries], [protected.name, auto.name, manual.name, "evil.zip"])
            self.assertEqual([e["type"] for e in entries], [
                "Защитный", "Автоматический", "Ручной", "Другой ZIP (требует проверки)"
            ])
            self.assertEqual([e["known_format"] for e in entries], [True, True, True, False])
            self.assertEqual(entries[0]["created"], "12.10.2026 02:00:00")
            self.assertEqual(list_backups(str(root / "missing")), [])

    def test_thirteen_legacy_zip_archives_are_counted_without_changing_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(6):
                make_zip(root / f"world_backup_20261011_120000_{i:06d}.zip")
            for i in range(6):
                make_zip(root / f"world_backup_2025-09-{i + 1:02d}.zip")
            make_zip(root / "minecraft-world-old-copy.zip")
            (root / ".world_backup_20251011_120000.zip.partial").write_bytes(b"partial")
            (root / "notes.txt").write_text("not a backup", encoding="utf-8")

            entries = list_backups(tmp)
            self.assertEqual(len(entries), 13)
            self.assertEqual(sum(item["known_format"] for item in entries), 6)
            self.assertTrue(any(item["type"] == "Ручной (старый формат)" for item in entries))
            self.assertTrue(any(item["type"] == "Другой ZIP (требует проверки)" for item in entries))
            self.assertEqual(backup_info(tmp, "minecraft-world-old-copy.zip")["file_count"], 2)

            config = {"backup_dir": tmp}
            remote_entries = remote_agent.dispatch({"action": "list_backups", "config": config})
            self.assertEqual(len(remote_entries), 13)
            self.assertEqual({entry["name"] for entry in entries},
                             {entry["name"] for entry in remote_entries})
            self.assertEqual(remote_agent.dispatch({
                "action": "backup_info", "config": config,
                "filename": "minecraft-world-old-copy.zip"
            })["file_count"], 2)

    def test_inspect_read_only_rejects_unsafe_and_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "world_backup_20261012_012000_123456.zip"
            make_zip(valid)
            data = backup_info(tmp, valid.name)
            self.assertTrue(data["valid"])
            self.assertEqual(data["file_count"], 2)
            self.assertEqual(data["preview"][0], "world/level.dat")
            self.assertFalse((root / "world").exists(), "Inspect must not extract the archive")
            with self.assertRaises(ValueError):
                backup_info(tmp, "../outside.zip")
            broken = root / "world_backup_20261012_013000_123456.zip"
            broken.write_bytes(b"not a zip")
            self.assertFalse(backup_info(tmp, broken.name)["valid"])
            fake = root / "world_backup_20261012_014000_123456.zip"
            fake.symlink_to(valid)
            with self.assertRaises(FileNotFoundError):
                backup_info(tmp, fake.name)

    def test_remote_catalog_uses_same_schema_and_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "world_auto_backup_20261012_033300_000001.zip"
            make_zip(archive)
            cfg = {"backup_dir": tmp}
            entries = remote_agent.dispatch({"action": "list_backups", "config": cfg})
            self.assertEqual(entries[0]["name"], archive.name)
            info = remote_agent.dispatch({"action": "backup_info", "config": cfg, "filename": archive.name})
            self.assertEqual(info["file_count"], 2)
            with self.assertRaises(ValueError):
                remote_agent.dispatch({"action": "backup_info", "config": cfg, "filename": "../secret"})


class CatalogUiTests(unittest.TestCase):
    def test_menu_permission_separation(self):
        with patch.object(access_control, "can_server", side_effect=lambda uid, sid, p: p == "backup.view"):
            k = backup_menu_keyboard("storm", 101)
        actions = [b.callback_data for row in k.inline_keyboard for b in row]
        self.assertIn("bk_list:storm:0", actions)
        self.assertNotIn("bk_create:storm", actions)
        with patch.object(access_control, "can_server", side_effect=lambda uid, sid, p: p == "backup.create"):
            k = backup_menu_keyboard("storm", 101)
        actions = [b.callback_data for row in k.inline_keyboard for b in row]
        self.assertNotIn("bk_list:storm:0", actions)
        self.assertIn("bk_create:storm", actions)

    def test_list_page_buttons_are_bounded_and_not_paths(self):
        sample = [{"name": f"world_backup_20261012_000000_{i:06d}.zip",
                   "created": "12.10.2026 00:00:00", "size_mb": 5.0, "type": "Ручной"}
                  for i in range(2)]
        k = backup_list_keyboard("storm", 0, sample, 10)
        labels = [b.text for row in k.inline_keyboard for b in row]
        callbacks = [b.callback_data for row in k.inline_keyboard for b in row]
        self.assertIn("Старее ▶️", labels)
        self.assertIn("bk_list:storm:1", callbacks)
        self.assertIn("bk_info:storm:0:0:" + addon_token(sample[0]["name"]), callbacks)
        self.assertLessEqual(max(map(len, callbacks)), 64)
        self.assertEqual(BACKUP_PAGE_SIZE, 6)
        self.assertEqual(backup_info_keyboard("storm", 0).inline_keyboard[0][0].callback_data, "bk_list:storm:0")


class CatalogHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_archive_selection_is_rejected(self):
        server = SimpleNamespace(server_id="storm", server_name="Storm", ssh_remote=None)
        callback = SimpleNamespace(
            data="bk_info:storm:0:0:" + addon_token("old.zip"),
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        current = [{"name": "world_backup_20261012_120000_000001.zip"}]
        with (
            patch.object(system, "resolve_server", return_value=server),
            patch.object(access_control, "can_server", return_value=True),
            patch.object(system, "_server_backups", new_callable=AsyncMock, return_value=current),
            patch.object(system, "_server_backup_details", new_callable=AsyncMock) as details,
        ):
            await system.show_backup_info(callback)
        details.assert_not_awaited()
        callback.answer.assert_awaited_once()
        self.assertIn("изменился", callback.answer.await_args.args[0])
        callback.message.edit_text.assert_not_awaited()

    async def test_view_only_cannot_trigger_creation(self):
        server = SimpleNamespace(server_id="storm", server_name="Storm")
        callback = SimpleNamespace(
            data="bk_create:storm",
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        with (
            patch.object(system, "resolve_server", return_value=server),
            patch.object(access_control, "can_server", return_value=False),
            patch.object(system, "deny_access", new_callable=AsyncMock) as denied,
            patch.object(system, "create_backup", new_callable=AsyncMock) as create,
        ):
            await system.create_manual_backup(callback)
        denied.assert_awaited_once_with(callback)
        create.assert_not_awaited()

    async def test_local_and_ssh_use_separate_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = "world_backup_20261012_120000_000001.zip"
            make_zip(Path(tmp) / filename)
            local = SimpleNamespace(ssh_remote=None, backup_dir=tmp)
            entries = await system._server_backups(local)
            self.assertEqual(len(entries), 1)
            info = await system._server_backup_details(local, filename)
            self.assertEqual(info["file_count"], 2)
            remote = SimpleNamespace(ssh_remote=SimpleNamespace(request=AsyncMock(side_effect=[entries, info])))
            self.assertEqual(await system._server_backups(remote), entries)
            self.assertEqual(await system._server_backup_details(remote, filename), info)
            self.assertEqual(remote.ssh_remote.request.await_count, 2)


if __name__ == "__main__":
    unittest.main()
