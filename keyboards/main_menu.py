from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⚙️ Система")],
            [KeyboardButton(text="💻 Консоль"), KeyboardButton(text="🧩 Моды")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Управление Minecraft-сервером",
        selective=True,
    )
