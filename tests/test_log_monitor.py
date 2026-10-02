import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:test-token")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("RCON_PASSWORD", "test-rcon-password")

from services.log_monitor import tail_log  # noqa: E402


class LogMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def _next_after_start(self, generator, write_line) -> str:
        task = asyncio.create_task(generator.__anext__())
        await asyncio.sleep(0.1)
        write_line()
        return await asyncio.wait_for(task, timeout=2)

    async def test_normal_append_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest.log"
            path.write_text("historical joined the game\n", encoding="utf-8")
            generator = tail_log(str(path))
            try:
                line = await self._next_after_start(
                    generator,
                    lambda: path.write_text(
                        path.read_text(encoding="utf-8") + "[Server thread/INFO]: Alex joined the game\n",
                        encoding="utf-8",
                    ),
                )
                self.assertIn("Alex joined the game", line)
            finally:
                await generator.aclose()

    async def test_replacement_log_is_reopened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest.log"
            rotated = Path(tmp) / "latest.log.1"
            path.write_text("old joined the game\n", encoding="utf-8")
            generator = tail_log(str(path))
            try:
                task = asyncio.create_task(generator.__anext__())
                await asyncio.sleep(0.1)
                os.replace(path, rotated)
                path.write_text("[Server thread/INFO]: Bea joined the game\n", encoding="utf-8")
                line = await asyncio.wait_for(task, timeout=2)
                self.assertIn("Bea joined the game", line)
            finally:
                await generator.aclose()

    async def test_truncated_log_is_read_from_new_beginning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latest.log"
            path.write_text("x" * 1000, encoding="utf-8")
            generator = tail_log(str(path))
            try:
                task = asyncio.create_task(generator.__anext__())
                await asyncio.sleep(0.1)
                path.write_text("[Server thread/INFO]: Cy joined the game\n", encoding="utf-8")
                line = await asyncio.wait_for(task, timeout=2)
                self.assertIn("Cy joined the game", line)
            finally:
                await generator.aclose()


if __name__ == "__main__":
    unittest.main()
