import asyncio
import html
import logging
import os
import shutil
from datetime import datetime

from config import BACKUP_DIR, WORLD_DIR
from services.rcon import send_rcon_command
from services.server_process import server_process_manager

logger = logging.getLogger(__name__)


async def _prepare_live_backup() -> tuple[bool, str | None]:
    """Pause saves and flush the world when a reachable server is running."""
    managed_status = await server_process_manager.status()
    save_off_result = await send_rcon_command("save-off")

    if save_off_result.startswith("❌"):
        if managed_status.running:
            return False, (
                "❌ Сервер работает, но RCON недоступен. "
                "Бэкап отменён, чтобы не архивировать мир во время записи."
            )
        return False, None

    flush_result = await send_rcon_command("save-all flush")
    if flush_result.startswith("❌"):
        await send_rcon_command("save-on")
        return False, (
            "❌ Не удалось выполнить <code>save-all flush</code>. "
            "Бэкап отменён."
        )

    return True, None


async def create_backup() -> str:
    """Create a consistent ZIP backup, coordinating with a live server through RCON."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_stem = os.path.join(BACKUP_DIR, f"world_backup_{timestamp}")
    world_parent = os.path.dirname(os.path.abspath(WORLD_DIR))
    world_name = os.path.basename(os.path.abspath(WORLD_DIR))

    saves_paused = False
    try:
        saves_paused, error = await _prepare_live_backup()
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
        logger.info("Backup created: %s (%.1f MB)", archive_path, size_mb)
        return (
            "✅ <b>Бэкап создан</b>\n"
            f"<code>{html.escape(archive_path)}</code>\n"
            f"Размер: <code>{size_mb:.1f} MB</code>"
        )
    except FileNotFoundError:
        return f"❌ Папка мира не найдена: <code>{html.escape(WORLD_DIR)}</code>"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backup failed")
        return f"❌ Ошибка создания бэкапа: <code>{html.escape(str(exc))}</code>"
    finally:
        if saves_paused:
            save_on_result = await send_rcon_command("save-on")
            if save_on_result.startswith("❌"):
                logger.error("Failed to re-enable Minecraft saves after backup: %s", save_on_result)
