import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from keyboards.inline import console_exit_keyboard
from keyboards.main_menu import get_main_menu
from middlewares.auth import require_permission
from services.rcon import send_rcon_command

router = Router()


class ConsoleStates(StatesGroup):
    console_mode = State()


@router.message(F.text == "💻 Консоль")
async def enter_console(message: Message, state: FSMContext) -> None:
    if not await require_permission(message, "console.use"):
        return

    await state.set_state(ConsoleStates.console_mode)
    await message.answer(
        "💻 <b>RCON-консоль активна.</b>\n"
        "Отправьте команду Minecraft без <code>/</code>.\n\n"
        "Например: <code>list</code>, <code>time set day</code>, <code>say hello</code>.",
        parse_mode="HTML",
        reply_markup=console_exit_keyboard(),
    )


@router.callback_query(F.data == "exit_console")
async def exit_console_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("✅ Вы вышли из RCON-консоли.")
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_menu(callback.from_user.id),
    )
    await callback.answer()


@router.message(ConsoleStates.console_mode)
async def handle_console_input(message: Message, state: FSMContext) -> None:
    if not await require_permission(message, "console.use"):
        await state.clear()
        return

    command = (message.text or "").strip()
    if not command:
        return
    if len(command) > 1000:
        await message.answer("❌ Команда слишком длинная.")
        return

    response = await send_rcon_command(command)
    await message.answer(
        f"<code>$ {html.escape(command)}</code>\n\n"
        f"<pre>{html.escape(response)}</pre>",
        parse_mode="HTML",
        reply_markup=console_exit_keyboard(),
    )
