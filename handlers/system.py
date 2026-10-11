import asyncio
import html
import logging
from pathlib import Path
from datetime import datetime

import psutil
from aiogram import Router
from aiogram.types import CallbackQuery, FSInputFile

from config import HOST_DISK_PATH
from handlers.start import _home_text, _server_picker
from keyboards.inline import (
    auto_stop_keyboard,
    auto_tasks_keyboard,
    auto_backup_keyboard,
    back_to_server_keyboard,
    BACKUP_PAGE_SIZE,
    addon_token,
    backup_info_keyboard,
    backup_list_keyboard,
    backup_menu_keyboard,
    backup_result_keyboard,
    confirm_keyboard,
    events_keyboard,
    logs_keyboard,
    server_home_keyboard,
)
from middlewares.auth import deny_access
from services.access_control import access_control
from services.auto_stop import auto_stop_manager
from services.auto_backup import auto_backup_manager
from services.backup import create_backup
from services.backup_catalog import backup_info, list_backups
from services.event_history import event_history
from services.telegram_context import resolve_server, safe_edit_text

router = Router()
logger = logging.getLogger(__name__)

_ACTION_LABELS = {
    "start": ("▶️", "запустить", "server.start"),
    "stop": ("⏹", "остановить", "server.stop"),
    "restart": ("🔁", "перезапустить", "server.restart"),
}
_ACTION_RESULT_MESSAGES = {
    "started": "✅ Сервер запущен.",
    "already_running": "ℹ️ Сервер уже запущен.",
    "stopped": "✅ Сервер остановлен.",
    "already_stopped": "ℹ️ Сервер уже остановлен.",
    "killed": "⚠️ Сервер не завершился вовремя и был принудительно остановлен.",
}


def action_result_message(result: str) -> str:
    return _ACTION_RESULT_MESSAGES.get(result, f"✅ Готово: {html.escape(result)}")


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
        f"Диск <code>{html.escape(info['disk_path'])}</code>: "
        f"<code>{disk.used / 1024**3:.1f}/{disk.total / 1024**3:.1f} ГБ ({disk.percent}%)</code>"
    )
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Серверы", callback_data="servers")]]))
    await callback.answer()


def _collect_host_info() -> dict:
    configured = Path(HOST_DISK_PATH).expanduser()
    disk_path = configured if configured.exists() else Path("/")
    return {
        "cpu": psutil.cpu_percent(interval=0.3),
        "ram": psutil.virtual_memory(),
        "disk": psutil.disk_usage(str(disk_path)),
        "disk_path": str(disk_path),
    }


def _telegram_actor(user) -> str:
    label = user.full_name or str(user.id)
    if user.username:
        label += f" (@{user.username})"
    return f"{label}, id={user.id}"


async def _events_text(server) -> str:
    try:
        process = await server.manager.status()
        if process.running:
            state = f"🟢 Работает · аптайм {process.uptime_seconds // 60} мин"
        else:
            state = "⚫ Остановлен"
    except Exception as exc:  # noqa: BLE001
        state = f"❔ Связь недоступна: {html.escape(str(exc)[:200])}"

    entries = await event_history.recent(server, limit=20)
    history = event_history.render(entries)
    return (
        f"📋 <b>События · {html.escape(server.server_name)}</b>\n"
        f"{state}\n"
        f"🗂 <code>logs/events/YYYY-MM-DD.log</code>\n\n"
        f"{history}\n\n"
        f"<i>Последние {len(entries)} событий. История хранится на диске по дням.</i>"
    )


def _duration_text(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} сек"


def _auto_stop_text(server) -> str:
    state = auto_stop_manager.status(server)
    if not state["enabled"]:
        status_text = "🚫 Выключен"
    else:
        status_text = f"✅ После {_duration_text(state['timeout_seconds'])} без игроков"

    if state["pending"]:
        countdown = (
            f"\n⏳ До проверки: <code>{state['remaining_seconds']} сек</code>"
        )
    else:
        countdown = "\n⏸ Сейчас отсчёт не активен."

    return (
        f"⏱ <b>Auto-stop · {html.escape(server.server_name)}</b>\n\n"
        f"{status_text}{countdown}\n\n"
        "Работает по событиям входа/выхода. Перед выключением сервер "
        "один раз перепроверяется через RCON."
    )


def _backup_interval_text(seconds: int) -> str:
    return {
        21600: "6 часов",
        43200: "12 часов",
        86400: "24 часа",
        259200: "3 дня",
        604800: "7 дней",
    }.get(seconds, f"{seconds} сек")


def _auto_backup_status_line(state: dict) -> str:
    if not state["enabled"]:
        return "🚫 Выключен"
    if state["running"]:
        started = datetime.fromtimestamp(state["started_at"]).astimezone().strftime("%H:%M:%S")
        return f"⏳ Создаётся бэкап… (начало в {started})"
    if state["pending"] and state["last_failed_at"]:
        retry = state["retry_in_seconds"]
        return (
            "❌ Последняя попытка завершилась ошибкой. "
            + (f"Повтор через {max(1, (retry + 59) // 60)} мин." if retry else "Повторная попытка ожидается.")
        )
    if state["pending"]:
        return "⏳ Срок наступил, ожидает остановки Minecraft."
    return "🕒 Ожидает следующего срока"


def _auto_backup_text(server) -> str:
    state = auto_backup_manager.status(server)
    if not state["enabled"]:
        return (
            f"💾 <b>Автобэкап · {html.escape(server.server_name)}</b>\n\n"
            "🚫 Выключен\n\n"
            "Автоматические копии создаются только при выключенном сервере."
        )

    due = datetime.fromtimestamp(state["next_due_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
    history = ""
    if state["last_completed_at"]:
        completed = datetime.fromtimestamp(state["last_completed_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
        size = (
            f" · {state['last_size_mb']:.1f} МБ"
            if state["last_size_mb"] is not None else ""
        )
        history = f"\n✅ Последний успешный: <code>{completed}</code>{size}"

    return (
        f"💾 <b>Автобэкап · {html.escape(server.server_name)}</b>\n\n"
        f"🔁 Каждые {_backup_interval_text(state['interval_seconds'])}\n"
        f"📅 Следующий срок: <code>{due}</code> (время сервера)\n"
        f"{_auto_backup_status_line(state)}{history}\n\n"
        "Копирование только при выключенном сервере. "
        "После завершения следующий срок отсчитывается заново."
    )


@router.callback_query(lambda c: c.data and c.data.startswith("tasks:"))
async def auto_tasks_menu(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    uid = callback.from_user.id
    if not (
        access_control.can_server(uid, server_id, "server.autostop")
        or access_control.can_server(uid, server_id, "backup.schedule")
    ):
        await deny_access(callback)
        return

    stop = auto_stop_manager.status(server)
    backup = auto_backup_manager.status(server)
    stop_label = _duration_text(stop["timeout_seconds"]) if stop["enabled"] else "выкл"
    backup_label = _backup_interval_text(backup["interval_seconds"]) if backup["enabled"] else "выкл"
    await safe_edit_text(
        callback.message,
        f"⚙️ <b>Автозадачи · {html.escape(server.server_name)}</b>\n\n"
        f"⏱ Автостоп: <b>{stop_label}</b>\n"
        f"💾 Автобэкап: <b>{backup_label}</b>\n"
        f"{_auto_backup_status_line(backup)}",
        parse_mode="HTML",
        reply_markup=auto_tasks_keyboard(server_id, uid),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("autobackup:"))
async def auto_backup_menu(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "backup.schedule"):
        await deny_access(callback)
        return

    state = auto_backup_manager.status(server)
    await safe_edit_text(
        callback.message,
        _auto_backup_text(server),
        parse_mode="HTML",
        reply_markup=auto_backup_keyboard(server_id, state["interval_seconds"]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("autobackup_set:"))
async def auto_backup_set(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("❌ Некорректная настройка.", show_alert=True)
        return
    try:
        seconds = int(parts[1])
    except ValueError:
        await callback.answer("❌ Некорректный интервал.", show_alert=True)
        return
    server_id = parts[2]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "backup.schedule"):
        await deny_access(callback)
        return
    try:
        state = await auto_backup_manager.set_interval(server, seconds)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await event_history.record(
        server,
        kind="auto_backup_setting",
        text=(
            f"Автобэкап: {_backup_interval_text(seconds) if seconds else 'выключен'}"
            f" · Telegram · {_telegram_actor(callback.from_user)}"
        ),
    )
    await callback.message.edit_text(
        _auto_backup_text(server),
        parse_mode="HTML",
        reply_markup=auto_backup_keyboard(server_id, state["interval_seconds"]),
    )
    await callback.answer(
        f"✅ Автобэкап: {_backup_interval_text(seconds) if seconds else 'выключен'}"
    )


@router.callback_query(lambda c: c.data and c.data.startswith("autostop:"))
async def auto_stop_menu(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(
        callback.from_user.id,
        server_id,
        "server.autostop",
    ):
        await deny_access(callback)
        return

    state = auto_stop_manager.status(server)
    await callback.message.edit_text(
        _auto_stop_text(server),
        parse_mode="HTML",
        reply_markup=auto_stop_keyboard(server_id, state["timeout_seconds"]),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("autostop_set:"))
async def auto_stop_set(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("❌ Некорректная настройка.", show_alert=True)
        return

    try:
        seconds = int(parts[1])
    except ValueError:
        await callback.answer("❌ Некорректный таймаут.", show_alert=True)
        return

    server_id = parts[2]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(
        callback.from_user.id,
        server_id,
        "server.autostop",
    ):
        await deny_access(callback)
        return

    try:
        state = await auto_stop_manager.set_timeout(server, seconds)
        setting = "выключен" if seconds == 0 else f"{_duration_text(seconds)} без игроков"
        await event_history.record(
            server,
            kind="auto_stop_setting",
            text=f"Auto-stop: {setting} · Telegram · {_telegram_actor(callback.from_user)}",
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(
        _auto_stop_text(server),
        parse_mode="HTML",
        reply_markup=auto_stop_keyboard(server_id, state["timeout_seconds"]),
    )
    if seconds:
        await callback.answer(f"✅ Auto-stop: {_duration_text(seconds)}")
    else:
        await callback.answer("✅ Auto-stop выключен")


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
        await auto_stop_manager.on_server_action(server, action, result)
        await event_history.record_action(
            server,
            action=action,
            result=result,
            source="Telegram",
            actor=_telegram_actor(callback.from_user),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Server action failed")
        await callback.message.edit_text(f"❌ <code>{html.escape(str(exc))}</code>", parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
        return
    result_message = action_result_message(result)
    home_text = await _home_text(server)
    await callback.message.edit_text(f"{result_message}\n\n{home_text}", parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
    logger.info("Server action %s completed with %s", action, result)


@router.callback_query(
    lambda c: c.data
    and (c.data.startswith("events:") or c.data.startswith("events_refresh:"))
)
async def server_events(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "events.view"):
        await deny_access(callback)
        return

    await callback.message.edit_text(
        await _events_text(server),
        parse_mode="HTML",
        reply_markup=events_keyboard(server_id),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("events_file:"))
async def server_events_file(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "events.view"):
        await deny_access(callback)
        return

    path = event_history.today_path(server)
    if not path.is_file():
        await callback.answer("Сегодня событий ещё нет.", show_alert=True)
        return

    await callback.answer("📄 Отправляю лог за сегодня")
    await callback.message.answer_document(
        FSInputFile(path),
        caption=f"📋 События · {html.escape(server.server_name)} · сегодня",
        parse_mode="HTML",
    )


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
    try:
        output = await server.manager.tail_output(30) or "(лог запуска пока пуст)"
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"SSH: {str(exc)[:120]}", show_alert=True)
        return
    await callback.message.edit_text(
        f"📜 <b>Логи · {html.escape(server.server_name)}</b>\n\n"
        f"<pre>{html.escape(output[-3500:])}</pre>",
        parse_mode="HTML",
        reply_markup=logs_keyboard(server_id),
    )
    await callback.answer()


async def _server_backups(server) -> list[dict]:
    if server.ssh_remote is not None:
        return await server.ssh_remote.request("list_backups", timeout=30)
    return await asyncio.to_thread(list_backups, server.backup_dir)


async def _server_backup_details(server, filename: str) -> dict:
    if server.ssh_remote is not None:
        return await server.ssh_remote.request("backup_info", filename=filename, timeout=30)
    return await asyncio.to_thread(backup_info, server.backup_dir, filename)


def _can_access_backups(user_id: int, server_id: str) -> bool:
    return (
        access_control.can_server(user_id, server_id, "backup.create")
        or access_control.can_server(user_id, server_id, "backup.view")
    )


@router.callback_query(lambda c: c.data and c.data.startswith("backup:"))
async def backup_menu(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    uid = callback.from_user.id
    if not _can_access_backups(uid, server_id):
        await deny_access(callback)
        return
    await safe_edit_text(
        callback.message,
        f"💾 <b>Бэкапы · {html.escape(server.server_name)}</b>\n\n"
        "Создайте копию мира или посмотрите сохранённые архивы.",
        parse_mode="HTML",
        reply_markup=backup_menu_keyboard(server_id, uid),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("bk_create:"))
async def create_manual_backup(callback: CallbackQuery) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "backup.create"):
        await deny_access(callback)
        return
    await callback.answer()
    await safe_edit_text(
        callback.message,
        f"⏳ Создаю бэкап мира · {html.escape(server.server_name)}…",
        parse_mode="HTML",
    )
    try:
        result = await create_backup(server)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backup failed for %s", server_id)
        result = f"❌ Ошибка бэкапа: <code>{html.escape(str(exc))}</code>"
    await safe_edit_text(
        callback.message,
        result,
        parse_mode="HTML",
        reply_markup=backup_result_keyboard(server_id),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("bk_list:"))
async def show_backup_list(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit() or len(parts[2]) > 4:
        await callback.answer("Некорректная страница.", show_alert=True)
        return
    _, server_id, raw_page = parts
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "backup.view"):
        await deny_access(callback)
        return
    try:
        entries = await _server_backups(server)
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"❌ Не удалось получить список: {str(exc)[:110]}", show_alert=True)
        return

    max_page = max(0, (len(entries) - 1) // BACKUP_PAGE_SIZE)
    page = min(int(raw_page), max_page)
    visible = entries[page * BACKUP_PAGE_SIZE:(page + 1) * BACKUP_PAGE_SIZE]
    summary = (
        f"📂 <b>Бэкапы · {html.escape(server.server_name)}</b>\n"
        f"Всего архивов: <b>{len(entries)}</b> · страница {page + 1}/{max_page + 1}\n\n"
    )
    if not entries:
        summary += "Архивов пока нет."
    else:
        summary += "Выберите архив для просмотра информации."
    await safe_edit_text(
        callback.message,
        summary,
        parse_mode="HTML",
        reply_markup=backup_list_keyboard(server_id, page, visible, len(entries)),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("bk_info:"))
async def show_backup_info(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if (
        len(parts) != 5
        or not parts[2].isdigit() or len(parts[2]) > 4
        or not parts[3].isdigit() or len(parts[3]) > 6
        or len(parts[4]) != 10
        or any(ch not in "0123456789abcdef" for ch in parts[4])
    ):
        await callback.answer("Некорректный архив.", show_alert=True)
        return
    _, server_id, raw_page, raw_index, token = parts
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "backup.view"):
        await deny_access(callback)
        return
    page, index = int(raw_page), int(raw_index)
    if index // BACKUP_PAGE_SIZE != page:
        await callback.answer("Некорректная страница архива.", show_alert=True)
        return
    try:
        entries = await _server_backups(server)
        if index >= len(entries) or addon_token(entries[index]["name"]) != token:
            await callback.answer("Список архивов изменился. Обновите его.", show_alert=True)
            return
        data = await _server_backup_details(server, entries[index]["name"])
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"❌ Не удалось прочитать архив: {str(exc)[:110]}", show_alert=True)
        return

    title = (
        f"📦 <b>Архив · {html.escape(server.server_name)}</b>\n\n"
        f"🗂 <code>{html.escape(data['name'])}</code>\n"
        f"📅 {html.escape(data['created'])}\n"
        f"🏷 {html.escape(data['type'])}\n"
        f"💾 {data['size_mb']:.1f} МБ\n"
    )
    if not data["valid"]:
        title += "\n⚠️ Не удалось прочитать структуру ZIP. Использовать архив для восстановления нельзя."
    else:
        total_mb = data["uncompressed_bytes"] / 1048576
        title += (
            f"\n📄 Файлов: {data['file_count']}\n"
            f"📦 Размер до сжатия: {total_mb:.1f} МБ\n"
        )
        if data["preview"]:
            preview = "\n".join(
                f"• <code>{html.escape(name[:100])}</code>" for name in data["preview"]
            )
            title += f"\n<b>Файлы внутри (первые 12):</b>\n{preview}"
    await safe_edit_text(
        callback.message,
        title,
        parse_mode="HTML",
        reply_markup=backup_info_keyboard(server_id, page),
    )
    await callback.answer()
