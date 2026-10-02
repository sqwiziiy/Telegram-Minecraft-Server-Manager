import asyncio
import gzip
import logging
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path as FilePath

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from config import CONTROL_API_HOST, CONTROL_API_PORT, CONTROL_API_TOKEN
from services.auto_stop import auto_stop_manager
from services.server_registry import ManagedServer, server_registry

logger = logging.getLogger(__name__)

MAX_FILE_READ_BYTES = 2 * 1024 * 1024
MAX_FILE_READ_CHARS = 500_000
MAX_FILE_LIST_ENTRIES = 500

app = FastAPI(
    title="Minecraft Server Manager Control API",
    version="2.0.0",
    description=(
        "Optional multi-server HTTP API for external clients such as bots, "
        "AI agents, Open WebUI/Jarvis, automation systems and scripts. "
        "The Telegram bot and built-in server automation do not depend on this API. "
        "Read-only and mutating operations are exposed as separate OpenAPI functions."
    ),
    docs_url=None,
    redoc_url=None,
)

_bearer = HTTPBearer(auto_error=False)


class RconCommandRequest(BaseModel):
    command: str = Field(
        min_length=1,
        max_length=500,
        description="One Minecraft RCON command without a leading slash.",
    )


class AutoStopRequest(BaseModel):
    timeout_seconds: int = Field(
        ge=0,
        le=86400,
        description=(
            "Seconds the server may stay empty before it is stopped. "
            "Use 0 to disable auto-stop."
        ),
    )


def _require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, CONTROL_API_TOKEN)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _get_server(server_id: str) -> ManagedServer:
    try:
        return server_registry.get(server_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


def _identity(server: ManagedServer) -> dict[str, str]:
    return {
        "server_id": server.server_id,
        "server_name": server.server_name,
    }


async def _status_payload(server: ManagedServer, *, include_players: bool = True) -> dict:
    process = await server.manager.status()
    result = {
        **_identity(server),
        **asdict(process),
        "rcon_configured": server.rcon_configured,
        "auto_stop": auto_stop_manager.status(server),
    }

    if include_players and process.running and server.rcon_configured:
        result["players"] = await server.rcon("list")
    else:
        result["players"] = None

    return result


def _server_root(server: ManagedServer) -> FilePath:
    return server.manager.server_dir.resolve()


def _safe_server_path(server: ManagedServer, relative_path: str) -> tuple[FilePath, FilePath]:
    root = _server_root(server)
    raw = (relative_path or ".").strip()

    candidate_input = FilePath(raw)
    if candidate_input.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path must be relative to the Minecraft server directory",
        )

    candidate = (root / candidate_input).resolve()

    if candidate != root and root not in candidate.parents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="path escapes the Minecraft server directory",
        )

    return root, candidate


def _display_relative_path(root: FilePath, path: FilePath) -> str:
    relative = path.relative_to(root)
    text = relative.as_posix()
    return "." if text == "." else text


def _looks_sensitive(path: FilePath) -> bool:
    lowered_parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()

    if ".git" in lowered_parts:
        return True

    if name in {
        ".env",
        "credentials.json",
        "token.json",
        "accounts.json",
    }:
        return True

    if path.suffix.casefold() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".jks",
        ".keystore",
    }:
        return True

    return False


def _redact_sensitive_lines(text: str) -> str:
    sensitive_keys = (
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "api-key",
        "private_key",
        "private-key",
    )
    output = []

    for line in text.splitlines():
        stripped = line.lstrip()
        lowered = stripped.casefold()

        separator = "=" if "=" in stripped else ":" if ":" in stripped else None
        if separator:
            key = stripped.split(separator, 1)[0].strip().casefold()
            if any(token in key for token in sensitive_keys):
                prefix = line[: len(line) - len(stripped)]
                output.append(f"{prefix}{stripped.split(separator, 1)[0]}{separator}<redacted>")
                continue

        if lowered.startswith("authorization:"):
            output.append("Authorization: <redacted>")
            continue

        output.append(line)

    return "\n".join(output)


def _decode_file_content(path: FilePath) -> str:
    if path.suffix.casefold() == ".gz":
        with gzip.open(path, "rb") as handle:
            data = handle.read(MAX_FILE_READ_BYTES + 1)
    else:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_READ_BYTES + 1)

    if len(data) > MAX_FILE_READ_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file exceeds {MAX_FILE_READ_BYTES} readable bytes",
        )

    if b"\x00" in data[:8192]:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="binary file cannot be returned as text",
        )

    return data.decode("utf-8", errors="replace")


async def _action_result(action: str, server: ManagedServer | None = None) -> dict:
    target = server or server_registry.default()

    try:
        result = await getattr(target.manager, action)()
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Control API action %s failed for server %s",
            action,
            target.server_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    await auto_stop_manager.on_server_action(target, action, result)
    payload = await _status_payload(target)
    payload["result"] = result
    return payload


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "servers": len(server_registry.list()),
    }


# ===== Multi-server read-only API =====


@app.get(
    "/v1/minecraft/servers",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_list_servers",
    summary="List all configured Minecraft servers",
)
async def minecraft_list_servers() -> dict[str, list[dict]]:
    """Return every configured server and its current process state."""
    servers = server_registry.list()
    statuses = await asyncio.gather(
        *(_status_payload(server, include_players=False) for server in servers)
    )
    return {"servers": statuses}


@app.get(
    "/v1/minecraft/servers/{server_id}/status",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_status",
    summary="Get one Minecraft server status",
)
async def minecraft_status(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    """Return process state and player list for the selected server."""
    return await _status_payload(_get_server(server_id))


@app.get(
    "/v1/minecraft/servers/{server_id}/auto-stop",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_get_auto_stop",
    summary="Get event-driven empty-server auto-stop settings",
)
async def minecraft_get_auto_stop(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    server = _get_server(server_id)
    return {
        **_identity(server),
        **auto_stop_manager.status(server),
    }


@app.get(
    "/v1/minecraft/servers/{server_id}/logs",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_logs",
    summary="Read recent manager console output for one server",
)
async def minecraft_logs(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
    lines: int = Query(default=50, ge=1, le=100),
) -> dict:
    """Read the last 1-100 lines captured from the selected managed process."""
    server = _get_server(server_id)
    content = await server.manager.tail_output(lines)
    return {
        **_identity(server),
        "lines": lines,
        "content": content,
    }


@app.get(
    "/v1/minecraft/servers/{server_id}/files",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_list_files",
    summary="Browse Minecraft server files such as crash-reports, logs, mods and config",
    description=(
        "Use this for filesystem questions about a configured Minecraft server. "
        "Prefer this over terminal/workspace file search when the user asks what is "
        "inside crash-reports, logs, mods, config, world, or another server directory."
    ),
)
async def minecraft_list_files(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
    path: str = Query(
        default=".",
        description=(
            "Relative directory inside the server root, for example '.', "
            "'logs', 'crash-reports', 'mods' or 'config'."
        ),
    ),
    recursive: bool = Query(
        default=False,
        description="Recursively list descendants below the selected directory.",
    ),
    max_entries: int = Query(default=200, ge=1, le=MAX_FILE_LIST_ENTRIES),
) -> dict:
    """Browse the configured Minecraft server folder without exposing host paths."""
    server = _get_server(server_id)
    root, target = _safe_server_path(server, path)

    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="path does not exist",
        )
    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path is not a directory",
        )

    iterator = target.rglob("*") if recursive else target.iterdir()
    entries = []

    for entry in iterator:
        if len(entries) >= max_entries:
            break

        try:
            resolved = entry.resolve()
            if resolved != root and root not in resolved.parents:
                continue
            stat_result = resolved.stat()
        except (FileNotFoundError, OSError):
            continue

        entries.append(
            {
                "path": _display_relative_path(root, resolved),
                "name": resolved.name,
                "type": "directory" if resolved.is_dir() else "file",
                "size_bytes": stat_result.st_size if resolved.is_file() else None,
                "modified_at": datetime.fromtimestamp(
                    stat_result.st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
            }
        )

    entries.sort(
        key=lambda item: (
            item["type"] != "directory",
            item["path"].casefold(),
        )
    )

    return {
        **_identity(server),
        "path": _display_relative_path(root, target),
        "recursive": recursive,
        "truncated": len(entries) >= max_entries,
        "entries": entries,
    }


@app.get(
    "/v1/minecraft/servers/{server_id}/files/read",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_read_file",
    summary="Read Minecraft crash reports and logs, including .log.gz",
    description=(
        "Read a file that belongs to the configured Minecraft server. Use this after "
        "minecraft_list_files when inspecting crash-reports or logs. Gzip text such as "
        "rotated .log.gz files is decompressed automatically; do not ask the user to "
        "upload or unpack a server file that is available here."
    ),
)
async def minecraft_read_file(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
    path: str = Query(
        description=(
            "Relative file path inside the server root, for example "
            "'crash-reports/crash-2026-09-27_20.10.00-server.txt' or "
            "'logs/2026-09-27-3.log.gz'. Gzip text is decompressed automatically."
        ),
    ),
    max_chars: int = Query(default=200_000, ge=1_000, le=MAX_FILE_READ_CHARS),
) -> dict:
    """Read diagnostic server files directly, with traversal and secret guards."""
    server = _get_server(server_id)
    root, target = _safe_server_path(server, path)

    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="file does not exist",
        )
    if not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path is not a file",
        )
    if _looks_sensitive(target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="sensitive file type is not readable through the Control API",
        )

    try:
        content = await asyncio.to_thread(_decode_file_content, target)
    except (OSError, gzip.BadGzipFile) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"failed to read file: {exc}",
        ) from exc

    content = _redact_sensitive_lines(content)
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars]

    return {
        **_identity(server),
        "path": _display_relative_path(root, target),
        "gzip_decompressed": target.suffix.casefold() == ".gz",
        "truncated": truncated,
        "content": content,
    }


# ===== Multi-server write API =====


@app.post(
    "/v1/minecraft/servers/{server_id}/start",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_start",
    summary="Start one Minecraft server",
)
async def minecraft_start(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    return await _action_result("start", _get_server(server_id))


@app.post(
    "/v1/minecraft/servers/{server_id}/stop",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_stop",
    summary="Stop one Minecraft server",
)
async def minecraft_stop(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    return await _action_result("stop", _get_server(server_id))


@app.post(
    "/v1/minecraft/servers/{server_id}/restart",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_restart",
    summary="Restart one Minecraft server",
)
async def minecraft_restart(
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    return await _action_result("restart", _get_server(server_id))


@app.post(
    "/v1/minecraft/servers/{server_id}/auto-stop",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_set_auto_stop",
    summary="Set event-driven empty-server auto-stop timeout",
)
async def minecraft_set_auto_stop(
    request: AutoStopRequest,
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    server = _get_server(server_id)
    state = await auto_stop_manager.set_timeout(server, request.timeout_seconds)
    return {
        **_identity(server),
        **state,
    }


@app.post(
    "/v1/minecraft/servers/{server_id}/rcon",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_rcon",
    summary="Run one raw RCON command on a Minecraft server",
)
async def minecraft_rcon(
    request: RconCommandRequest,
    server_id: str = Path(description="Stable server id returned by minecraft_list_servers"),
) -> dict:
    server = _get_server(server_id)
    command = request.command.strip()

    if not command or any(char in command for char in ("\n", "\r", "\x00")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RCON command must be one non-empty line",
        )

    if command.startswith("/"):
        command = command[1:].lstrip()

    if not command:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="RCON command must not be empty",
        )

    if not server.rcon_configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"RCON is not configured for server {server.server_id!r}",
        )

    response = await server.rcon(command)
    return {
        **_identity(server),
        "command": command,
        "response": response,
    }


# ===== Legacy single-server aliases =====
# These keep existing clients working while Open WebUI moves to the multi-server tools.


@app.get(
    "/v1/minecraft/status",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_status_default",
    summary="Get default Minecraft server status (legacy)",
    deprecated=True,
)
async def minecraft_status_default() -> dict:
    return await _status_payload(server_registry.default())


@app.get(
    "/v1/minecraft/logs",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_logs_default",
    summary="Read default Minecraft server logs (legacy)",
    deprecated=True,
)
async def minecraft_logs_default(
    lines: int = Query(default=50, ge=1, le=100),
) -> dict:
    server = server_registry.default()
    content = await server.manager.tail_output(lines)
    return {
        **_identity(server),
        "lines": lines,
        "content": content,
    }


@app.post(
    "/v1/minecraft/start",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_start_default",
    summary="Start default Minecraft server (legacy)",
    deprecated=True,
)
async def minecraft_start_default() -> dict:
    return await _action_result("start")


@app.post(
    "/v1/minecraft/stop",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_stop_default",
    summary="Stop default Minecraft server (legacy)",
    deprecated=True,
)
async def minecraft_stop_default() -> dict:
    return await _action_result("stop")


@app.post(
    "/v1/minecraft/restart",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_restart_default",
    summary="Restart default Minecraft server (legacy)",
    deprecated=True,
)
async def minecraft_restart_default() -> dict:
    return await _action_result("restart")


async def run_control_api() -> None:
    """Run the shared multi-server API in the Telegram manager process."""
    config = uvicorn.Config(
        app,
        host=CONTROL_API_HOST,
        port=CONTROL_API_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # aiogram owns process-level signal handling.
    server.install_signal_handlers = lambda: None

    logger.info(
        "External Control API starting on http://%s:%s (%s servers)",
        CONTROL_API_HOST,
        CONTROL_API_PORT,
        len(server_registry.list()),
    )
    await server.serve()
