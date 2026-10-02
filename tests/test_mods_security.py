import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from handlers.mods import _safe_upload_name  # noqa: E402


class ModsSecurityTests(unittest.TestCase):
    def test_upload_rejects_path_traversal_names(self) -> None:
        for name in ("../evil.jar", "subdir/evil.jar", r"..\evil.jar"):
            self.assertIsNone(_safe_upload_name(name), name)

    def test_upload_accepts_plain_jar_name(self) -> None:
        self.assertEqual(_safe_upload_name("create-2.0.jar"), "create-2.0.jar")


if __name__ == "__main__":
    unittest.main()
