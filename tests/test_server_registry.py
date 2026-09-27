import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services import server_registry as registry_module  # noqa: E402
from config import SERVER_ID  # noqa: E402


class ServerRegistryTests(unittest.TestCase):
    def test_loads_multiple_servers_and_uses_relative_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"servers": [
                {"id": "storm-survival", "name": "Storm Survival", "server_dir": str(root / "storm"), "rcon_port": 25575},
                {"id": "create", "name": "Create", "server_dir": str(root / "create"), "rcon_port": 25576},
                {"id": "vanilla", "name": "Vanilla", "server_dir": str(root / "vanilla"), "rcon_port": 25577},
            ]}
            config_path = root / "servers.json"
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(config_path)):
                registry = registry_module.ServerRegistry()
            self.assertEqual([s.server_id for s in registry.list()], ["storm-survival", "create", "vanilla"])
            self.assertEqual(registry.default().server_id, "storm-survival")
            self.assertEqual(registry.get("create").mods_dir, str((root / "create" / "mods").resolve()))
            self.assertEqual(registry.get("vanilla").world_dir, str((root / "vanilla" / "world").resolve()))

    def test_duplicate_ids_and_invalid_ports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for payload in (
                {"servers": [
                    {"id": "duplicate", "name": "One", "server_dir": str(root)},
                    {"id": "duplicate", "name": "Two", "server_dir": str(root)},
                ]},
                {"servers": [{"id": "bad", "name": "Bad", "server_dir": str(root), "rcon_port": 70000}]},
            ):
                path = root / "servers.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(path)):
                    with self.assertRaises(RuntimeError):
                        registry_module.ServerRegistry()

    def test_missing_or_empty_file_uses_legacy_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            with patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(missing)):
                registry = registry_module.ServerRegistry()
            self.assertEqual([s.server_id for s in registry.list()], [SERVER_ID])

            empty = root / "empty.json"
            empty.write_text(json.dumps({"servers": []}), encoding="utf-8")
            with patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(empty)):
                registry = registry_module.ServerRegistry()
            self.assertEqual([s.server_id for s in registry.list()], [SERVER_ID])

    def test_invalid_default_id_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "servers.json"
            path.write_text(json.dumps({"servers": [
                {"id": "storm-survival", "name": "Storm", "server_dir": tmp},
            ]}), encoding="utf-8")
            with (
                patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(path)),
                patch.object(registry_module, "DEFAULT_SERVER_ID", "missing"),
            ):
                with self.assertRaisesRegex(RuntimeError, "DEFAULT_SERVER_ID"):
                    registry_module.ServerRegistry()

    def test_server_id_slug_validation(self) -> None:
        invalid_ids = [
            "bad:id",
            "has space",
            "path/name",
            "😀emoji",
            "a" * 33,
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for server_id in invalid_ids:
                path = Path(tmp) / "servers.json"
                path.write_text(json.dumps({"servers": [{
                    "id": server_id, "name": "Bad", "server_dir": tmp,
                }]}), encoding="utf-8")
                with patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(path)):
                    with self.assertRaisesRegex(RuntimeError, "Server id"):
                        registry_module.ServerRegistry()

            path.write_text(json.dumps({"servers": [{
                "id": "storm-survival", "name": "Storm", "server_dir": tmp,
            }]}), encoding="utf-8")
            with patch.object(registry_module, "MINECRAFT_SERVERS_FILE", str(path)):
                registry = registry_module.ServerRegistry()
            self.assertEqual(registry.list()[0].server_id, "storm-survival")


if __name__ == "__main__":
    unittest.main()
