"""Private, centralized backups of configuration files edited by CLI wizards.

Config snapshots live under <bot project>/config_backups/, never beside
server.properties, users.json, servers.json, or .env. World ZIP backups are
unrelated and retain their existing locations.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

_SAFE_SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ALLOWED_FILES = {"users.json", "servers.json", ".env", "server.properties"}
_STATE_BACKUP_NAMES = {"auto_stop_state.json", "auto_backup_state.json"}


def save_config_backup(
    source_path: Path,
    previous_content: bytes,
    backup_dir: Path,
    *,
    server_id: str | None = None,
    backup_label: str | None = None,
) -> Path:
    """Write an exclusive 0600 backup from the validated pre-edit snapshot.

    A Minecraft server ID is part of the name for server.properties backups,
    preventing name clashes across different Minecraft installations.
    """
    if backup_label is not None:
        # Auto-task state files may use custom filenames in .env. Their
        # backup labels are fixed and validated to avoid arbitrary paths.
        if backup_label not in _STATE_BACKUP_NAMES or source_path.suffix != ".json":
            raise ValueError("Unsupported auto-task state backup label")
        prefix = backup_label
    elif source_path.name == "server.properties":
        if not server_id or not _SAFE_SERVER_ID.fullmatch(server_id):
            raise ValueError("server_id is required to back up server.properties")
        prefix = f"{server_id}-server.properties"
    elif source_path.name in _ALLOWED_FILES:
        prefix = source_path.name
    else:
        raise ValueError(f"Unsupported config backup source: {source_path.name}")

    if backup_dir.is_symlink():
        raise OSError(f"Config backup directory must not be a symlink: {backup_dir}")
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Backups can contain bot tokens, RCON passwords and other secrets.
    backup_dir.chmod(0o700)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = backup_dir / f"{prefix}.bak-{stamp}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as destination:
            destination.write(previous_content)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
