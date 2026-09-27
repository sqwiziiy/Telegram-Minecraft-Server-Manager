import asyncio
import html
import logging

import psutil
from aiogram import Router
from aiogram.types import CallbackQuery

from handlers.start import _home_text, _server_picker
from keyboards.inline import confirm_keyboard, server_home_keyboard
from middlewares.auth import deny_access
from services.access_control import access_control
from services.backup import create_backup
from services.telegram_context import resolve_server

router = Router()
logger = logging.getLogger(__name__)

_ACTION_LABELS = {
    "start": ("▶️", "запустить", "server.start"),
    "stop": ("⏹", "остановить", "server.stop"),
    "restart": ("🔁", "перезапустить", "server.restart"),
}


def _action_parts(data: str, prefix: str) -> tuple[str, str] | None:
    if not data.startswith(prefix):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[1] not in _ACTION_LABELS or not parts[2]:
        return None
    return parts[1], parts[2]


@router.callback_query(lambda c: c.data and c.data.startswith("host"))
async def host_info(callback: CallbackQuery) -> None:
    if callback.data != "host" or not access_control.can_system(callback.from_user.id):
        await deny_access(callback)
        return
    info = await asyncio.to_thread(_collect_host_info)
    ram = info["ram"]
    disk = info["disk"]
    text = (
        "🖥 <b>Хост</b>\n\n"
        f"CPU: <code>{info['cpu']:.1f}%</code>\n"
        f"RAM: <code>{ram.used / 1024**3:.1f}/{ram.total / 1024**3:.1f} ГБ ({ram.percent}%)</code>\n"
        f"Диск: <code>{disk.used / 1024**3:.1f}/{disk.total / 1024**3:.1f} ГБ ({disk.percent}%)</code>"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Серверы", callback_data="servers")]]))
    await callback.answer()


def _collect_host_info() -> dict:
    return {"cpu": psutil.cpu_percent(interval=0.3), "ram": psutil.virtual_memory(), "disk": psutil.disk_usage("/")}


@router.callback_query(lambda c: c.data and c.data.startswith("confirm:"))
async def confirm_action(callback: CallbackQuery) -> None:
    parsed = _action_parts(callback.data, "confirm:")
    if parsed is None:
        await callback.answer("❌ Некорректное действие.", show_alert=True)
        return
    action, server_id = parsed
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    _, verb, permission = _ACTION_LABELS[action]
    if not access_control.can_server(callback.from_user.id, server_id, permission):
        await deny_access(callback)
        return
    warning = "\nMinecraft получит команду <code>stop</code> через RCON перед остановкой." if action in {"stop", "restart"} else ""
    icon = _ACTION_LABELS[action][0]
    await callback.message.edit_text(f"{icon} <b>Точно {verb} {html.escape(server.server_name)}?</b>{warning}", parse_mode="HTML", reply_markup=confirm_keyboard(action, server_id))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("do:"))
async def execute_action(callback: CallbackQuery) -> None:
    parsed = _action_parts(callback.data, "do:")
    if parsed is None:
        await callback.answer("❌ Некорректное действие.", show_alert=True)
        return
    action, server_id = parsed
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, _ACTION_LABELS[action][2]):
        await deny_access(callback)
        return
    await callback.answer()
    await callback.message.edit_text(f"⏳ Выполняю для {html.escape(server.server_name)}…", parse_mode="HTML")
    try:
        result = await getattr(server.manager, action)()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Server action failed")
        await callback.message.edit_text(f"❌ <code>{html.escape(str(exc))}</code>", parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
        return
    messages = {"started": "✅ Сервер запущен.", "already_running": "ℹ️ Сервер уже запущен.", "stopped": "✅ Сервер остановлен.", "already_stopped": "ℹ️ Сервер уже остановлен.", "killed": "⚠️ Сервер принудительно остановлен."}
    await callback.message.edit_text(await _home_text(server), parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
    logger.info("Server action %s completed with %s", action, result)


@router.callback_query(lambda c: c.data and c.data.startswith("logs:"))
async def server_logs(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "logs.view"):
        await deny_access(callback)
        return
    output = await server.manager.tail_output(30) or "(лог запуска пока пуст)"
    await callback.message.answer(f"📜 <b>Логи · {html.escape(server.server_name)}</b>\n\n<pre>{html.escape(output[-3500:])}</pre>", parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("backup:"))
async def server_backup(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "backup.create"):
        await deny_access(callback)
        return
    await callback.answer()
    status = await callback.message.answer(f"⏳ Создаю бэкап мира · {html.escape(server.server_name)}…", parse_mode="HTML")
    await status.edit_text(await create_backup(server), parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
