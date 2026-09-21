from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from keyboards.main_menu import get_main_menu

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🎮 <b>Minecraft Server Manager</b>\n\n"
        "Доступные функции показаны в меню ниже.",
        parse_mode="HTML",
        reply_markup=get_main_menu(message.from_user.id),
    )
