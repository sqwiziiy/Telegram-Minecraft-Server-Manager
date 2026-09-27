import json
import os
from dataclasses import dataclass
from pathlib import Path

from config import (
    DEFAULT_SERVER_ID,
    MINECRAFT_SERVERS_FILE,
    RCON_HOST,
    RCON_PASSWORD,
    RCON_PORT,
    SERVER_DIR,
    SERVER_ID,
    SERVER_NAME,
    SERVER_OUTPUT_LOG,
    SERVER_PID_FILE,
    SERVER_START_COMMAND,
    SERVER_STOP_TIMEOUT,
)
from services.rcon import send_rcon_command
from services.server_process import ServerProcessManager, server_process_manager


@dataclass(slots=True)
class ManagedServer:
    server_id: str
    server_name: str
    manager: ServerProcessManager
    rcon_host: str
    rcon_port: int
    rcon_password: str

    @property
    def rcon_configured(self) -> bool:
        return bool(self.rcon_password)

    async def rcon(self, command: str) -> str:
        return await send_rcon_command(
            command,
            host=self.rcon_host,
            port=self.rcon_port,
            password=self.rcon_password,
        )


class ServerRegistry:
    """Registry for the default server plus optional additional servers."""

    def __init__(self) -> None:
        self._servers: dict[str, ManagedServer] = {}

        self._add(
            ManagedServer(
                server_id=SERVER_ID,
                server_name=SERVER_NAME,
                manager=server_process_manager,
                rcon_host=RCON_HOST,
                rcon_port=RCON_PORT,
                rcon_password=RCON_PASSWORD,
            )
        )
        self._load_additional_servers()

        if DEFAULT_SERVER_ID not in self._servers:
            raise RuntimeError(
                f"DEFAULT_SERVER_ID={DEFAULT_SERVER_ID!r} is not present in the server registry"
            )

    def _add(self, server: ManagedServer) -> None:
        if server.server_id in self._servers:
            raise RuntimeError(f"Duplicate Minecraft server id: {server.server_id!r}")
        self._servers[server.server_id] = server

    @staticmethod
    def _required_text(raw: dict, key: str) -> str:
        value = str(raw.get(key, "")).strip()
        if not value:
            raise RuntimeError(f"Additional server is missing required field {key!r}")
        return value

    def _load_additional_servers(self) -> None:
        path = Path(MINECRAFT_SERVERS_FILE)
        if not path.exists():
            return

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to read Minecraft servers file {path}: {exc}") from exc

        items = payload.get("servers") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise RuntimeError(
                f"{path} must contain a JSON list or an object with a 'servers' list"
            )

        for raw in items:
            if not isinstance(raw, dict):
                raise RuntimeError(f"Invalid server entry in {path}: expected object")

            server_id = self._required_text(raw, "id")
            server_name = self._required_text(raw, "name")
            server_dir = Path(self._required_text(raw, "server_dir")).expanduser().resolve()

            start_command = str(raw.get("start_command", "./start.sh")).strip() or "./start.sh"
            pid_file = str(
                Path(raw.get("pid_file") or server_dir / ".telegram-mc-manager.pid")
                .expanduser()
                .resolve()
            )
            output_log = str(
                Path(raw.get("output_log") or server_dir / "logs" / "manager-console.log")
                .expanduser()
                .resolve()
            )
            stop_timeout = max(5.0, float(raw.get("stop_timeout", SERVER_STOP_TIMEOUT)))

            rcon_host = str(raw.get("rcon_host", "127.0.0.1")).strip() or "127.0.0.1"
            rcon_port = int(raw.get("rcon_port", 25575))
            if not 1 <= rcon_port <= 65535:
                raise RuntimeError(
                    f"Server {server_id!r} has invalid rcon_port={rcon_port}"
                )

            password_env = str(raw.get("rcon_password_env", "")).strip()
            rcon_password = os.getenv(password_env, "") if password_env else ""

            manager = ServerProcessManager(
                server_dir=str(server_dir),
                start_command=start_command,
                pid_file=pid_file,
                output_log=output_log,
                stop_timeout=stop_timeout,
                rcon_host=rcon_host,
                rcon_port=rcon_port,
                rcon_password=rcon_password,
            )

            self._add(
                ManagedServer(
                    server_id=server_id,
                    server_name=server_name,
                    manager=manager,
                    rcon_host=rcon_host,
                    rcon_port=rcon_port,
                    rcon_password=rcon_password,
                )
            )

    def list(self) -> list[ManagedServer]:
        return list(self._servers.values())

    def get(self, server_id: str) -> ManagedServer:
        try:
            return self._servers[server_id]
        except KeyError as exc:
            known = ", ".join(sorted(self._servers))
            raise KeyError(f"Unknown Minecraft server {server_id!r}. Known: {known}") from exc

    def default(self) -> ManagedServer:
        return self.get(DEFAULT_SERVER_ID)


server_registry = ServerRegistry()
