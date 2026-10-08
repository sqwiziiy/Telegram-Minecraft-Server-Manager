from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
import html

from keyboards.inline import server_home_keyboard, server_list_keyboard
from middlewares.auth import deny_access
from services.access_control import access_control
from services.server_registry import server_registry
from services.telegram_context import resolve_server, safe_edit_text

router = Router()


async def _server_picker(user_id: int):
    servers = [s for s in server_registry.list() if access_control.server_ids_for(user_id, [s.server_id])]
    if not servers:
        return "⛔ У вас нет доступа ни к одному Minecraft-серверу.", None
    statuses: dict[str, bool | None] = {}
    for server in servers:
        try:
            statuses[server.server_id] = (await server.manager.status()).running
        except Exception:  # SSH unavailable must NOT masquerade as stopped.
            statuses[server.server_id] = None
    return "🎮 <b>Minecraft Server Manager</b>\n\nВыберите сервер (❔ — связь недоступна):", server_list_keyboard(servers, statuses, show_host=access_control.can_system(user_id))


async def _home_text(server) -> str:
    try:
        process = await server.manager.status()
    except Exception as exc:  # noqa: BLE001
        return f"🌩 <b>{html.escape(server.server_name)}</b>\n❔ Связь с сервером недоступна: <code>{html.escape(str(exc)[:250])}</code>"
    if not process.running:
        return f"🌩 <b>{html.escape(server.server_name)}</b>\n⚫ Остановлен"
    players = await server.rcon("list") if server.rcon_configured else "нет данных"
    return (f"🌩 <b>{html.escape(server.server_name)}</b>\n"
            f"🟢 Работает · <code>{html.escape(players)}</code>\n"
            f"Аптайм: {process.uptime_seconds // 60} мин · RAM: {process.memory_mb / 1024:.1f} GB")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    # Telegram keeps reply keyboards on the client until explicitly removed.
    # Send an invisible cleanup message so the new UI remains inline-only.
    await message.answer("\u2063", reply_markup=ReplyKeyboardRemove(remove_keyboard=True))
    await state.clear()
    text, markup = await _server_picker(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(lambda c: c.data in {"servers", "sv_refresh"})
async def server_list_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _server_picker(callback.from_user.id)
    await safe_edit_text(callback.message, text, parse_mode="HTML", reply_markup=markup)
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("sv:"))
async def server_selected(callback: CallbackQuery, state: FSMContext) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if not access_control.server_ids_for(callback.from_user.id, [server_id]):
        await deny_access(callback)
        return
    await state.clear()
    await state.update_data(server_id=server_id)
    await callback.message.edit_text(await _home_text(server), parse_mode="HTML", reply_markup=server_home_keyboard(server, callback.from_user.id))
    await callback.answer()
