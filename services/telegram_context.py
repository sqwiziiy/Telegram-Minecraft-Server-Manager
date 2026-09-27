from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from services.access_control import access_control
from services.server_registry import ManagedServer, server_registry


def resolve_server(server_id: object) -> ManagedServer | None:
    if not isinstance(server_id, str) or not server_id:
        return None
    try:
        return server_registry.get(server_id)
    except KeyError:
        return None


async def safe_edit_text(message, text: str, **kwargs):
    """Edit a Telegram message, ignoring only the harmless no-op edit error."""
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return None
        raise


async def selected_server(state: FSMContext, user_id: int, permission: str | None = None) -> ManagedServer | None:
    data = await state.get_data()
    server_id = data.get("server_id")
    if not isinstance(server_id, str):
        return None
    server = resolve_server(server_id)
    if server is None:
        return None
    if permission and not access_control.can_server(user_id, server_id, permission):
        return None
    if not permission and not access_control.server_ids_for(user_id, [server_id]):
        return None
    return server
