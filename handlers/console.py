import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from keyboards.inline import console_exit_keyboard
from middlewares.auth import deny_access
from services.access_control import access_control
from services.telegram_context import resolve_server
from keyboards.inline import server_home_keyboard
from handlers.start import _home_text, _server_picker

router = Router()


class ConsoleStates(StatesGroup):
    console_mode = State()


@router.callback_query(lambda c: c.data and c.data.startswith("console:"))
async def enter_console(callback: CallbackQuery, state: FSMContext) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "console.use"):
        await deny_access(callback)
        return
    await state.clear()
    await state.set_state(ConsoleStates.console_mode)
    await state.update_data(server_id=server_id)
    await callback.message.edit_text(f"💻 <b>RCON · {html.escape(server.server_name)}</b>\nОтправьте команду Minecraft без <code>/</code>.", parse_mode="HTML", reply_markup=console_exit_keyboard(server_id))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("exit_console:"))
async def exit_console(callback: CallbackQuery, state: FSMContext) -> None:
    server_id = callback.data.split(":", 1)[1]
    await state.clear()
    server = resolve_server(server_id)
    if server is not None and access_control.server_ids_for(callback.from_user.id, [server_id]):
        await callback.message.edit_text(await _home_text(server), parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
    else:
        text, markup = await _server_picker(callback.from_user.id)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await callback.answer()


@router.message(ConsoleStates.console_mode, F.text & ~F.text.startswith("/"))
async def handle_console_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    server_id = data.get("server_id")
    if not isinstance(server_id, str) or not access_control.can_server(message.from_user.id, server_id, "console.use"):
        await state.clear()
        await message.answer("⛔ Доступ к консоли запрещён.")
        return
    server = resolve_server(server_id)
    if server is None:
        await state.clear()
        await message.answer("❌ Сервер больше не настроен.")
        return
    command = (message.text or "").strip()
    if not command:
        return
    if len(command) > 1000:
        await message.answer("❌ Команда слишком длинная.")
        return
    response = await server.rcon(command)
    await message.answer(f"<b>💻 RCON · {html.escape(server.server_name)}</b>\n<code>$ {html.escape(command)}</code>\n\n<pre>{html.escape(response)}</pre>", parse_mode="HTML", reply_markup=console_exit_keyboard(server_id))
