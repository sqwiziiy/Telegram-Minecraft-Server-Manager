from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from services.access_control import access_control


_SYSTEM_PANEL_PERMISSIONS = (
    "system.view",
    "server.status",
    "server.start",
    "server.stop",
    "server.restart",
    "logs.view",
    "backup.create",
)

_MOD_PANEL_PERMISSIONS = (
    "mods.view",
    "mods.upload",
    "mods.delete",
)


def _can_any(user_id: int, permissions: tuple[str, ...]) -> bool:
    return any(access_control.can(user_id, permission) for permission in permissions)


def get_main_menu(user_id: int) -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    rows: list[list[KeyboardButton]] = []

    top_row: list[KeyboardButton] = []
    if access_control.can(user_id, "server.status"):
        top_row.append(KeyboardButton(text="📊 Статус"))
    if _can_any(user_id, _SYSTEM_PANEL_PERMISSIONS):
        top_row.append(KeyboardButton(text="⚙️ Система"))
    if top_row:
        rows.append(top_row)

    second_row: list[KeyboardButton] = []
    if access_control.can(user_id, "console.use"):
        second_row.append(KeyboardButton(text="💻 Консоль"))
    if _can_any(user_id, _MOD_PANEL_PERMISSIONS):
        second_row.append(KeyboardButton(text="🧩 Моды"))
    if second_row:
        rows.append(second_row)

    if not rows:
        return ReplyKeyboardRemove(remove_keyboard=True)

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Управление Minecraft-сервером",
        selective=True,
    )
