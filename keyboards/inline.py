from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.access_control import access_control


def console_exit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Выйти из консоли", callback_data="exit_console")]
        ]
    )


def system_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    action_row: list[InlineKeyboardButton] = []
    if access_control.can(user_id, "server.start"):
        action_row.append(InlineKeyboardButton(text="▶️ Запустить", callback_data="confirm_start"))
    if access_control.can(user_id, "server.stop"):
        action_row.append(InlineKeyboardButton(text="⏹ Остановить", callback_data="confirm_stop"))
    if action_row:
        rows.append(action_row)

    if access_control.can(user_id, "server.restart"):
        rows.append([
            InlineKeyboardButton(text="🔁 Перезапустить", callback_data="confirm_restart")
        ])

    utility_row: list[InlineKeyboardButton] = []
    if access_control.can(user_id, "logs.view"):
        utility_row.append(InlineKeyboardButton(text="📜 Лог запуска", callback_data="server_logs"))
    if access_control.can(user_id, "backup.create"):
        utility_row.append(InlineKeyboardButton(text="💾 Бэкап", callback_data="create_backup"))
    if utility_row:
        rows.append(utility_row)

    if any(
        access_control.can(user_id, permission)
        for permission in (
            "system.view",
            "server.status",
            "server.start",
            "server.stop",
            "server.restart",
            "logs.view",
            "backup.create",
        )
    ):
        rows.append([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_system")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_action_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    labels = {
        "start": ("▶️ Да, запустить", "start_server"),
        "stop": ("⏹ Да, остановить", "stop_server"),
        "restart": ("🔁 Да, перезапустить", "restart_server"),
    }
    yes_text, yes_cb = labels[action]
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text=yes_text, callback_data=yes_cb),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_{action}"),
        ]]
    )


def mods_list_keyboard(count: int, user_id: int) -> InlineKeyboardMarkup | None:
    if count <= 0 or not access_control.can(user_id, "mods.delete"):
        return None

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i in range(count):
        row.append(InlineKeyboardButton(text=f"🗑 {i + 1}", callback_data=f"dm:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mod_delete_confirm_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"dm_ok:{idx}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="dm_cancel"),
        ]]
    )
