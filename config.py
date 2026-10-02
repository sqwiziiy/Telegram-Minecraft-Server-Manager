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


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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

_access_users_path = Path(os.getenv("ACCESS_USERS_FILE", "users.json")).expanduser()
if not _access_users_path.is_absolute():
    _access_users_path = PROJECT_DIR / _access_users_path
ACCESS_USERS_FILE: str = str(_access_users_path.resolve())

_auto_stop_state_path = Path(os.getenv("AUTO_STOP_STATE_FILE", "auto_stop_state.json")).expanduser()
if not _auto_stop_state_path.is_absolute():
    _auto_stop_state_path = PROJECT_DIR / _auto_stop_state_path
AUTO_STOP_STATE_FILE: str = str(_auto_stop_state_path.resolve())
AUTO_STOP_DEFAULT_SECONDS: int = int(os.getenv("AUTO_STOP_DEFAULT_SECONDS", "0"))
if not 0 <= AUTO_STOP_DEFAULT_SECONDS <= 86400:
    raise RuntimeError("AUTO_STOP_DEFAULT_SECONDS must be between 0 and 86400")

RCON_HOST: str = os.getenv("RCON_HOST", "127.0.0.1")
RCON_PORT: int = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD: str = os.getenv("RCON_PASSWORD", "")
if not 1 <= RCON_PORT <= 65535:
    raise RuntimeError("RCON_PORT must be between 1 and 65535")

SERVER_DIR: str = os.getenv("SERVER_DIR", "/opt/minecraft")

# Stable identity exposed by the Jarvis API. This becomes important when
# multiple manager instances control different Minecraft servers.
SERVER_ID: str = os.getenv("SERVER_ID", "minecraft").strip() or "minecraft"
SERVER_NAME: str = os.getenv("SERVER_NAME", "Minecraft Server").strip() or "Minecraft Server"

_servers_file = Path(os.getenv("MINECRAFT_SERVERS_FILE", "servers.json")).expanduser()
if not _servers_file.is_absolute():
    _servers_file = PROJECT_DIR / _servers_file
MINECRAFT_SERVERS_FILE: str = str(_servers_file.resolve())

DEFAULT_SERVER_ID: str = os.getenv("DEFAULT_SERVER_ID", SERVER_ID).strip() or SERVER_ID
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

# ===== Optional external Control API =====
# CONTROL_API_* is the generic v1.1+ naming. Existing JARVIS_API_* settings are
# still accepted so current Jarvis/Open WebUI installations keep working.
def _api_env(primary: str, legacy: str, default: str = "") -> str:
    value = os.getenv(primary)
    if value is not None:
        return value.strip()
    return os.getenv(legacy, default).strip()


def _api_bool_env(primary: str, legacy: str, default: bool = False) -> bool:
    value = os.getenv(primary)
    if value is None:
        value = os.getenv(legacy)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


CONTROL_API_ENABLED: bool = _api_bool_env(
    "CONTROL_API_ENABLED", "JARVIS_API_ENABLED", False
)
CONTROL_API_HOST: str = (
    _api_env("CONTROL_API_HOST", "JARVIS_API_HOST", "127.0.0.1")
    or "127.0.0.1"
)
CONTROL_API_PORT: int = int(
    _api_env("CONTROL_API_PORT", "JARVIS_API_PORT", "8765")
)
CONTROL_API_TOKEN: str = _api_env("CONTROL_API_TOKEN", "JARVIS_API_TOKEN")

if not 1 <= CONTROL_API_PORT <= 65535:
    raise RuntimeError("CONTROL_API_PORT must be between 1 and 65535")

if CONTROL_API_ENABLED and len(CONTROL_API_TOKEN) < 32:
    raise RuntimeError(
        "CONTROL_API_TOKEN must be at least 32 characters when CONTROL_API_ENABLED=true"
    )

# Backwards-compatible Python aliases for integrations or local code that still
# imports the old names.
JARVIS_API_ENABLED = CONTROL_API_ENABLED
JARVIS_API_HOST = CONTROL_API_HOST
JARVIS_API_PORT = CONTROL_API_PORT
JARVIS_API_TOKEN = CONTROL_API_TOKEN
