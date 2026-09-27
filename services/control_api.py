import logging
import secrets
from dataclasses import asdict

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import JARVIS_API_HOST, JARVIS_API_PORT, JARVIS_API_TOKEN
from services.rcon import send_rcon_command
from services.server_process import server_process_manager

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Minecraft Server Manager Control API",
    version="1.0.0",
    description=(
        "Restricted control API for Jarvis/Open WebUI. "
        "Only explicit Minecraft server operations are exposed."
    ),
    docs_url=None,
    redoc_url=None,
)

_bearer = HTTPBearer(auto_error=False)


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


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/v1/minecraft/status",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_status",
    summary="Get Minecraft server status",
)
async def minecraft_status() -> dict:
    """Return managed-process state and the current Minecraft player list when available."""
    process = await server_process_manager.status()
    result = asdict(process)

    if process.running:
        result["players"] = await send_rcon_command("list")
    else:
        result["players"] = None

    return result


@app.get(
    "/v1/minecraft/logs",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_logs",
    summary="Read recent Minecraft manager console output",
)
async def minecraft_logs(
    lines: int = Query(default=50, ge=1, le=100),
) -> dict[str, str | int]:
    """Read the last 1-100 lines captured from the managed Minecraft process."""
    content = await server_process_manager.tail_output(lines)
    return {"lines": lines, "content": content}


async def _action_result(action: str) -> dict:
    try:
        result = await getattr(server_process_manager, action)()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Jarvis API action %s failed", action)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    current = await server_process_manager.status()
    payload = asdict(current)
    payload["result"] = result
    return payload


@app.post(
    "/v1/minecraft/start",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_start",
    summary="Start the Minecraft server",
)
async def minecraft_start() -> dict:
    """Start the configured Minecraft server unless it is already running."""
    return await _action_result("start")


@app.post(
    "/v1/minecraft/stop",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_stop",
    summary="Stop the Minecraft server",
)
async def minecraft_stop() -> dict:
    """Gracefully stop Minecraft, falling back to process signals when required."""
    return await _action_result("stop")


@app.post(
    "/v1/minecraft/restart",
    dependencies=[Depends(_require_bearer)],
    operation_id="minecraft_restart",
    summary="Restart the Minecraft server",
)
async def minecraft_restart() -> dict:
    """Gracefully restart the configured Minecraft server."""
    return await _action_result("restart")


async def run_control_api() -> None:
    """Run the API in the bot process so Telegram and Jarvis share one process manager."""
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
        "Jarvis control API starting on http://%s:%s",
        JARVIS_API_HOST,
        JARVIS_API_PORT,
    )
    await server.serve()
