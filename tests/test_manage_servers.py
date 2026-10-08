import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from scripts.manage_servers import (  # noqa: E402
    ConfigurationError, apply_files, build_entry, existing_ports, load_server_config,
    parse_properties, run_wizard, suggest_port, update_env, update_properties,
)


class ManageServersTests(unittest.TestCase):
    def test_edit_properties_preserves_unrelated_values_and_removes_duplicates(self):
        before = (
            "# Minecraft properties\n"
            "max-players=20\n"
            "server-port=25565\n"
            "server-port=25564\n"
            "enable-rcon=false\n"
            "rcon.port=25575\n"
        )
        after = update_properties(before, {
            "server-port": "25566", "enable-rcon": "true",
            "rcon.port": "25576", "rcon.password": "secret",
        })
        props = parse_properties(after)
        self.assertEqual(after.count("server-port="), 1)
        self.assertEqual(props["server-port"], "25566")
        self.assertEqual(props["enable-rcon"], "true")
        self.assertEqual(props["rcon.port"], "25576")
        self.assertEqual(props["rcon.password"], "secret")
        self.assertEqual(props["max-players"], "20")
        self.assertIn("# Minecraft properties", after)

    def test_env_inserts_and_replaces_without_touching_other_keys(self):
        source = "# bot\nBOT_TOKEN=secret\n"
        result = update_env(source, "TEST_SERVER_RCON_PASSWORD", "aBc123")
        self.assertIn('TEST_SERVER_RCON_PASSWORD="aBc123"', result)
        self.assertIn("BOT_TOKEN=secret", result)
        result = update_env(result, "TEST_SERVER_RCON_PASSWORD", "second")
        self.assertEqual(result.count("TEST_SERVER_RCON_PASSWORD="), 1)
        self.assertIn('"second"', result)

    def test_invalid_servers_json_fails_without_rewriting(self):
        with self.assertRaises(ConfigurationError):
            load_server_config(b'{"servers": [ }')

    def test_existing_ports_detect_game_rcon_and_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "survival"
            folder.mkdir()
            (folder / "server.properties").write_text(
                "server-port=25565\nrcon.port=25575\n"
                "enable-query=true\nquery.port=25574\n", encoding="utf-8",
            )
            reserved = existing_ports([{
                "id": "survival", "server_dir": str(folder), "rcon_port": 25575,
            }])
            self.assertEqual(reserved, {25565, 25575, 25574})
            with patch("scripts.manage_servers.available_port", return_value=True):
                self.assertEqual(suggest_port(25565, reserved), 25566)

    def test_atomic_change_creates_backups_and_checks_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "servers.json"
            original = b'{"servers":[]}\n'
            path.write_bytes(original)
            backups = apply_files([(path, original, b'{"servers":[1]}\n', False)])
            self.assertEqual(path.read_bytes(), b'{"servers":[1]}\n')
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            with self.assertRaisesRegex(ConfigurationError, "изменился"):
                apply_files([(path, original, b"{}", False)])

    def test_wizard_adds_new_server_without_changing_existing_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bot = root / "bot"
            bot.mkdir()
            storm = root / "storm-survival"
            storm.mkdir()
            (storm / "server.properties").write_text(
                "server-port=25565\nenable-rcon=true\nrcon.port=25575\n", encoding="utf-8"
            )
            test = root / "test-server"
            test.mkdir()
            (test / "start-server.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (test / "server.properties").write_text(
                "# test world\nlevel-name=customworld\nmax-players=2\n"
                "server-port=25565\nenable-rcon=false\nrcon.port=25575\n",
                encoding="utf-8",
            )
            (bot / ".env").write_text(
                'BOT_TOKEN="do-not-touch"\nDEFAULT_SERVER_ID=storm-survival\n',
                encoding="utf-8",
            )
            servers = {"servers": [{
                "id": "storm-survival", "name": "Storm Survival",
                "server_dir": str(storm), "rcon_port": 25575,
                "backup_retention_max_count": 5,
            }]}
            (bot / "servers.json").write_text(json.dumps(servers, indent=2), encoding="utf-8")
            with (
                patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": ""}),
                patch("builtins.input", side_effect=[
                    str(test), "", "", "", "", "", "", "да",
                ]),
                patch("scripts.manage_servers.available_port", return_value=True),
                patch("scripts.manage_servers.secrets.token_hex", return_value="strong-test-password"),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertTrue(run_wizard(bot))

            data = json.loads((bot / "servers.json").read_text(encoding="utf-8"))
            self.assertEqual(data["servers"][0], servers["servers"][0])
            added = data["servers"][1]
            self.assertEqual(added["id"], "test-server")
            self.assertEqual(added["start_command"], "./start-server.sh")
            self.assertEqual(added["rcon_port"], 25576)
            self.assertEqual(added["world_dir"], str(test / "customworld"))
            self.assertEqual(added["auto_stop_seconds"], 0)
            self.assertEqual(added["backup_retention_max_count"], 0)
            props = parse_properties((test / "server.properties").read_text(encoding="utf-8"))
            self.assertEqual(props["server-port"], "25566")
            self.assertEqual(props["rcon.port"], "25576")
            self.assertEqual(props["enable-rcon"], "true")
            self.assertEqual(props["rcon.password"], "strong-test-password")
            self.assertEqual(props["max-players"], "2")
            env = (bot / ".env").read_text(encoding="utf-8")
            self.assertIn('TEST_SERVER_RCON_PASSWORD="strong-test-password"', env)
            self.assertIn('BOT_TOKEN="do-not-touch"', env)
            self.assertEqual((bot / ".env").stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(bot.glob("servers.json.bak-*"))), 1)
            self.assertEqual(len(list(bot.glob(".env.bak-*"))), 1)
            self.assertEqual(len(list(test.glob("server.properties.bak-*"))), 1)

    def test_cancel_before_save_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bot = root / "bot"
            bot.mkdir()
            server = root / "new"
            server.mkdir()
            (server / "start.sh").write_text("echo test", encoding="utf-8")
            (bot / ".env").write_text("BOT_TOKEN=unchanged\n", encoding="utf-8")
            (bot / "servers.json").write_text('{"servers":[]}\n', encoding="utf-8")
            initial = [(p, p.read_bytes()) for p in (bot / ".env", bot / "servers.json")]
            with (
                patch("builtins.input", side_effect=[
                    str(server), "", "", "", "", "", "", "нет",
                ]),
                patch("scripts.manage_servers.available_port", return_value=True),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertFalse(run_wizard(bot))
            for path, original in initial:
                self.assertEqual(path.read_bytes(), original)
            self.assertFalse((server / "server.properties").exists())

    def test_duplicate_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bot = root / "bot"
            bot.mkdir()
            server = root / "storm"
            server.mkdir()
            (bot / "servers.json").write_text(json.dumps({"servers": [{
                "id": "storm", "server_dir": str(server),
            }]}), encoding="utf-8")
            with (
                patch("builtins.input", return_value=str(server)),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                with self.assertRaisesRegex(ConfigurationError, "уже подключ"):
                    run_wizard(bot)


if __name__ == "__main__":
    unittest.main()
