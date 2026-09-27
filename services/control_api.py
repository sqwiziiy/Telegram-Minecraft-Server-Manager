import asyncio
import logging
import secrets
from dataclasses import asdict

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from config import JARVIS_API_HOST, JARVIS_API_PORT, JARVIS_API_TOKEN
from services.server_registry import ManagedServer, server_registry

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Minecraft Server Manager Control API",
    version="2.0.0",
    description=(
        "Multi-server control API for Jarvis/Open WebUI. "
        "Read-only discovery/status/log operations and explicit write operations "
        "are exposed as separate OpenAPI functions."
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


def _require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, JARVIS_API_TOKEN)
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
    }

    if include_players and process.running and server.rcon_configured:
        result["players"] = await server.rcon("list")
    else:
        result["players"] = None

    return result


async def _action_result(action: str, server: ManagedServer | None = None) -> dict:
    target = server or server_registry.default()

    try:
        result = await getattr(target.manager, action)()
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Jarvis API action %s failed for server %s",
            action,
            target.server_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

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
        host=JARVIS_API_HOST,
        port=JARVIS_API_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # aiogram owns process-level signal handling.
    server.install_signal_handlers = lambda: None

    logger.info(
        "Jarvis multi-server API starting on http://%s:%s (%s servers)",
        JARVIS_API_HOST,
        JARVIS_API_PORT,
        len(server_registry.list()),
    )
    await server.serve()
