import asyncio
import html
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import ADMIN_IDS, BOT_TOKEN, JARVIS_API_ENABLED
from handlers import console, mods, start, status, system
from middlewares.auth import AuthMiddleware
from services.access_control import access_control
from services.auto_stop import auto_stop_manager
from services.event_feed import event_feed
from services.log_monitor import tail_log
from services.server_registry import server_registry

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


async def _monitor_server(server) -> None:
    while True:
        try:
            async for line in tail_log(server.minecraft_log_path):
                await auto_stop_manager.handle_line(server, line)
                await event_feed.publish_line(
                    bot,
                    server_id=server.server_id,
                    server_name=server.server_name,
                    line=line,
                    user_ids=access_control.users_with_server_permission(
                        server.server_id,
                        "logs.view",
                    ),
                )
        except FileNotFoundError:
            logger.warning("Minecraft log not found for %s: %s; retrying in 15s", server.server_id, server.minecraft_log_path)
            await asyncio.sleep(15)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Log monitor failed: %s", exc)
            await asyncio.sleep(10)


async def _log_monitor_task() -> None:
    await asyncio.gather(*(_monitor_server(server) for server in server_registry.list()))


async def _notify_auto_stop(server, timeout_seconds: int, result: str) -> None:
    if timeout_seconds % 60 == 0:
        duration = f"{timeout_seconds // 60} мин"
    else:
        duration = f"{timeout_seconds} сек"

    text = (
        f"🌙 <b>{html.escape(server.server_name)} автоматически выключен.</b>\n"
        f"Причина: сервер был пуст {duration}.\n"
        f"Результат: <code>{html.escape(result)}</code>"
    )
    for user_id in access_control.users_with_server_permission(
        server.server_id,
        "server.stop",
    ):
        try:
            await bot.send_message(user_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to send auto-stop notification to %s for %s: %s",
                user_id,
                server.server_id,
                exc,
            )


async def _on_startup() -> None:
    logger.info("Bot started. Full-access users: %s", ADMIN_IDS)
    auto_stop_manager.set_notifier(_notify_auto_stop)
    await auto_stop_manager.bootstrap(server_registry.list())
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                "✅ <b>Minecraft Server Manager запущен.</b>\n"
                "Выберите сервер через /start.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to notify admin %s: %s", admin_id, exc)


async def _on_shutdown() -> None:
    await auto_stop_manager.shutdown()
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, "⛔ Minecraft Server Manager остановлен.")
        except Exception:  # noqa: BLE001
            pass


async def main() -> None:
    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    tasks = [
        asyncio.create_task(_log_monitor_task(), name="log_monitor"),
    ]

    if JARVIS_API_ENABLED:
        from services.control_api import run_control_api

        tasks.append(
            asyncio.create_task(run_control_api(), name="jarvis_control_api")
        )
    else:
        logger.info("Jarvis control API is disabled")

    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Background task %s stopped with error: %s",
                    task.get_name(),
                    exc,
                )
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
