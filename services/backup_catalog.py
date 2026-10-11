"""Read-only listing and inspection of Minecraft world ZIP backups."""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

_ARCHIVE_RE = re.compile(
    r"^world_(?P<kind>auto_backup|backup|pre_restore)_(?P<stamp>\d{8}_\d{6}_\d{6})\.zip$"
)
_LABELS = {
    "auto_backup": "Автоматический",
    "backup": "Ручной",
    "pre_restore": "Защитный",
}
_PREVIEW_LIMIT = 12


def _archive_meta(file: Path) -> dict | None:
    match = _ARCHIVE_RE.fullmatch(file.name)
    if not match or file.is_symlink() or not file.is_file():
        return None
    try:
        stamp = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S_%f")
        info = file.stat()
    except (OSError, ValueError):
        return None
    return {
        "name": file.name,
        "type": _LABELS[match.group("kind")],
        "created": stamp.strftime("%d.%m.%Y %H:%M:%S"),
        "timestamp": stamp.strftime("%Y%m%d_%H%M%S_%f"),
        "size_bytes": info.st_size,
        "size_mb": round(info.st_size / (1024 * 1024), 1),
    }


def list_backups(backup_dir: str) -> list[dict]:
    root = Path(backup_dir)
    if not root.is_dir():
        return []
    entries = []
    for entry in root.iterdir():
        meta = _archive_meta(entry)
        if meta is not None:
            entries.append(meta)
    return sorted(entries, key=lambda item: (item["timestamp"], item["name"]), reverse=True)


def backup_info(backup_dir: str, filename: str) -> dict:
    if not isinstance(filename, str) or not _ARCHIVE_RE.fullmatch(filename):
        raise ValueError("Unknown backup filename")
    folder = Path(backup_dir)
    item = folder / filename
    meta = _archive_meta(item)
    if meta is None:
        raise FileNotFoundError("Backup not found or unsafe file type")
    try:
        with zipfile.ZipFile(item, "r") as archive:
            infos = archive.infolist()
            meta["file_count"] = sum(not entry.is_dir() for entry in infos)
            meta["uncompressed_bytes"] = sum(
                entry.file_size for entry in infos if not entry.is_dir()
            )
            meta["preview"] = [
                entry.filename[:180]
                for entry in infos if not entry.is_dir()
            ][:_PREVIEW_LIMIT]
            meta["valid"] = True
    except (zipfile.BadZipFile, OSError, ValueError):
        meta.update(valid=False, file_count=None, uncompressed_bytes=None, preview=[])
    return meta
