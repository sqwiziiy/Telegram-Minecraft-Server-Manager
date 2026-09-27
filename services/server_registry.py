import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from config import (
    DEFAULT_SERVER_ID,
    BACKUP_DIR,
    MINECRAFT_SERVERS_FILE,
    MINECRAFT_LOG_PATH,
    MODS_DIR,
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
    WORLD_DIR,
)
from services.rcon import send_rcon_command
from services.server_process import ServerProcessManager, server_process_manager

logger = logging.getLogger(__name__)
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(slots=True)
class ManagedServer:
    server_id: str
    server_name: str
    manager: ServerProcessManager
    rcon_host: str
    rcon_port: int
    rcon_password: str
    minecraft_log_path: str
    mods_dir: str
    world_dir: str
    backup_dir: str

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
    """Load all modern servers from JSON, with a legacy env-only fallback."""

    def __init__(self) -> None:
        self._servers: dict[str, ManagedServer] = {}

        path = Path(MINECRAFT_SERVERS_FILE)
        if path.exists():
            loaded = self._load_json_servers(path)
            if not loaded:
                logger.warning(
                    "Minecraft servers file %s is empty; falling back to deprecated "
                    "single-server environment configuration",
                    path,
                )
                self._add(self._legacy_server())
        else:
            logger.warning(
                "Minecraft servers file %s is missing; using deprecated single-server "
                "environment configuration",
                path,
            )
            self._add(self._legacy_server())

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

    @staticmethod
    def _validate_server_id(server_id: str) -> str:
        if not _SERVER_ID_RE.fullmatch(server_id):
            raise RuntimeError(
                f"Server id {server_id!r} is invalid; use 1-32 lowercase ASCII "
                "letters, digits, '-' or '_' and start with a letter or digit"
            )
        return server_id

    def _legacy_server(self) -> ManagedServer:
        self._validate_server_id(SERVER_ID)
        return ManagedServer(
            server_id=SERVER_ID,
            server_name=SERVER_NAME,
            manager=server_process_manager,
            rcon_host=RCON_HOST,
            rcon_port=RCON_PORT,
            rcon_password=RCON_PASSWORD,
            minecraft_log_path=MINECRAFT_LOG_PATH,
            mods_dir=MODS_DIR,
            world_dir=WORLD_DIR,
            backup_dir=BACKUP_DIR,
        )

    def _load_json_servers(self, path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to read Minecraft servers file {path}: {exc}") from exc

        items = payload.get("servers") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise RuntimeError(
                f"{path} must contain a JSON list or an object with a 'servers' list"
            )

        if not items:
            return False

        for raw in items:
            if not isinstance(raw, dict):
                raise RuntimeError(f"Invalid server entry in {path}: expected object")

            server_id = self._required_text(raw, "id")
            self._validate_server_id(server_id)
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
            try:
                rcon_port = int(raw.get("rcon_port", 25575))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Server {server_id!r} has invalid rcon_port") from exc
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
                    minecraft_log_path=self._path_value(raw, "minecraft_log_path", server_dir / "logs" / "latest.log"),
                    mods_dir=self._path_value(raw, "mods_dir", server_dir / "mods"),
                    world_dir=self._path_value(raw, "world_dir", server_dir / "world"),
                    backup_dir=self._path_value(raw, "backup_dir", server_dir / "backups"),
                )
            )
        return True

    @staticmethod
    def _path_value(raw: dict, key: str, default: Path) -> str:
        value = raw.get(key)
        return str(Path(value if value else default).expanduser().resolve())

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
