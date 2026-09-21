import asyncio
import html
import logging

import psutil
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from keyboards.inline import server_action_confirm_keyboard, system_keyboard
from middlewares.auth import deny_access, require_permission
from services.access_control import access_control
from services.backup import create_backup
from services.server_process import server_process_manager

router = Router()
logger = logging.getLogger(__name__)

_SYSTEM_PANEL_PERMISSIONS = (
    "system.view",
    "server.status",
    "server.start",
    "server.stop",
    "server.restart",
    "logs.view",
    "backup.create",
)


def _can_open_system_panel(user_id: int) -> bool:
    return any(access_control.can(user_id, permission) for permission in _SYSTEM_PANEL_PERMISSIONS)


def _collect_sys_info() -> dict:
    cpu = psutil.cpu_percent(interval=0.3)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {"cpu": cpu, "ram": ram, "disk": disk}


def _format_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    parts.append(f"{minutes}м")
    return " ".join(parts)


async def _system_text(user_id: int) -> str:
    server = await server_process_manager.status()
    can_view_status = access_control.can(user_id, "server.status")

    if server.running:
        if can_view_status:
            server_line = (
                f"🟢 <b>Сервер:</b> работает · PID <code>{server.pid}</code> · "
                f"{_format_uptime(server.uptime_seconds)} · {server.memory_mb:.0f} MB"
            )
        else:
            server_line = "🟢 <b>Сервер:</b> работает"
    else:
        server_line = "🔴 <b>Сервер:</b> остановлен"

    text = "⚙️ <b>Панель сервера</b>\n\n" + server_line

    if access_control.can(user_id, "system.view"):
        info = await asyncio.to_thread(_collect_sys_info)
        ram = info["ram"]
        disk = info["disk"]
        text += (
            "\n\n"
            f"🖥 CPU: <code>{info['cpu']:.1f}%</code>\n"
            f"💾 RAM: <code>{ram.used / 1024**3:.1f}/{ram.total / 1024**3:.1f} ГБ ({ram.percent}%)</code>\n"
            f"💿 Диск: <code>{disk.used / 1024**3:.1f}/{disk.total / 1024**3:.1f} ГБ ({disk.percent}%)</code>"
        )

    return text


async def _send_system_panel(message: Message) -> None:
    if not _can_open_system_panel(message.from_user.id):
        await deny_access(message, "⛔ У вас нет доступа к панели сервера.")
        return

    await message.answer(
        await _system_text(message.from_user.id),
        parse_mode="HTML",
        reply_markup=system_keyboard(message.from_user.id),
    )


@router.message(F.text == "⚙️ Система")
async def system_menu(message: Message) -> None:
    await _send_system_panel(message)


@router.message(Command("sys"))
async def cmd_sys(message: Message) -> None:
    await _send_system_panel(message)


@router.callback_query(F.data == "refresh_system")
async def callback_refresh_system(callback: CallbackQuery) -> None:
    if not _can_open_system_panel(callback.from_user.id):
        await deny_access(callback, "⛔ У вас нет доступа к панели сервера.")
        return

    await callback.answer()
    await callback.message.edit_text(
        await _system_text(callback.from_user.id),
        parse_mode="HTML",
        reply_markup=system_keyboard(callback.from_user.id),
    )


@router.callback_query(F.data == "server_logs")
async def callback_server_logs(callback: CallbackQuery) -> None:
    if not await require_permission(callback, "logs.view"):
        return

    await callback.answer()
    output = await server_process_manager.tail_output(30)
    if not output:
        output = "(лог запуска пока пуст)"
    await callback.message.answer(
        "📜 <b>Последние строки manager-console.log</b>\n\n"
        f"<pre>{html.escape(output[-3500:])}</pre>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "create_backup")
async def callback_create_backup(callback: CallbackQuery) -> None:
    if not await require_permission(callback, "backup.create"):
        return

    await callback.answer()
    status = await callback.message.answer("⏳ Создаю бэкап мира…")
    result = await create_backup()
    await status.edit_text(result, parse_mode="HTML")


_CONFIRM_TEXTS = {
    "confirm_start": ("▶️", "запустить", "start", "server.start"),
    "confirm_stop": ("⏹", "остановить", "stop", "server.stop"),
    "confirm_restart": ("🔁", "перезапустить", "restart", "server.restart"),
}


@router.callback_query(F.data.in_(set(_CONFIRM_TEXTS)))
async def callback_confirm_action(callback: CallbackQuery) -> None:
    icon, verb, action, permission = _CONFIRM_TEXTS[callback.data]
    if not await require_permission(callback, permission):
        return

    warning = (
        "\nMinecraft получит команду <code>stop</code> через RCON перед остановкой."
        if action in {"stop", "restart"}
        else ""
    )
    await callback.message.answer(
        f"{icon} <b>Точно {verb} сервер?</b>{warning}",
        parse_mode="HTML",
        reply_markup=server_action_confirm_keyboard(action),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"cancel_start", "cancel_stop", "cancel_restart"}))
async def callback_cancel_action(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()


_ACTIONS = {
    "start_server": ("▶️", "start", "server.start"),
    "stop_server": ("⏹", "stop", "server.stop"),
    "restart_server": ("🔁", "restart", "server.restart"),
}


@router.callback_query(F.data.in_(set(_ACTIONS)))
async def callback_execute_action(callback: CallbackQuery) -> None:
    icon, action, permission = _ACTIONS[callback.data]
    if not await require_permission(callback, permission):
        return

    await callback.answer()
    await callback.message.edit_text(f"{icon} Выполняю…")

    try:
        result = await getattr(server_process_manager, action)()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Server action %s failed", action)
        await callback.message.edit_text(
            f"❌ <b>Не удалось выполнить {action}</b>\n<code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )
        return

    messages = {
        "started": "✅ Сервер запущен.",
        "already_running": "ℹ️ Сервер уже запущен.",
        "stopped": "✅ Сервер корректно остановлен.",
        "already_stopped": "ℹ️ Сервер уже остановлен.",
        "killed": "⚠️ Сервер не завершился вовремя и был принудительно остановлен.",
    }
    logger.info("Server action %s by Telegram user %s: %s", action, callback.from_user.id, result)
    await callback.message.edit_text(messages.get(result, f"✅ Готово: {html.escape(result)}"))
