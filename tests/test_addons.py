import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.addons import _toggle_sync, is_addon_name, list_addons, toggle_addon  # noqa: E402
from keyboards.inline import addon_token, mods_list_keyboard, mod_toggle_confirm_keyboard  # noqa: E402
from services.access_control import access_control  # noqa: E402


class AddonFileTests(unittest.TestCase):
    def test_safe_roundtrip_keeps_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "sodium.jar"
            file.write_bytes(b"important bytes")
            self.assertEqual(list_addons(folder), ["sodium.jar"])
            self.assertEqual(_toggle_sync(folder, "sodium.jar"), "sodium.jar.disabled")
            self.assertEqual(list_addons(folder), ["sodium.jar.disabled"])
            self.assertFalse(file.exists())
            self.assertEqual((Path(folder) / "sodium.jar.disabled").read_bytes(), b"important bytes")
            self.assertEqual(_toggle_sync(folder, "sodium.jar.disabled"), "sodium.jar")
            self.assertEqual(file.read_bytes(), b"important bytes")
            self.assertFalse((Path(folder) / "sodium.jar.disabled").exists())

    def test_collision_preserves_both_originals(self):
        with tempfile.TemporaryDirectory() as folder:
            active = Path(folder) / "test.jar"
            disabled = Path(folder) / "test.jar.disabled"
            active.write_bytes(b"active")
            disabled.write_bytes(b"disabled")
            with self.assertRaises(FileExistsError):
                _toggle_sync(folder, "test.jar")
            with self.assertRaises(FileExistsError):
                _toggle_sync(folder, "test.jar.disabled")
            self.assertEqual(active.read_bytes(), b"active")
            self.assertEqual(disabled.read_bytes(), b"disabled")

    def test_traversal_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder).parent / "outside-addon-for-test.jar"
            # Keep the external file inside another TemporaryDirectory to avoid
            # altering any real user's files.
            with tempfile.TemporaryDirectory() as outside_dir:
                outside = Path(outside_dir) / "outside.jar"
                outside.write_bytes(b"outside")
                (Path(folder) / "link.jar").symlink_to(outside)
                self.assertEqual(list_addons(folder), [])
                with self.assertRaises(ValueError):
                    _toggle_sync(folder, "link.jar")
                for invalid in ("../outside.jar", "/tmp/file.jar", "file.jar/evil", "file.zip", "file.jar.disabled.disabled"):
                    self.assertFalse(is_addon_name(invalid))
                    with self.assertRaises(ValueError):
                        _toggle_sync(folder, invalid)
                self.assertEqual(outside.read_bytes(), b"outside")

    def test_keyboard_toggle_rejects_stale_file_id(self):
        files = ["alpha.jar", "beta.jar.disabled"]
        with patch.object(access_control, "can_server", side_effect=lambda *args: args[-1] == "mods.toggle"):
            keyboard = mods_list_keyboard(files, 123, "storm")
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertIn(f"mt:storm:0:{addon_token('alpha.jar')}", callbacks)
        self.assertIn(f"mt:storm:1:{addon_token('beta.jar.disabled')}", callbacks)
        confirmation = mod_toggle_confirm_keyboard("storm", 1, "beta.jar.disabled")
        self.assertTrue(confirmation.inline_keyboard[0][0].text.startswith("✅ Да, включить"))
        self.assertIn("mt_ok:storm:1:", confirmation.inline_keyboard[0][0].callback_data)


class AddonManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_lock_blocks_running_local_server(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = "example.jar"
            original = Path(folder) / filename
            original.write_bytes(b"data")
            manager = SimpleNamespace()
            manager.run_if_stopped = AsyncMock(return_value=(False, None))
            server = SimpleNamespace(manager=manager, ssh_remote=None, mods_dir=folder)
            ran, result = await toggle_addon(server, filename)
            self.assertFalse(ran)
            self.assertIsNone(result)
            self.assertEqual(original.read_bytes(), b"data")
            manager.run_if_stopped.assert_awaited_once()

    async def test_local_and_ssh_use_correct_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "example.jar").write_bytes(b"content")

            async def perform(action):
                return True, await action()

            local_manager = SimpleNamespace(run_if_stopped=AsyncMock(side_effect=perform))
            local = SimpleNamespace(manager=local_manager, ssh_remote=None, mods_dir=folder)
            self.assertEqual(await toggle_addon(local, "example.jar"), (True, "example.jar.disabled"))

            remote = SimpleNamespace(request=AsyncMock(return_value="plugin.jar.disabled"))
            ssh_manager = SimpleNamespace(run_if_stopped=AsyncMock(side_effect=perform))
            ssh_server = SimpleNamespace(manager=ssh_manager, ssh_remote=remote, mods_dir="/remote/plugins")
            self.assertEqual(await toggle_addon(ssh_server, "plugin.jar"), (True, "plugin.jar.disabled"))
            remote.request.assert_awaited_once_with("toggle_mod", filename="plugin.jar", timeout=40)


if __name__ == "__main__":
    unittest.main()
