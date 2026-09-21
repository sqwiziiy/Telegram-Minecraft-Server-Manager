import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.access_control import ALL_PERMISSIONS, AccessControl  # noqa: E402


class AccessControlTests(unittest.TestCase):
    def _write_users(self, root: str, payload: object) -> Path:
        path = Path(root) / "users.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_operator_has_server_controls_and_read_only_mods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            users_file = self._write_users(
                tmp,
                {
                    "users": {
                        "222": {
                            "name": "Friend",
                            "role": "operator",
                            "allow": [],
                            "deny": [],
                        }
                    }
                },
            )
            access = AccessControl(owner_ids=[111], users_file=users_file)

            self.assertTrue(access.can(222, "server.status"))
            self.assertTrue(access.can(222, "server.start"))
            self.assertTrue(access.can(222, "server.stop"))
            self.assertTrue(access.can(222, "server.restart"))
            self.assertTrue(access.can(222, "mods.view"))
            self.assertFalse(access.can(222, "console.use"))
            self.assertFalse(access.can(222, "mods.upload"))
            self.assertFalse(access.can(222, "mods.delete"))
            self.assertFalse(access.can(222, "backup.create"))
            self.assertFalse(access.can(222, "logs.view"))

    def test_allow_adds_and_deny_overrides_role_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            users_file = self._write_users(
                tmp,
                {
                    "users": {
                        "222": {
                            "role": "operator",
                            "allow": ["backup.create"],
                            "deny": ["server.restart"],
                        }
                    }
                },
            )
            access = AccessControl(owner_ids=[111], users_file=users_file)

            self.assertTrue(access.can(222, "backup.create"))
            self.assertFalse(access.can(222, "server.restart"))

    def test_owner_and_legacy_admin_have_full_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            access = AccessControl(
                owner_ids=[111],
                legacy_admin_ids=[333],
                users_file=Path(tmp) / "missing.json",
            )

            for permission in ALL_PERMISSIONS:
                self.assertTrue(access.can(111, permission))
                self.assertTrue(access.can(333, permission))

    def test_unknown_user_and_unknown_permission_are_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            access = AccessControl(
                owner_ids=[111],
                users_file=Path(tmp) / "missing.json",
            )

            self.assertFalse(access.is_authorized(999))
            self.assertFalse(access.can(999, "server.start"))
            self.assertFalse(access.can(111, "made.up.permission"))

    def test_unknown_role_is_ignored_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            users_file = self._write_users(
                tmp,
                {"users": {"222": {"role": "superuser", "allow": ["console.use"]}}},
            )
            access = AccessControl(owner_ids=[111], users_file=users_file)

            self.assertFalse(access.is_authorized(222))
            self.assertFalse(access.can(222, "console.use"))

    def test_unknown_permission_name_does_not_grant_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            users_file = self._write_users(
                tmp,
                {
                    "users": {
                        "222": {
                            "role": "custom",
                            "allow": ["console.use", "root.shell"],
                        }
                    }
                },
            )
            access = AccessControl(owner_ids=[111], users_file=users_file)

            self.assertTrue(access.can(222, "console.use"))
            self.assertFalse(access.can(222, "root.shell"))

    def test_malformed_json_fails_closed_for_file_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            users_file = Path(tmp) / "users.json"
            users_file.write_text("{ definitely not json", encoding="utf-8")
            access = AccessControl(owner_ids=[111], users_file=users_file)

            self.assertTrue(access.is_authorized(111))
            self.assertFalse(access.is_authorized(222))


if __name__ == "__main__":
    unittest.main()
