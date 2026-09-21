import asyncio
import html
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import ADMIN_IDS, BOT_TOKEN, MINECRAFT_LOG_PATH
from handlers import console, mods, start, status, system
from keyboards.main_menu import get_main_menu
from middlewares.auth import AuthMiddleware
from services.access_control import access_control
from services.log_monitor import tail_log

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())

dp.include_router(console.router)
dp.include_router(start.router)
dp.include_router(status.router)
dp.include_router(mods.router)
dp.include_router(system.router)


async def _log_monitor_task() -> None:
    while True:
        try:
            async for line in tail_log(MINECRAFT_LOG_PATH):
                safe_line = html.escape(line)
                for user_id in access_control.users_with_permission("logs.view"):
                    try:
                        await bot.send_message(user_id, f"📋 <code>{safe_line}</code>")
                    except Exception as send_exc:  # noqa: BLE001
                        logger.warning("Failed to send log line to user %s: %s", user_id, send_exc)
        except FileNotFoundError:
            logger.warning("Minecraft log not found: %s; retrying in 15s", MINECRAFT_LOG_PATH)
            await asyncio.sleep(15)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Log monitor failed: %s", exc)
            await asyncio.sleep(10)


async def _on_startup() -> None:
    logger.info("Bot started. Full-access users: %s", ADMIN_IDS)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "✅ <b>Minecraft Server Manager запущен.</b>\n"
                "Управление сервером доступно из меню ниже.",
                reply_markup=get_main_menu(admin_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)


async def _on_shutdown() -> None:
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "⛔ Minecraft Server Manager остановлен.")
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    log_task = asyncio.create_task(_log_monitor_task(), name="log_monitor")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        log_task.cancel()
        try:
            await log_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
