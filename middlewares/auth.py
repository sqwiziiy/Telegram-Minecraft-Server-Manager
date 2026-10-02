from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from services.access_control import access_control


async def deny_access(event: TelegramObject, text: str = "⛔ Доступ запрещён.") -> None:
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)


async def require_permission(event: TelegramObject, permission: str) -> bool:
    user = getattr(event, "from_user", None)
    if user is not None and access_control.can(user.id, permission):
        return True

    await deny_access(event, "⛔ У вас нет права на это действие.")
    return False


class AuthMiddleware(BaseMiddleware):
    """Rejects users that are not present in the access policy."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        if user is None or not access_control.is_authorized(user.id):
            await deny_access(event)
            return None

        return await handler(event, data)
