import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from services.rcon import send_rcon_command
from services.server_process import server_process_manager

router = Router()


def _format_uptime(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}ч {minutes}м"


async def server_status(message: Message) -> None:
    process = await server_process_manager.status()
    if not process.running:
        await message.answer(
            "📊 <b>Статус сервера</b>\n\n🔴 Сервер остановлен.",
            parse_mode="HTML",
        )
        return

    players = await send_rcon_command("list")
    await message.answer(
        "📊 <b>Статус сервера</b>\n\n"
        f"🟢 Работает · PID <code>{process.pid}</code> · {_format_uptime(process.uptime_seconds)}\n"
        f"🧠 Java/process RSS: <code>{process.memory_mb:.0f} MB</code>\n"
        f"👥 <code>{html.escape(players)}</code>",
        parse_mode="HTML",
    )


@router.message(F.text == "📊 Статус")
async def status_button(message: Message) -> None:
    await server_status(message)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await server_status(message)
