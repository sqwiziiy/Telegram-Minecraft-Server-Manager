import asyncio
import html
import logging
import os
import shutil
from datetime import datetime

from services.server_registry import ManagedServer, server_registry
from services.backup_retention import AUTO_BACKUP_PREFIX, prune_auto_backups

logger = logging.getLogger(__name__)
_backup_locks: dict[str, asyncio.Lock] = {}


async def _prepare_live_backup(server: ManagedServer) -> tuple[bool, str | None]:
    """Pause saves and flush the world when a reachable server is running."""
    managed_status = await server.manager.status()
    save_off_result = await server.rcon("save-off")

    if save_off_result.startswith("❌"):
        if managed_status.running:
            return False, (
                "❌ Сервер работает, но RCON недоступен. "
                "Бэкап отменён, чтобы не архивировать мир во время записи."
            )
        return False, None

    flush_result = await server.rcon("save-all flush")
    if flush_result.startswith("❌"):
        await server.rcon("save-on")
        return False, (
            "❌ Не удалось выполнить <code>save-all flush</code>. "
            "Бэкап отменён."
        )

    return True, None


async def _create_backup_unlocked(server: ManagedServer | None = None, *, automatic: bool = False) -> str:
    """Create a consistent ZIP backup, coordinating with a live server through RCON."""
    server = server or server_registry.default()
    backup_dir = server.backup_dir
    world_dir = server.world_dir
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    prefix = AUTO_BACKUP_PREFIX if automatic else "world_backup_"
    archive_stem = os.path.join(backup_dir, f"{prefix}{timestamp}")
    world_parent = os.path.dirname(os.path.abspath(world_dir))
    world_name = os.path.basename(os.path.abspath(world_dir))

    saves_paused = False
    try:
        saves_paused, error = await _prepare_live_backup(server)
        if error:
            return error

        archive_path = await asyncio.to_thread(
            shutil.make_archive,
            archive_stem,
            "zip",
            root_dir=world_parent,
            base_dir=world_name,
        )
        size_mb = os.path.getsize(archive_path) / 1024 / 1024
        retention_text = ""
        if automatic:
            count_limit = getattr(server, "backup_retention_max_count", 0)
            size_limit = getattr(server, "backup_retention_max_gb", 0.0)
            if count_limit or size_limit:
                try:
                    retention = await asyncio.to_thread(
                        prune_auto_backups,
                        backup_dir,
                        archive_path,
                        max_count=count_limit,
                        max_gb=size_limit,
                    )
                    if retention.deleted_count:
                        retention_text = (
                            f"\n🗑 Удалено старых автобэкапов: <code>{retention.deleted_count}</code>"
                        )
                    if not retention.within_limits:
                        retention_text += "\n⚠️ Лимит хранения не достигнут; новый архив сохранён."
                except Exception:
                    logger.exception("Retention cleanup failed for %s", server.server_id)
                    retention_text = "\n⚠️ Не удалось очистить старые автобэкапы."
        logger.info("Backup created: %s (%.1f MB)", archive_path, size_mb)
        return (
            "✅ <b>Бэкап создан</b>\n"
            f"<code>{html.escape(archive_path)}</code>\n"
            f"Размер: <code>{size_mb:.1f} MB</code>{retention_text}"
        )
    except FileNotFoundError:
        return f"❌ Папка мира не найдена: <code>{html.escape(world_dir)}</code>"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backup failed")
        return f"❌ Ошибка создания бэкапа: <code>{html.escape(str(exc))}</code>"
    finally:
        if saves_paused:
            save_on_result = await server.rcon("save-on")
            if save_on_result.startswith("❌"):
                logger.error("Failed to re-enable Minecraft saves after backup: %s", save_on_result)


async def create_backup(server: ManagedServer | None = None, *, automatic: bool = False) -> str:
    """Serialize manual and scheduled backups of the same world."""
    server = server or server_registry.default()
    async with _backup_locks.setdefault(server.server_id, asyncio.Lock()):
        return await _create_backup_unlocked(server, automatic=automatic)
