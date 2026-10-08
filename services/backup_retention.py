"""Safe retention policy for archives produced by the automatic scheduler.

Manual backups (world_backup_*.zip) and arbitrary files are NEVER deleted.
Only auto archives with our exact generated filename are eligible.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

AUTO_BACKUP_PREFIX = "world_auto_backup_"
_AUTO_ARCHIVE_NAME = re.compile(r"^world_auto_backup_\\d{8}_\\d{6}_\\d{6}\\.zip$")


@dataclass(frozen=True)
class RetentionResult:
    deleted_count: int
    freed_bytes: int
    within_limits: bool


def prune_auto_backups(
    directory: str | Path,
    newest_archive: str | Path,
    *,
    max_count: int = 0,
    max_gb: float = 0,
) -> RetentionResult:
    """Prune oldest automatic ZIP archives until enabled limits are satisfied.

    The newly created backup is always preserved, even if it alone exceeds
    the size limit. Files without our strict auto-backup filename are ignored;
    symlinks are never followed or deleted. Called only after a successful
    scheduled backup, while holding the per-server backup lock.
    """
    if max_count <= 0 and max_gb <= 0:
        return RetentionResult(0, 0, True)

    root = Path(directory)
    protected = Path(newest_archive)
    candidates: list[tuple[Path, int]] = []
    for path in root.iterdir():
        if not _AUTO_ARCHIVE_NAME.fullmatch(path.name) or path.is_symlink():
            continue
        if not path.is_file():
            continue
        candidates.append((path, path.stat().st_size))

    if not any(path == protected for path, _ in candidates):
        # Never delete archives if we cannot identify a valid new backup.
        raise RuntimeError("Fresh automatic backup is missing from retention inventory")

    # Generated filename embeds the timestamp; lexical order is chronological.
    candidates.sort(key=lambda item: item[0].name)
    total_bytes = sum(size for _, size in candidates)
    max_bytes = max_gb * (1024 ** 3) if max_gb > 0 else 0

    def over_limit() -> bool:
        return (max_count > 0 and len(candidates) > max_count) or (
            max_gb > 0 and total_bytes > max_bytes
        )

    deleted = 0
    freed = 0
    while over_limit():
        oldest = next(((path, size) for path, size in candidates if path != protected), None)
        if oldest is None:
            break  # Only the fresh archive remains, even if it exceeds max_gb.
        path, size = oldest
        try:
            path.unlink()
        except OSError:
            logger.exception("Unable to delete old automatic backup %s", path)
            break  # Do not skip older undeletable files to delete newer ones.
        candidates.remove(oldest)
        total_bytes -= size
        deleted += 1
        freed += size

    if not over_limit():
        logger.info("Auto-backup retention in %s: removed %d archive(s)", root, deleted)
    else:
        logger.warning("Automatic backup retention still exceeds limits in %s", root)
    return RetentionResult(deleted, freed, not over_limit())
