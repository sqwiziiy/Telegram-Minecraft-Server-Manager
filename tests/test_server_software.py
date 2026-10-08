"""Minecraft gameplay software types must not interfere with SSH/local transport."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.server_software import choose_software, suggest_software
from keyboards.inline import server_home_keyboard
from services.server_registry import ServerRegistry


class SoftwareDetectionTests(unittest.TestCase):
    def test_suggestions_from_folders(self):
        self.assertEqual(suggest_software(True, False)[0], "mods")
        self.assertEqual(suggest_software(False, True)[0], "plugins")
        self.assertEqual(suggest_software(False, False)[0], "vanilla")
        self.assertIsNone(suggest_software(True, True)[0])

    def test_suggested_folder_and_manual_override(self):
        with (
            patch("builtins.input", return_value=""),
            patch("builtins.print"),
        ):
            self.assertEqual(choose_software(True, False), "mods")
        with (
            patch("builtins.input", side_effect=["x", "3"]),
            patch("builtins.print"),
        ):
            self.assertEqual(choose_software(True, False), "plugins")

    def test_old_servers_keep_mods_menu_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            directory = base / "server"
            directory.mkdir()
            config = base / "servers.json"
            config.write_text(json.dumps({"servers": [{
                "id": "old", "name": "Old Fabric", "server_dir": str(directory),
                "rcon_port": 25575,
            }]}), encoding="utf-8")
            with (
                patch("services.server_registry.MINECRAFT_SERVERS_FILE", str(config)),
                patch("services.server_registry.DEFAULT_SERVER_ID", "old"),
            ):
                server = ServerRegistry().get("old")
            self.assertEqual(server.server_software, "mods")
            self.assertEqual(server.mods_dir, str(directory / "mods"))

    def test_plugin_server_routes_addons_to_plugins_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            directory = base / "paper"
            directory.mkdir()
            config = base / "servers.json"
            config.write_text(json.dumps({"servers": [{
                "id": "paper", "name": "Paper", "server_dir": str(directory),
                "server_software": "plugins", "rcon_port": 25575,
            }]}), encoding="utf-8")
            with (
                patch("services.server_registry.MINECRAFT_SERVERS_FILE", str(config)),
                patch("services.server_registry.DEFAULT_SERVER_ID", "paper"),
            ):
                server = ServerRegistry().get("paper")
            self.assertEqual(server.server_software, "plugins")
            self.assertEqual(server.mods_dir, str(directory / "plugins"))

    def test_remote_paper_uses_plugins_directory_via_ssh(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            key_file = base / "ssh_key"
            known_file = base / "known_hosts"
            key_file.write_text("test key", encoding="utf-8")
            known_file.write_text("host key", encoding="utf-8")
            remote_root = "/home/minecraft/paper-server"
            config = base / "servers.json"
            config.write_text(json.dumps({"servers": [{
                "id": "paper-ssh", "name": "Paper SSH",
                "type": "ssh", "server_dir": remote_root,
                "server_software": "plugins",
                "ssh": {"host": "192.0.2.5", "user": "minecraft", "port": 22,
                        "key_file": str(key_file), "known_hosts": str(known_file)},
                "rcon_port": 25575
            }]}), encoding="utf-8")
            with (
                patch("services.server_registry.MINECRAFT_SERVERS_FILE", str(config)),
                patch("services.server_registry.DEFAULT_SERVER_ID", "paper-ssh"),
            ):
                server = ServerRegistry().get("paper-ssh")
            self.assertEqual(server.server_software, "plugins")
            self.assertEqual(server.mods_dir, remote_root + "/plugins")
            self.assertIsNotNone(server.ssh_remote)
            self.assertEqual(server.ssh_remote.config["mods_dir"], remote_root + "/plugins")

    def test_unknown_software_fails_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "servers.json"
            config.write_text(json.dumps({"servers": [{
                "id": "oops", "name": "Bad", "server_dir": str(base),
                "server_software": "something-else"
            }]}), encoding="utf-8")
            with (
                patch("services.server_registry.MINECRAFT_SERVERS_FILE", str(config)),
                patch("services.server_registry.DEFAULT_SERVER_ID", "oops"),
            ):
                with self.assertRaisesRegex(RuntimeError, "server_software"):
                    ServerRegistry()

    def test_telegram_menu_follows_software_type(self):
        with patch("keyboards.inline.access_control.can_server", return_value=True):
            results = {}
            for kind in ("vanilla", "mods", "plugins"):
                server = SimpleNamespace(server_id="test", server_software=kind)
                ui = server_home_keyboard(server, 123)
                results[kind] = [btn.text for row in ui.inline_keyboard for btn in row]
            self.assertNotIn("🧩 Моды", results["vanilla"])
            self.assertNotIn("🔌 Плагины", results["vanilla"])
            self.assertIn("🧩 Моды", results["mods"])
            self.assertIn("🔌 Плагины", results["plugins"])


class VanillaAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_mods_callback_cannot_open_vanilla_addons(self):
        from handlers.mods import list_mods

        server = SimpleNamespace(server_id="vanilla", server_software="vanilla")
        callback = SimpleNamespace(
            data="mods:vanilla", answer=AsyncMock(), from_user=SimpleNamespace(id=42)
        )
        state = AsyncMock()
        with patch("handlers.mods.resolve_server", return_value=server):
            await list_mods(callback, state)
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.call_args.kwargs["show_alert"])
        state.clear.assert_not_awaited()

    async def test_stale_upload_state_cannot_upload_to_vanilla(self):
        from handlers.mods import upload_mod

        server = SimpleNamespace(server_id="vanilla", server_software="vanilla")
        state = AsyncMock()
        state.get_data.return_value = {"server_id": "vanilla"}
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
        )
        with patch("handlers.mods.resolve_server", return_value=server):
            await upload_mod(message, state)
        message.answer.assert_awaited_once()
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
