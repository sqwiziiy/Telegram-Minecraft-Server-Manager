import html

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from keyboards.inline import server_home_keyboard
from middlewares.auth import deny_access
from services.access_control import access_control
from services.telegram_context import resolve_server

router = Router()


def _format_uptime(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    return f"{hours}ч {rem // 60}м"


async def _status_text(server) -> str:
    process = await server.manager.status()
    if not process.running:
        return f"🌩 <b>{html.escape(server.server_name)}</b>\n\n⚫ Остановлен"
    players = await server.rcon("list") if server.rcon_configured else "нет данных"
    return (f"🌩 <b>{html.escape(server.server_name)}</b>\n"
            f"🟢 Работает · <code>{html.escape(players)}</code>\n"
            f"Аптайм: {_format_uptime(process.uptime_seconds)} · RAM: {process.memory_mb / 1024:.1f} GB")


async def _show_status(callback: CallbackQuery, server_id: str, state: FSMContext | None = None) -> None:
    if not access_control.can_server(callback.from_user.id, server_id, "server.status"):
        await deny_access(callback)
        return
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if state is not None:
        await state.clear()
    await callback.message.edit_text(await _status_text(server), parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("status:"))
async def status_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_status(callback, callback.data.split(":", 1)[1], state)


@router.callback_query(lambda c: c.data and c.data.startswith("manage:"))
async def manage_callback(callback: CallbackQuery, state: FSMContext) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.server_ids_for(callback.from_user.id, [server_id]):
        await deny_access(callback)
        return
    await state.clear()
    from keyboards.inline import server_actions_keyboard
    await callback.message.edit_text(f"⚙️ <b>Управление · {html.escape(server.server_name)}</b>", parse_mode="HTML", reply_markup=server_actions_keyboard(server, callback.from_user.id))
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("refresh:"))
async def refresh_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _show_status(callback, callback.data.split(":", 1)[1], state)
