import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

_T = TypeVar("_T")
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

# Minecraft may close the RCON socket immediately after accepting "stop".
# In these cases delivery is uncertain, so give the process time to exit cleanly.
_RCON_STOP_UNCERTAIN_MARKERS = (
    "Таймаут ожидания ответа",
    "некорректный RCON-ответ",
)


@dataclass(slots=True)
class ServerStatus:
    running: bool
    pid: int | None = None
    uptime_seconds: int = 0
    memory_mb: float = 0.0


class ServerProcessManager:
    """Start and control one Minecraft server directly, without systemd or shell=True."""

    def __init__(
        self,
        *,
        server_dir: str | None = None,
        start_command: str | None = None,
        pid_file: str | None = None,
        output_log: str | None = None,
        stop_timeout: float | None = None,
        rcon_host: str | None = None,
        rcon_port: int | None = None,
        rcon_password: str | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        server_dir = SERVER_DIR if server_dir is None else server_dir
        start_command = SERVER_START_COMMAND if start_command is None else start_command
        pid_file = SERVER_PID_FILE if pid_file is None else pid_file
        output_log = SERVER_OUTPUT_LOG if output_log is None else output_log
        stop_timeout = SERVER_STOP_TIMEOUT if stop_timeout is None else stop_timeout

        self.server_dir = Path(server_dir).expanduser().resolve()
        self.pid_file = Path(pid_file).expanduser().resolve()
        self.output_log = Path(output_log).expanduser().resolve()
        self.start_command = start_command
        self.stop_timeout = max(5.0, float(stop_timeout))
        self.rcon_host = rcon_host
        self.rcon_port = rcon_port
        self.rcon_password = rcon_password

    def _build_argv(self) -> list[str]:
        argv = shlex.split(self.start_command)
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
                    "command": self.start_command,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self.pid_file)

    @staticmethod
    def _tree_memory_mb(process: psutil.Process) -> float:
        total = 0
        processes = [process]
        try:
            processes.extend(process.children(recursive=True))
        except psutil.Error:
            pass
        for item in processes:
            try:
                total += item.memory_info().rss
            except psutil.Error:
                pass
        return total / 1024 / 1024

    async def status(self) -> ServerStatus:
        process = await asyncio.to_thread(self._get_managed_process)
        if process is None:
            return ServerStatus(running=False)

        try:
            pid, uptime, memory = await asyncio.to_thread(
                lambda: (
                    process.pid,
                    max(0, int(time.time() - process.create_time())),
                    self._tree_memory_mb(process),
                )
            )
            return ServerStatus(
                running=True,
                pid=pid,
                uptime_seconds=uptime,
                memory_mb=memory,
            )
        except psutil.Error:
            return ServerStatus(running=False)

    async def run_if_stopped(
        self, action: Callable[[], Awaitable[_T]]
    ) -> tuple[bool, _T | None]:
        """Run a long offline operation without allowing a concurrent start.

        The same lock protects start/stop. Checking process existence while
        holding it prevents the manager from starting Minecraft mid-backup.
        """
        async with self._lock:
            if await asyncio.to_thread(self._get_managed_process):
                return False, None
            return True, await action()

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

    async def _terminate_process_group(self, process: psutil.Process, sig: signal.Signals) -> None:
        try:
            pgid = await asyncio.to_thread(os.getpgid, process.pid)
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, psutil.Error):
            try:
                if sig == signal.SIGKILL:
                    process.kill()
                else:
                    process.terminate()
            except psutil.NoSuchProcess:
                pass

    @staticmethod
    def _should_wait_after_rcon_stop(result: str) -> bool:
        if not result.startswith("❌"):
            return True
        return any(marker in result for marker in _RCON_STOP_UNCERTAIN_MARKERS)

    async def stop(self) -> str:
        async with self._lock:
            process = await asyncio.to_thread(self._get_managed_process)
            if process is None:
                return "already_stopped"

            # Prefer Minecraft's own shutdown path so the world is saved cleanly.
            rcon_result = ""
            try:
                rcon_result = await send_rcon_command(
                    "stop",
                    host=self.rcon_host,
                    port=self.rcon_port,
                    password=self.rcon_password,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Graceful RCON stop failed")

            if self._should_wait_after_rcon_stop(rcon_result):
                if await self._wait_stopped(process, self.stop_timeout):
                    self._remove_pid_file()
                    return "stopped"
            else:
                logger.warning(
                    "RCON stop was not delivered; falling back to SIGTERM: %s",
                    rcon_result,
                )

            await self._terminate_process_group(process, signal.SIGTERM)
            if await self._wait_stopped(process, min(10.0, self.stop_timeout)):
                self._remove_pid_file()
                return "stopped"

            await self._terminate_process_group(process, signal.SIGKILL)
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


server_process_manager = ServerProcessManager(
    server_dir=SERVER_DIR,
    start_command=SERVER_START_COMMAND,
    pid_file=SERVER_PID_FILE,
    output_log=SERVER_OUTPUT_LOG,
    stop_timeout=SERVER_STOP_TIMEOUT,
)
