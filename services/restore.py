"""Coordinated offline restore: Minecraft start and backups remain locked."""
from __future__ import annotations

import asyncio
import socket
from pathlib import Path

from services import remote_agent
from services.backup import _backup_locks


def _assert_local_offline(server) -> None:
    """Fail closed if a Minecraft instance is running outside bot PID tracking."""
    ports = {int(server.rcon_port)}
    properties = Path(server.manager.server_dir) / "server.properties"
    port = 25565
    if properties.is_file():
        for line in properties.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("server-port="):
                try:
                    port = int(line.partition("=")[2].strip())
                except ValueError as exc:
                    raise RuntimeError("Cannot verify local Minecraft server port") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("Invalid local Minecraft server port")
    ports.add(port)
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                raise RuntimeError(
                    f"Port {port} accepts connections; world restore requires a fully stopped server"
                )
        except OSError:
            pass


async def restore_world(server, filename: str) -> tuple[bool, dict | None]:
    """No network or file modifications unless the managed process is stopped."""
    async def operation() -> dict:
        async with _backup_locks.setdefault(server.server_id, asyncio.Lock()):
            if server.ssh_remote is not None:
                return await server.ssh_remote.request(
                    "restore_world", filename=filename, timeout=4 * 3600
                )
            await asyncio.to_thread(_assert_local_offline, server)
            config = {"world_dir": server.world_dir, "backup_dir": server.backup_dir}
            return await asyncio.to_thread(remote_agent.restore_world_files, config, filename)

    return await server.manager.run_if_stopped(operation)
