import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


BOT_TOKEN: str = _required("BOT_TOKEN")

_raw_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(i.strip()) for i in _raw_ids.split(",") if i.strip().isdigit()]
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS must contain at least one Telegram user id")

RCON_HOST: str = os.getenv("RCON_HOST", "127.0.0.1")
RCON_PORT: int = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD: str = os.getenv("RCON_PASSWORD", "")

SERVER_DIR: str = os.getenv("SERVER_DIR", "/opt/minecraft")
SERVER_START_COMMAND: str = os.getenv("SERVER_START_COMMAND", "./start.sh").strip()
SERVER_PID_FILE: str = os.getenv(
    "SERVER_PID_FILE",
    str(Path(SERVER_DIR) / ".telegram-mc-manager.pid"),
)
SERVER_OUTPUT_LOG: str = os.getenv(
    "SERVER_OUTPUT_LOG",
    str(Path(SERVER_DIR) / "logs" / "manager-console.log"),
)
SERVER_STOP_TIMEOUT: float = max(5.0, float(os.getenv("SERVER_STOP_TIMEOUT", "45")))

MINECRAFT_LOG_PATH: str = os.getenv(
    "MINECRAFT_LOG_PATH",
    str(Path(SERVER_DIR) / "logs" / "latest.log"),
)
MODS_DIR: str = os.getenv("MODS_DIR", str(Path(SERVER_DIR) / "mods"))
WORLD_DIR: str = os.getenv("WORLD_DIR", str(Path(SERVER_DIR) / "world"))
BACKUP_DIR: str = os.getenv("BACKUP_DIR", str(Path(SERVER_DIR) / "backups"))
MAX_MOD_UPLOAD_MB: int = max(1, int(os.getenv("MAX_MOD_UPLOAD_MB", "100")))

# ===== Optional Jarvis / Open WebUI control API =====
JARVIS_API_ENABLED: bool = _bool_env("JARVIS_API_ENABLED", False)
JARVIS_API_HOST: str = os.getenv("JARVIS_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
JARVIS_API_PORT: int = int(os.getenv("JARVIS_API_PORT", "8765"))
JARVIS_API_TOKEN: str = os.getenv("JARVIS_API_TOKEN", "").strip()

if not 1 <= JARVIS_API_PORT <= 65535:
    raise RuntimeError("JARVIS_API_PORT must be between 1 and 65535")

if JARVIS_API_ENABLED and len(JARVIS_API_TOKEN) < 32:
    raise RuntimeError(
        "JARVIS_API_TOKEN must be at least 32 characters when JARVIS_API_ENABLED=true"
    )
