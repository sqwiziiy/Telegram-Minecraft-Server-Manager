"""Safe server-unregistration wizard regression tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")

from scripts.manage_servers import run_wizard
from scripts.manage_users import ConfigurationError


class RemoveServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "bot"
        self.project.mkdir()
        self.minecraft = Path(self.tmp.name) / "survival"
        self.minecraft.mkdir()
        (self.minecraft / "world").mkdir()
        (self.minecraft / "world" / "level.dat").write_bytes(b"DO NOT DELETE")
        (self.minecraft / "mods").mkdir()
        (self.minecraft / "mods" / "example.jar").write_bytes(b"JAR")
        (self.minecraft / "backups").mkdir()
        (self.minecraft / "backups" / "world.zip").write_bytes(b"ZIP")
        self.remote = {
            "id": "remote", "name": "Remote Test", "type": "ssh",
            "server_dir": "/home/mc/test", "rcon_port": 25575,
            "rcon_password_env": "REMOTE_RCON_PASSWORD",
            "ssh": {"host": "example.local", "user": "mc"},
        }
        self.local = {
            "id": "survival", "name": "Survival",
            "server_dir": str(self.minecraft),
            "rcon_password_env": "SURVIVAL_RCON_PASSWORD",
        }
        self.saved = {"servers": [self.local, self.remote], "note": "preserve me"}
        (self.project / "servers.json").write_text(
            json.dumps(self.saved), encoding="utf-8"
        )
        (self.project / ".env").write_text(
            'BOT_TOKEN="keep-secret"\nDEFAULT_SERVER_ID="survival"\n'
            'SURVIVAL_RCON_PASSWORD="local"\n'
            'REMOTE_RCON_PASSWORD="remote"\n'
            'EXTRA_KEY="untouched"\n', encoding="utf-8",
        )
        (self.project / "users.json").write_text(
            json.dumps({"users": {
                "123": {"name": "Friend", "servers": {
                    "survival": {"role": "operator"},
                    "remote": {"role": "viewer"},
                }},
                "456": {"name": "Only Remote", "servers": {
                    "remote": {"role": "operator"},
                }},
            }}), encoding="utf-8",
        )
        (self.project / "auto_stop_state.json").write_text(
            json.dumps({"servers": {"survival": 30, "remote": 120}, "version": 1}),
            encoding="utf-8",
        )
        (self.project / "auto_backup_state.json").write_text(
            json.dumps({"servers": {
                "remote": {"interval_seconds": 86400, "next_due_at": 2000000000.0},
                "survival": {"interval_seconds": 43200, "next_due_at": 2000000000.0},
            }}), encoding="utf-8",
        )

    def invoke(self, inputs):
        with (
            patch("builtins.input", side_effect=inputs),
            patch("builtins.print"),
            patch.dict(os.environ, {
                "MINECRAFT_SERVERS_FILE": "",
                "ACCESS_USERS_FILE": "",
                "AUTO_STOP_STATE_FILE": "",
                "AUTO_BACKUP_STATE_FILE": "",
                "DEFAULT_SERVER_ID": "",
            }),
        ):
            return run_wizard(self.project)

    def json_file(self, name):
        return json.loads((self.project / name).read_text(encoding="utf-8"))

    def backup_names(self):
        folder = self.project / "config_backups"
        return sorted(x.name.split(".bak-")[0] for x in folder.iterdir()) if folder.exists() else []

    def assert_files_untouched(self):
        self.assertEqual((self.minecraft / "world" / "level.dat").read_bytes(), b"DO NOT DELETE")
        self.assertEqual((self.minecraft / "mods" / "example.jar").read_bytes(), b"JAR")
        self.assertEqual((self.minecraft / "backups" / "world.zip").read_bytes(), b"ZIP")

    def test_remove_remote_without_ssh_or_minecraft_deletion(self):
        with patch("services.remote_ssh.SSHRemote.request") as ssh:
            self.assertTrue(self.invoke(["4", "2", "remote"]))
        ssh.assert_not_called()
        config = self.json_file("servers.json")
        self.assertEqual(config["servers"], [self.local])
        self.assertEqual(config["note"], "preserve me")
        text = (self.project / ".env").read_text()
        self.assertNotIn("REMOTE_RCON_PASSWORD=", text)
        self.assertIn('SURVIVAL_RCON_PASSWORD="local"', text)
        self.assertIn('DEFAULT_SERVER_ID="survival"', text)
        self.assertIn('EXTRA_KEY="untouched"', text)
        users = self.json_file("users.json")["users"]
        self.assertEqual(users["123"]["servers"], {"survival": {"role": "operator"}})
        self.assertEqual(users["456"]["servers"], {})
        self.assertEqual(self.json_file("auto_stop_state.json"),
                         {"servers": {"survival": 30}, "version": 1})
        self.assertEqual(set(self.json_file("auto_backup_state.json")["servers"]), {"survival"})
        self.assertEqual(self.backup_names(), [
            ".env", "auto_backup_state.json", "auto_stop_state.json", "servers.json", "users.json"
        ])
        for name in ("servers.json", "users.json", ".env", "auto_stop_state.json", "auto_backup_state.json"):
            matches = list((self.project / "config_backups").glob(name + ".bak-*"))
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].stat().st_mode & 0o777, 0o600)
        self.assert_files_untouched()

    def test_removing_default_updates_default_and_drops_legacy_roles(self):
        users = self.json_file("users.json")
        users["users"]["999"] = {
            "name": "Legacy friend", "role": "admin", "allow": ["console.use"]
        }
        (self.project / "users.json").write_text(json.dumps(users), encoding="utf-8")
        self.assertTrue(self.invoke(["4", "1", "remote", "survival"]))
        self.assertEqual(self.json_file("servers.json")["servers"], [self.remote])
        updated = (self.project / ".env").read_text()
        self.assertIn('DEFAULT_SERVER_ID="remote"', updated)
        self.assertNotIn("SURVIVAL_RCON_PASSWORD=", updated)
        migrated = self.json_file("users.json")["users"]
        self.assertEqual(migrated["999"], {"name": "Legacy friend", "servers": {}})
        self.assertEqual(migrated["123"]["servers"], {"remote": {"role": "viewer"}})
        self.assert_files_untouched()

    def test_wrong_confirmation_changes_nothing(self):
        originals = {
            p.name: p.read_bytes() for p in self.project.iterdir() if p.is_file()
        }
        self.assertFalse(self.invoke(["4", "2", "nope"]))
        for name, value in originals.items():
            self.assertEqual((self.project / name).read_bytes(), value)
        self.assertFalse((self.project / "config_backups").exists())
        self.assert_files_untouched()

    def test_cancel_selection_changes_nothing(self):
        self.assertFalse(self.invoke(["4", "0"]))
        self.assertEqual(self.json_file("servers.json"), self.saved)
        self.assertFalse((self.project / "config_backups").exists())

    def test_last_server_cannot_be_deleted(self):
        (self.project / "servers.json").write_text(
            json.dumps({"servers": [self.local]}), encoding="utf-8"
        )
        self.assertFalse(self.invoke(["4"]))
        self.assertEqual(self.json_file("servers.json")["servers"], [self.local])
        self.assertFalse((self.project / "config_backups").exists())

    def test_shared_rcon_env_variable_must_remain(self):
        servers = self.json_file("servers.json")
        servers["servers"][1]["rcon_password_env"] = "SURVIVAL_RCON_PASSWORD"
        (self.project / "servers.json").write_text(json.dumps(servers), encoding="utf-8")
        self.assertTrue(self.invoke(["4", "1", "remote", "survival"]))
        content = (self.project / ".env").read_text()
        self.assertIn('SURVIVAL_RCON_PASSWORD="local"', content)

    def test_corrupted_state_aborts_all_config_changes(self):
        original = (self.project / "servers.json").read_bytes()
        (self.project / "auto_stop_state.json").write_text("{ broken", encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            self.invoke(["4", "2"])
        self.assertEqual((self.project / "servers.json").read_bytes(), original)
        self.assertFalse((self.project / "config_backups").exists())

    def test_custom_task_state_paths_still_get_backups(self):
        env = (self.project / ".env").read_text()
        env += 'AUTO_STOP_STATE_FILE="custom-stop.json"\n'
        env += 'AUTO_BACKUP_STATE_FILE="custom-schedule.json"\n'
        (self.project / ".env").write_text(env, encoding="utf-8")
        (self.project / "auto_stop_state.json").rename(self.project / "custom-stop.json")
        (self.project / "auto_backup_state.json").rename(self.project / "custom-schedule.json")
        self.assertTrue(self.invoke(["4", "2", "remote"]))
        self.assertEqual(set(self.json_file("custom-schedule.json")["servers"]), {"survival"})
        self.assertIn("auto_stop_state.json", self.backup_names())
        self.assertIn("auto_backup_state.json", self.backup_names())

    def test_list_style_servers_json_supported(self):
        (self.project / "servers.json").write_text(
            json.dumps([self.local, self.remote]), encoding="utf-8"
        )
        self.assertTrue(self.invoke(["4", "2", "remote"]))
        self.assertEqual(self.json_file("servers.json"), [self.local])

    def test_rollback_restores_previous_files_when_second_write_fails(self):
        from scripts.manage_servers import safe_write as actual_write
        before = {
            p.name: p.read_bytes() for p in self.project.iterdir() if p.is_file()
        }
        attempts = 0

        def unreliable_write(path, payload, mode):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError("fake disk failure")
            return actual_write(path, payload, mode)

        with patch("scripts.manage_servers.safe_write", side_effect=unreliable_write):
            with self.assertRaisesRegex(ConfigurationError, "Запись не завершена"):
                self.invoke(["4", "2", "remote"])
        for name, content in before.items():
            self.assertEqual((self.project / name).read_bytes(), content)
        self.assert_files_untouched()

    def test_custom_servers_and_users_paths_are_backed_up(self):
        (self.project / "servers.json").rename(self.project / "mc_registry.json")
        (self.project / "users.json").rename(self.project / "access.json")
        env_path = self.project / ".env"
        env_path.write_text(env_path.read_text(encoding="utf-8") +
                            'MINECRAFT_SERVERS_FILE="mc_registry.json"\n'
                            'ACCESS_USERS_FILE="access.json"\n', encoding="utf-8")
        self.assertTrue(self.invoke(["4", "2", "remote"]))
        saved = json.loads((self.project / "mc_registry.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["servers"], [self.local])
        self.assertEqual(set(self.json_file("access.json")["users"]["123"]["servers"]),
                         {"survival"})
        names = self.backup_names()
        self.assertIn("users.json", names)
        self.assertIn("servers.json", names)

    def test_corrupted_users_json_aborts_without_changes(self):
        original = (self.project / "servers.json").read_bytes()
        (self.project / "users.json").write_text('{"users": broken}', encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            self.invoke(["4", "2"])
        self.assertEqual((self.project / "servers.json").read_bytes(), original)
        self.assertFalse((self.project / "config_backups").exists())

    def test_cannot_delete_if_shell_default_override_points_to_target(self):
        with (
            patch("builtins.input", side_effect=["4", "2"]),
            patch("builtins.print"),
            patch.dict(os.environ, {"MINECRAFT_SERVERS_FILE": "",
                                    "DEFAULT_SERVER_ID": "remote"}),
        ):
            with self.assertRaisesRegex(ConfigurationError, "внешнем окружении"):
                run_wizard(self.project)
        self.assertFalse((self.project / "config_backups").exists())
        self.assertEqual(self.json_file("servers.json"), self.saved)


if __name__ == "__main__":
    unittest.main()
