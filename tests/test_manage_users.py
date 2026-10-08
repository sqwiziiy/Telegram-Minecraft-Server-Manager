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

from scripts.manage_users import (  # noqa: E402
    ConfigurationError,
    PERMISSION_IDS,
    UserEditor,
    apply_policy,
    atomic_save,
    configured_path,
    edit_custom,
    effective_permissions,
    load_servers,
    load_users,
    read_simple_env,
)
from services.access_control import ALL_PERMISSIONS  # noqa: E402


class AccessEditorTests(unittest.TestCase):
    def test_permission_choices_follow_access_control(self) -> None:
        self.assertEqual(PERMISSION_IDS, ALL_PERMISSIONS - {"system.view"})

    def test_configured_paths_read_dotenv_and_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                'ACCESS_USERS_FILE="./config/users.json"\n'
                'MINECRAFT_SERVERS_FILE=./config/servers.json\n',
                encoding="utf-8",
            )
            env = read_simple_env(root)
            with patch.dict(os.environ, {"ACCESS_USERS_FILE": ""}):
                self.assertEqual(
                    configured_path(root, env, "ACCESS_USERS_FILE", "users.json"),
                    root / "config" / "users.json",
                )
            with patch.dict(os.environ, {"ACCESS_USERS_FILE": str(root / "manual.json")}):
                self.assertEqual(
                    configured_path(root, env, "ACCESS_USERS_FILE", "users.json"),
                    root / "manual.json",
                )

    def test_servers_are_selected_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "servers.json"
            path.write_text(json.dumps({"servers": [
                {"id": "storm-survival", "name": "Storm Survival"},
                {"id": "test-server", "name": "Test Server"},
            ]}), encoding="utf-8")
            self.assertEqual(
                load_servers(path),
                [("storm-survival", "Storm Survival"), ("test-server", "Test Server")],
            )

    def test_invalid_user_json_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            path.write_text('{"users": { "123":', encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaises(ConfigurationError):
                load_users(path)
            self.assertEqual(path.read_bytes(), before)

    def test_add_server_access_keeps_other_user_policies(self) -> None:
        cfg = {"users": {
            "7472435566": {"name": "test", "servers": {
                "storm-survival": {
                    "role": "custom", "allow": ["server.start", "server.status"], "deny": [],
                },
            }},
            "777": {"name": "Friend", "servers": {
                "storm-survival": {"role": "operator", "allow": [], "deny": ["server.restart"]},
            }},
        }}
        unchanged = json.loads(json.dumps(cfg))
        self.assertTrue(apply_policy(
            cfg, "7472435566", "test-server",
            {"role": "viewer", "allow": [], "deny": []},
            "storm-survival",
        ))
        self.assertEqual(
            cfg["users"]["7472435566"]["servers"]["storm-survival"],
            unchanged["users"]["7472435566"]["servers"]["storm-survival"],
        )
        self.assertEqual(cfg["users"]["777"], unchanged["users"]["777"])
        self.assertEqual(
            effective_permissions(cfg["users"]["7472435566"]["servers"]["test-server"]),
            {"server.status", "mods.view"},
        )

    def test_remove_one_server_access_keeps_other(self) -> None:
        cfg = {"users": {"123": {"servers": {
            "storm": {"role": "admin", "allow": [], "deny": []},
            "test": {"role": "viewer", "allow": [], "deny": []},
        }}}}
        self.assertTrue(apply_policy(cfg, "123", "test", None, "storm"))
        self.assertEqual(list(cfg["users"]["123"]["servers"]), ["storm"])

    def test_legacy_policy_migrates_to_default_without_expanding(self) -> None:
        cfg = {"users": {"123": {
            "name": "Old", "role": "operator", "allow": [],
            "deny": ["server.restart"],
        }}}
        self.assertTrue(apply_policy(
            cfg, "123", "test-server",
            {"role": "custom", "allow": ["server.status"], "deny": []},
            "storm-survival",
        ))
        self.assertEqual(
            cfg["users"]["123"]["servers"]["storm-survival"],
            {"role": "operator", "allow": [], "deny": ["server.restart"]},
        )
        self.assertEqual(
            cfg["users"]["123"]["servers"]["test-server"]["allow"],
            ["server.status"],
        )

    def test_atomic_save_keeps_timestamped_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            original = b'{ "users": { "1": {"name": "Friend", "servers": {}}} }\n'
            path.write_bytes(original)
            backup = atomic_save(path, {"users": {"1": {
                "name": "Friend", "servers": {"test": {
                    "role": "viewer", "allow": [], "deny": [],
                }},
            }}}, original, backup_dir=Path(tmp) / "config_backups")
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_bytes(), original)
            data, _ = load_users(path)
            self.assertIn("test", data["users"]["1"]["servers"])
            self.assertEqual(len(list((Path(tmp) / "config_backups").glob("users.json.bak-*"))), 1)
            self.assertFalse(list(Path(tmp).glob("users.json.bak-*")))
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_atomic_save_rejects_external_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.json"
            original = b'{"users":{}}'
            path.write_bytes(original)
            path.write_bytes(b'{"users":{"1":{}}}')
            with self.assertRaisesRegex(ConfigurationError, "изменён другой программой"):
                atomic_save(path, {"users": {}}, original, backup_dir=Path(tmp) / "config_backups")
            self.assertEqual(path.read_bytes(), b'{"users":{"1":{}}}')
            self.assertFalse((Path(tmp) / "config_backups").exists())

    def test_interactive_add_and_edit_across_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            users_path = Path(tmp) / "users.json"
            editor = UserEditor(
                users_path,
                [("storm-survival", "Storm"), ("test-server", "Test")],
                "storm-survival",
                backup_dir=Path(tmp) / "config_backups",
            )
            with (
                patch("builtins.input", side_effect=[
                    "a", "7472435566", "test", "1", "2",
                    "1", "2", "3", "0", "0",
                ]),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                editor.run()
            data, _ = load_users(users_path)
            user = data["users"]["7472435566"]
            self.assertEqual(user["servers"]["storm-survival"]["role"], "custom")
            self.assertEqual(user["servers"]["test-server"]["role"], "operator")
            self.assertEqual(len(list((Path(tmp) / "config_backups").glob("users.json.bak-*"))), 1)
            self.assertFalse(list(Path(tmp).glob("users.json.bak-*")))

    def test_custom_permission_toggle(self) -> None:
        existing = {"role": "viewer", "allow": [], "deny": []}
        with (
            patch("builtins.input", side_effect=["2", "s"]),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            policy = edit_custom(existing)
        self.assertEqual(
            effective_permissions(policy),
            {"server.status", "server.start", "mods.view"},
        )


if __name__ == "__main__":
    unittest.main()
