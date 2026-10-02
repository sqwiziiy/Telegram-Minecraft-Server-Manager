from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.access_control import access_control
from services.server_registry import ManagedServer


def server_list_keyboard(servers: list[ManagedServer], statuses: dict[str, bool], *, show_host: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{'🟢' if statuses.get(s.server_id, False) else '⚫'} {s.server_name}", callback_data=f"sv:{s.server_id}")] for s in servers]
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="sv_refresh")])
    if show_host:
        rows.append([InlineKeyboardButton(text="🖥 Хост", callback_data="host")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_home_keyboard(server: ManagedServer, user_id: int) -> InlineKeyboardMarkup:
    sid = server.server_id
    rows: list[list[InlineKeyboardButton]] = []
    if access_control.can_server(user_id, sid, "server.status"):
        rows.append([InlineKeyboardButton(text="📊 Статус", callback_data=f"status:{sid}")])
    if any(access_control.can_server(user_id, sid, p) for p in ("server.start", "server.stop", "server.restart")):
        rows.append([InlineKeyboardButton(text="⚙️ Управление", callback_data=f"manage:{sid}")])
    if access_control.can_server(user_id, sid, "server.autostop"):
        rows.append([InlineKeyboardButton(text="⏱ Auto-stop", callback_data=f"autostop:{sid}")])
    if access_control.can_server(user_id, sid, "events.view"):
        rows.append([InlineKeyboardButton(text="📋 События", callback_data=f"events:{sid}")])
    utility = []
    for label, perm, action in (("💻 Консоль", "console.use", "console"), ("🧩 Моды", "mods.view", "mods"), ("📜 Логи", "logs.view", "logs"), ("💾 Бэкап", "backup.create", "backup")):
        if access_control.can_server(user_id, sid, perm):
            utility.append(InlineKeyboardButton(text=label, callback_data=f"{action}:{sid}"))
    for i in range(0, len(utility), 2):
        rows.append(utility[i:i + 2])
    if access_control.can_server(user_id, sid, "server.status"):
        rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh:{sid}")])
    rows.append([InlineKeyboardButton(text="◀️ Серверы", callback_data="servers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_actions_keyboard(server: ManagedServer, user_id: int) -> InlineKeyboardMarkup:
    sid = server.server_id
    buttons = [InlineKeyboardButton(text=label, callback_data=f"confirm:{action}:{sid}") for label, perm, action in (("▶️ Запустить", "server.start", "start"), ("⏹ Остановить", "server.stop", "stop"), ("🔁 Перезапустить", "server.restart", "restart")) if access_control.can_server(user_id, sid, perm)]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"sv:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(action: str, server_id: str) -> InlineKeyboardMarkup:
    labels = {"start": "▶️ Да, запустить", "stop": "⏹ Да, остановить", "restart": "🔁 Да, перезапустить"}
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=labels[action], callback_data=f"do:{action}:{server_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"sv:{server_id}"),
    ]])


def console_exit_keyboard(server_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Выйти из консоли", callback_data=f"exit_console:{server_id}")
    ]])


def mods_list_keyboard(count: int, user_id: int, server_id: str | None = None) -> InlineKeyboardMarkup | None:
    if not server_id:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    if count > 0 and access_control.can_server(user_id, server_id, "mods.delete"):
        buttons = [InlineKeyboardButton(text=f"🗑 {i + 1}", callback_data=f"dm:{server_id}:{i}") for i in range(count)]
        rows.extend(buttons[i:i + 5] for i in range(0, len(buttons), 5))
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"sv:{server_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mod_delete_confirm_keyboard(server_id: str, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"dm_ok:{server_id}:{idx}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"mods:{server_id}"),
    ]])


def auto_stop_keyboard(server_id: str, current_seconds: int) -> InlineKeyboardMarkup:
    options = (
        ("🚫 Выкл", 0),
        ("1 мин", 60),
        ("2 мин", 120),
        ("5 мин", 300),
        ("10 мин", 600),
    )
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if seconds == current_seconds else "") + label,
            callback_data=f"autostop_set:{seconds}:{server_id}",
        )
        for label, seconds in options
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"sv:{server_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def events_keyboard(server_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"events_refresh:{server_id}")],
            [InlineKeyboardButton(text="📄 Лог за сегодня", callback_data=f"events_file:{server_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"sv:{server_id}")],
        ]
    )
