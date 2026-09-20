import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from config import (
    SERVER_DIR,
    SERVER_OUTPUT_LOG,
    SERVER_PID_FILE,
    SERVER_START_COMMAND,
    SERVER_STOP_TIMEOUT,
)
from services.rcon import send_rcon_command

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ServerStatus:
    running: bool
    pid: int | None = None
    uptime_seconds: int = 0
    memory_mb: float = 0.0


class ServerProcessManager:
    """Starts and controls the Minecraft server without systemd or shell=True."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.server_dir = Path(SERVER_DIR).expanduser().resolve()
        self.pid_file = Path(SERVER_PID_FILE).expanduser().resolve()
        self.output_log = Path(SERVER_OUTPUT_LOG).expanduser().resolve()

    def _build_argv(self) -> list[str]:
        argv = shlex.split(SERVER_START_COMMAND)
        if not argv:
            raise RuntimeError("SERVER_START_COMMAND is empty")

        first = argv[0]
        candidate = Path(first).expanduser()
        if not candidate.is_absolute():
            candidate = self.server_dir / candidate

        if first.lower().endswith((".sh", ".bash")):
            if not candidate.is_file():
                raise FileNotFoundError(f"Start script not found: {candidate}")
            return ["/bin/bash", str(candidate), *argv[1:]]

        return argv

    def _read_pid_record(self) -> dict | None:
        try:
            data = json.loads(self.pid_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def _remove_pid_file(self) -> None:
        try:
            self.pid_file.unlink()
        except FileNotFoundError:
            pass

    def _get_managed_process(self) -> psutil.Process | None:
        record = self._read_pid_record()
        if not record:
            return None

        try:
            pid = int(record["pid"])
            expected_create_time = float(record["create_time"])
            process = psutil.Process(pid)
            if abs(process.create_time() - expected_create_time) > 1.0:
                self._remove_pid_file()
                return None
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                self._remove_pid_file()
                return None
            return process
        except (KeyError, TypeError, ValueError, psutil.Error):
            self._remove_pid_file()
            return None

    def _write_pid_record(self, process: psutil.Process) -> None:
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.pid_file.with_suffix(self.pid_file.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "pid": process.pid,
                    "create_time": process.create_time(),
                    "command": SERVER_START_COMMAND,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self.pid_file)

    async def status(self) -> ServerStatus:
        process = await asyncio.to_thread(self._get_managed_process)
        if process is None:
            return ServerStatus(running=False)

        try:
            info = await asyncio.to_thread(
                lambda: (
                    process.pid,
                    max(0, int(time.time() - process.create_time())),
                    process.memory_info().rss / 1024 / 1024,
                )
            )
            return ServerStatus(
                running=True,
                pid=info[0],
                uptime_seconds=info[1],
                memory_mb=info[2],
            )
        except psutil.Error:
            return ServerStatus(running=False)

    async def start(self) -> str:
        async with self._lock:
            if await asyncio.to_thread(self._get_managed_process):
                return "already_running"

            if not self.server_dir.is_dir():
                raise FileNotFoundError(f"SERVER_DIR not found: {self.server_dir}")

            argv = self._build_argv()
            self.output_log.parent.mkdir(parents=True, exist_ok=True)

            def _spawn() -> subprocess.Popen:
                log_handle = open(self.output_log, "ab", buffering=0)
                try:
                    return subprocess.Popen(
                        argv,
                        cwd=self.server_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                finally:
                    log_handle.close()

            child = await asyncio.to_thread(_spawn)
            await asyncio.sleep(0.8)
            return_code = child.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"Server process exited immediately with code {return_code}. "
                    f"Check {self.output_log}"
                )

            process = psutil.Process(child.pid)
            await asyncio.to_thread(self._write_pid_record, process)
            logger.info("Minecraft server started: pid=%s argv=%r", child.pid, argv)
            return "started"

    async def _wait_stopped(self, process: psutil.Process, timeout: float) -> bool:
        try:
            await asyncio.to_thread(process.wait, timeout)
            return True
        except psutil.TimeoutExpired:
            return False
        except psutil.NoSuchProcess:
            return True

    async def stop(self) -> str:
        async with self._lock:
            process = await asyncio.to_thread(self._get_managed_process)
            if process is None:
                return "already_stopped"

            # First ask Minecraft to save and stop cleanly.
            try:
                await send_rcon_command("stop")
            except Exception:  # noqa: BLE001
                logger.exception("Graceful RCON stop failed")

            if await self._wait_stopped(process, SERVER_STOP_TIMEOUT):
                self._remove_pid_file()
                return "stopped"

            # RCON unavailable or ignored: terminate the whole process group.
            try:
                pgid = await asyncio.to_thread(os.getpgid, process.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, psutil.Error):
                try:
                    process.terminate()
                except psutil.NoSuchProcess:
                    pass

            if await self._wait_stopped(process, min(10.0, SERVER_STOP_TIMEOUT)):
                self._remove_pid_file()
                return "stopped"

            try:
                pgid = await asyncio.to_thread(os.getpgid, process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, psutil.Error):
                try:
                    process.kill()
                except psutil.NoSuchProcess:
                    pass

            await self._wait_stopped(process, 5.0)
            self._remove_pid_file()
            return "killed"

    async def restart(self) -> str:
        await self.stop()
        await asyncio.sleep(1.0)
        return await self.start()

    async def tail_output(self, lines: int = 30) -> str:
        lines = max(1, min(lines, 100))

        def _read() -> str:
            try:
                with self.output_log.open("r", encoding="utf-8", errors="replace") as fh:
                    return "".join(fh.readlines()[-lines:]).strip()
            except FileNotFoundError:
                return ""

        return await asyncio.to_thread(_read)


server_process_manager = ServerProcessManager()
