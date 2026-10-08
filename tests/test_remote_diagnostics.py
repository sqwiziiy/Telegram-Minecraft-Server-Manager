"""Read-only checks for remote Minecraft connectivity and key permissions."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")

from scripts.check_remote_servers import diagnose, key_warning


class SSHKeyDiagnosticsTests(unittest.TestCase):
    def test_private_key_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "minecraft_bot"
            key.write_text("test-only", encoding="utf-8")
            key.chmod(0o600)
            self.assertIsNone(key_warning(str(key)))
            key.chmod(0o644)
            self.assertIn("group/world accessible", key_warning(str(key)))
            self.assertIsNotNone(key_warning(str(key.parent / "absent")))


class RemoteDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_diagnostic_omits_server_properties_and_rcon_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "minecraft_bot"
            key.write_text("fake", encoding="utf-8")
            key.chmod(0o600)
            remote = SimpleNamespace(
                settings=SimpleNamespace(
                    host="example.local", user="minecraft", port=22, key_file=str(key)
                ),
                request=AsyncMock(side_effect=[
                    {"python": "3.14.0", "properties": "rcon.password=very-private-value"},
                    {"running": True, "pid": 4055},
                ]),
            )
            server = SimpleNamespace(
                server_id="test", server_name="Test", ssh_remote=remote,
            )
            with patch("builtins.print") as emit:
                self.assertTrue(await diagnose(server))
            output = "\n".join(str(call) for call in emit.call_args_list)
            self.assertNotIn("very-private-value", output)
            self.assertIn("managed PID 4055", output)
            self.assertEqual(remote.request.await_count, 2)


if __name__ == "__main__":
    unittest.main()
