"""SSH transport and process manager for a remote Linux Minecraft server.

SSH host keys are ALWAYS verified against the user's known_hosts. Only key
authentication is allowed. Remote actions execute a bundled Python agent via
stdin JSON, never interpolating user input into shell commands.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, TypeVar

import asyncssh

from services.server_process import ServerStatus

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass(frozen=True)
class SSHSettings:
    host: str
    user: str
    port: int = 22
    key_file: str = "~/.ssh/id_ed25519"
    known_hosts: str = "~/.ssh/known_hosts"

    def validate(self) -> None:
        if not self.host or not self.user or any(c.isspace() for c in self.host + self.user):
            raise ValueError("SSH host/user must be non-empty without whitespace")
        if not 1 <= self.port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        for label, raw in (("SSH key", self.key_file), ("SSH known_hosts", self.known_hosts)):
            filename = Path(raw).expanduser()
            if not filename.is_file():
                raise FileNotFoundError(f"{label} file not found: {filename}")


@lru_cache(maxsize=1)
def _agent_command() -> str:
    source = (Path(__file__).resolve().parent / "remote_agent.py").read_text(encoding="utf-8")
    return "python3 -c " + shlex.quote(source)


class SSHRemote:
    def __init__(self, settings: SSHSettings, config: dict) -> None:
        settings.validate()
        self.settings = settings
        self.config = config

    def _connection_kwargs(self) -> dict:
        return {
            "host": self.settings.host,
            "port": self.settings.port,
            "username": self.settings.user,
            "client_keys": [str(Path(self.settings.key_file).expanduser())],
            "known_hosts": str(Path(self.settings.known_hosts).expanduser()),
            "connect_timeout": 15,
            "agent_forwarding": False,
        }

    async def _connect(self):
        try:
            return await asyncssh.connect(**self._connection_kwargs())
        except asyncssh.PermissionDenied as exc:
            raise ConnectionError(
                f"SSH authentication rejected for {self.settings.user}@{self.settings.host}. "
                f"Check key_file ({self.settings.key_file}), authorized_keys and the "
                "service user's SSH access without an interactive agent."
            ) from exc
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"SSH connection to {self.settings.user}@{self.settings.host}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    async def request(self, action: str, *, timeout: float = 90, **params):
        request = {"action": action, "config": self.config, **params}
        try:
            async with await self._connect() as conn:
                result = await conn.run(
                    _agent_command(),
                    input=json.dumps(request, ensure_ascii=False),
                    timeout=timeout,
                    check=False,
                )
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            uncertainty = (
                " Operation may have completed remotely: refresh server status before retrying."
                if action in {"start", "stop", "restart", "backup", "configure_properties"}
                else ""
            )
            raise ConnectionError(
                f"SSH {action} transport failed: {type(exc).__name__}: {exc}.{uncertainty}"
            ) from exc
        if not result.stdout.strip():
            raise RuntimeError(f"SSH agent {action} returned no response: {result.stderr[-500:]}")
        try:
            response = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"SSH agent {action} returned invalid JSON") from exc
        if not response.get("ok"):
            raise RuntimeError(f"SSH {action}: {response.get('error', 'remote error')}")
        if result.exit_status:
            raise RuntimeError(f"SSH agent {action} exited with status {result.exit_status}")
        return response["value"]

    async def upload_mod(self, filename: str, source_path: str) -> str:
        # Names are re-validated and destination confined inside mods_dir by
        # the remote agent (commit_mod), never supplied as shell commands.
        import re

        if not re.fullmatch(r"[A-Za-z0-9_\-. +\[\]()@#]+\.jar", filename):
            raise ValueError("Invalid mod filename")
        temp_name = ".upload-" + secrets.token_hex(12)
        mod_dir = self.config["mods_dir"].rstrip("/")
        temp_path = mod_dir + "/" + temp_name
        try:
            # SFTP or remote commit must not hang the Telegram callback forever.
            async with asyncio.timeout(300):
                async with await self._connect() as conn:
                    async with conn.start_sftp_client() as sftp:
                        try:
                            await sftp.put(source_path, temp_path)
                            return await self._request_on_connection(
                                conn, "commit_mod", filename=filename, temporary_name=temp_name
                            )
                        finally:
                            try:
                                await sftp.remove(temp_path)
                            except (OSError, asyncssh.SFTPError):
                                pass
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"SSH/SFTP upload interrupted ({type(exc).__name__}). "
                "Check the mods/plugins list before uploading again."
            ) from exc

    async def _request_on_connection(self, conn, action: str, **params):
        result = await conn.run(
            _agent_command(),
            input=json.dumps({"action": action, "config": self.config, **params}, ensure_ascii=False),
            timeout=90,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"SSH agent {action} returned invalid JSON") from exc
        if not payload.get("ok") or result.exit_status:
            raise RuntimeError(f"SSH {action}: {payload.get('error', 'remote error')}")
        return payload["value"]

    async def follow_log(self, path: str) -> AsyncIterator[str]:
        # tail is provided by coreutils on supported Linux remote hosts.
        # This session is deliberately long-lived; main.py retries on drop.
        async with await self._connect() as conn:
            process = await conn.create_process("tail -n 0 -F -- " + shlex.quote(path))
            try:
                async for line in process.stdout:
                    yield line.rstrip("\n")
            finally:
                process.close()
                await process.wait_closed()


class RemoteServerProcessManager:
    def __init__(self, remote: SSHRemote) -> None:
        self.remote = remote
        self._lock = asyncio.Lock()
        self.server_dir = Path(remote.config["server_dir"])
        self.output_log = Path(remote.config["output_log"])

    async def status(self) -> ServerStatus:
        result = await self.remote.request("status", timeout=25)
        return ServerStatus(
            running=bool(result["running"]),
            pid=result.get("pid"),
            uptime_seconds=int(result.get("uptime_seconds", 0)),
            memory_mb=float(result.get("memory_mb", 0)),
        )

    async def run_if_stopped(
        self, action: Callable[[], Awaitable[_T]]
    ) -> tuple[bool, _T | None]:
        async with self._lock:
            if (await self.status()).running:
                return False, None
            return True, await action()

    async def start(self) -> str:
        async with self._lock:
            return await self.remote.request("start", timeout=40)

    async def stop(self) -> str:
        async with self._lock:
            return await self.remote.request(
                "stop", timeout=float(self.remote.config.get("stop_timeout", 45)) + 35
            )

    async def restart(self) -> str:
        async with self._lock:
            return await self.remote.request(
                "restart", timeout=float(self.remote.config.get("stop_timeout", 45)) + 45
            )

    async def tail_output(self, lines: int = 30) -> str:
        return await self.remote.request("tail_output", lines=lines, timeout=20)
