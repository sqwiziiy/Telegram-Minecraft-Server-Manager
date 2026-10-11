"""Read-only listing and inspection of Minecraft world ZIP backups."""
from __future__ import annotations

import os
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path

# New backups use six-digit microseconds. Old or manually copied archives
# may have different names; don't silently exclude valid ZIPs from the list.
_ARCHIVE_RE = re.compile(
    r"^world_(?P<kind>auto_backup|backup|pre_restore)_(?P<stamp>\\d{8}_\\d{6}_\\d{6})\\.zip$"
)
_LABELS = {
    "auto_backup": "Автоматический",
    "backup": "Ручной",
    "pre_restore": "Защитный",
}
_PREVIEW_LIMIT = 12


def _safe_zip_filename(filename: str) -> bool:
    return (
        isinstance(filename, str)
        and filename.lower().endswith(".zip")
        and not filename.startswith(".")
        and len(filename.encode("utf-8")) <= 255
        and not any(char in filename for char in ("/", "\\\\", "\\x00", "\\n", "\\r"))
        and all(ord(char) >= 32 for char in filename)
    )


def _archive_meta(file: Path) -> dict | None:
    if not _safe_zip_filename(file.name) or file.is_symlink() or not file.is_file():
        return None
    try:
        info = file.stat()
        match = _ARCHIVE_RE.fullmatch(file.name)
        if match:
            stamp = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S_%f")
            archive_type = _LABELS[match.group("kind")]
            known_format = True
        else:
            stamp = datetime.fromtimestamp(info.st_mtime)
            lower = file.name.lower()
            if lower.startswith("world_auto_backup_"):
                archive_type = "Автоматический (старый формат)"
            elif lower.startswith("world_pre_restore_"):
                archive_type = "Защитный (старый формат)"
            elif lower.startswith("world_backup_"):
                archive_type = "Ручной (старый формат)"
            else:
                archive_type = "Другой ZIP (требует проверки)"
            known_format = False
    except (OSError, ValueError, OverflowError):
        return None
    return {
        "name": file.name,
        "type": archive_type,
        "known_format": known_format,
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
    if not _safe_zip_filename(filename):
        raise ValueError("Unknown backup filename")
    folder = Path(backup_dir)
    item = folder / filename
    meta = _archive_meta(item)
    if meta is None:
        raise FileNotFoundError("Backup not found or unsafe file type")
    try:
        fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError("Backup is not a regular file")
            with zipfile.ZipFile(handle, "r") as archive:
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
