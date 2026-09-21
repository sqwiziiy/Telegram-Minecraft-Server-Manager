import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def _telegram_ids(name: str) -> list[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []

    result: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) <= 0:
            raise RuntimeError(f"{name} contains an invalid Telegram user id: {item!r}")
        result.append(int(item))
    return sorted(set(result))


BOT_TOKEN: str = _required("BOT_TOKEN")

# OWNER_IDS is the v1.1+ owner configuration. ADMIN_IDS stays supported as a
# backwards-compatible full-access list during migration from v1.0.
LEGACY_ADMIN_IDS: list[int] = _telegram_ids("ADMIN_IDS")
OWNER_IDS: list[int] = _telegram_ids("OWNER_IDS")
if not OWNER_IDS:
    OWNER_IDS = LEGACY_ADMIN_IDS.copy()
if not OWNER_IDS:
    raise RuntimeError("OWNER_IDS (or legacy ADMIN_IDS) must contain at least one Telegram user id")

# Compatibility alias for code that has not yet moved to AccessControl.
ADMIN_IDS: list[int] = sorted(set(OWNER_IDS) | set(LEGACY_ADMIN_IDS))
ACCESS_USERS_FILE: str = os.getenv(
    "ACCESS_USERS_FILE",
    str(PROJECT_DIR / "users.json"),
)

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
