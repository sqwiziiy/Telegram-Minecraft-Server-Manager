import html
import os
import re
import tempfile

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, Message

from config import MAX_MOD_UPLOAD_MB
from keyboards.inline import addon_token, mods_list_keyboard, mod_toggle_confirm_keyboard
from middlewares.auth import deny_access
from services.access_control import access_control
from services.addons import is_addon_name, is_disabled, list_addons, toggle_addon
from services.telegram_context import resolve_server, safe_edit_text

router = Router()


class ModsStates(StatesGroup):
    awaiting_upload = State()
_SAFE_NAME_RE = re.compile(r'^[\w\-. +\[\]()@#]+\.jar$', re.ASCII)


def _safe(name: str) -> bool:
    return bool(_SAFE_NAME_RE.fullmatch(name)) and "/" not in name and "\\" not in name


def _safe_upload_name(original: str) -> str | None:
    safe_name = os.path.basename(original)
    if safe_name != original or not _safe(safe_name):
        return None
    return safe_name


async def _files_for(server) -> list[str] | None:
    if server.ssh_remote is not None:
        return await server.ssh_remote.request("list_mods")
    return list_addons(server.mods_dir)


def _can_toggle(user_id: int, server_id: str) -> bool:
    return access_control.can_server(user_id, server_id, "mods.toggle") or access_control.can_server(
        user_id, server_id, "mods.delete"  # legacy permission, no longer deletes files
    )


def _addon_software(server) -> str:
    return getattr(server, "server_software", "mods")


def _addon_labels(server) -> tuple[str, str, str]:
    if _addon_software(server) == "plugins":
        return "🔌 <b>Плагины</b>", "плагинов", "плагин"
    return "🧩 <b>Моды</b>", "модов", "мод"


def _text(files: list[str], user_id: int, server_id: str, *, software: str = "mods") -> str:
    title, plural = ("🔌 <b>Плагины</b>", "плагинов") if software == "plugins" else ("🧩 <b>Моды</b>", "модов")
    lines = [
        f"{'🚫' if is_disabled(name) else '✅'} {idx}. <code>{html.escape(name)}</code>"
        for idx, name in enumerate(files, start=1)
    ]
    text = title + "\n\n" + ("\n".join(lines) if lines else f"Папка {plural} пуста.")
    if _can_toggle(user_id, server_id) and files:
        text += "\n\n🚫 N — отключить, ✅ N — включить (нажмите номер ниже)."
        text += "\nИзменения применяются после следующего запуска Minecraft."
    if access_control.can_server(user_id, server_id, "mods.upload"):
        text += "\n\nОтправьте .jar для загрузки."
    return text


@router.callback_query(lambda c: c.data and c.data.startswith("mods:"))
async def list_mods(callback: CallbackQuery, state: FSMContext) -> None:
    server_id = callback.data.split(":", 1)[1]
    server = resolve_server(server_id)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if _addon_software(server) == "vanilla":
        await callback.answer("🍃 Vanilla: моды и плагины отключены.", show_alert=True)
        return
    if not access_control.can_server(callback.from_user.id, server_id, "mods.view"):
        await deny_access(callback)
        return
    await state.clear()
    await state.update_data(server_id=server_id)
    await state.set_state(ModsStates.awaiting_upload)
    try:
        files = await _files_for(server)
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"SSH: {str(exc)[:120]}", show_alert=True)
        return
    if files is None:
        await callback.message.edit_text(
            f"❌ Папка {_addon_labels(server)[1]} не найдена:\n<code>{html.escape(server.mods_dir)}</code>",
            parse_mode="HTML",
            reply_markup=mods_list_keyboard([], callback.from_user.id, server_id),
        )
        await callback.answer()
        return
    await safe_edit_text(callback.message, _text(files, callback.from_user.id, server_id, software=_addon_software(server)), parse_mode="HTML", reply_markup=mods_list_keyboard(files, callback.from_user.id, server_id))
    await callback.answer()


def _toggle_parts(data: str, prefix: str) -> tuple[str, int, str] | None:
    if not isinstance(data, str) or not data.startswith(prefix):
        return None
    try:
        sid, raw_idx, token = data[len(prefix):].split(":", 2)
        idx = int(raw_idx)
        if not sid or not 0 <= idx < 100000 or not re.fullmatch(r"[0-9a-f]{10}", token):
            return None
        return sid, idx, token
    except ValueError:
        return None


async def _selected_addon(server, idx: int, token: str) -> str | None:
    files = await _files_for(server)
    if not files or idx >= len(files):
        return None
    filename = files[idx]
    return filename if is_addon_name(filename) and addon_token(filename) == token else None


@router.callback_query(lambda c: c.data and c.data.startswith("mt:"))
async def ask_toggle(callback: CallbackQuery) -> None:
    parts = _toggle_parts(callback.data, "mt:")
    if parts is None:
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    sid, idx, token = parts
    server = resolve_server(sid)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if _addon_software(server) == "vanilla" or not _can_toggle(callback.from_user.id, sid):
        await deny_access(callback)
        return
    try:
        filename = await _selected_addon(server, idx, token)
        if filename is None:
            await callback.answer("Список изменился. Обновите его.", show_alert=True)
            return
        if (await server.manager.status()).running:
            await callback.answer("⏹ Сначала остановите Minecraft.", show_alert=True)
            return
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"❌ Нет доступа к серверу: {str(exc)[:110]}", show_alert=True)
        return
    verb = "Включить" if is_disabled(filename) else "Отключить"
    await safe_edit_text(
        callback.message,
        f"🧩 <b>{verb} {_addon_labels(server)[2]}?</b>\n"
        f"<code>{html.escape(filename)}</code>",
        parse_mode="HTML",
        reply_markup=mod_toggle_confirm_keyboard(sid, idx, filename),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("mt_ok:"))
async def confirm_toggle(callback: CallbackQuery) -> None:
    parts = _toggle_parts(callback.data, "mt_ok:")
    if parts is None:
        await callback.answer("Некорректный выбор.", show_alert=True)
        return
    sid, idx, token = parts
    server = resolve_server(sid)
    if server is None:
        await callback.answer("❌ Сервер больше не настроен.", show_alert=True)
        return
    if _addon_software(server) == "vanilla" or not _can_toggle(callback.from_user.id, sid):
        await deny_access(callback)
        return
    try:
        filename = await _selected_addon(server, idx, token)
        if filename is None:
            await callback.answer("Список изменился. Обновите его.", show_alert=True)
            return
        ran, updated_name = await toggle_addon(server, filename)
        if not ran:
            await callback.answer("⏹ Сначала остановите Minecraft.", show_alert=True)
            return
        files = await _files_for(server) or []
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        await callback.answer(f"❌ Не удалось изменить файл: {str(exc)[:100]}", show_alert=True)
        return
    except Exception as exc:  # noqa: BLE001
        await callback.answer(f"❌ Сервер недоступен: {str(exc)[:100]}", show_alert=True)
        return
    await safe_edit_text(
        callback.message,
        f"✅ <code>{html.escape(str(updated_name))}</code>\n\n"
        + _text(files, callback.from_user.id, sid, software=_addon_software(server)),
        parse_mode="HTML",
        reply_markup=mods_list_keyboard(files, callback.from_user.id, sid),
    )
    await callback.answer("✅ Готово. Изменения применятся после запуска сервера.")


@router.message(ModsStates.awaiting_upload, F.document)
async def upload_mod(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sid = data.get("server_id")
    server = resolve_server(sid)
    if server is None:
        await message.answer("❌ Сервер больше не настроен.")
        await state.clear()
        return
    if _addon_software(server) == "vanilla":
        await message.answer("🍃 Vanilla: загрузка модов и плагинов отключена.")
        await state.clear()
        return
    if not access_control.can_server(message.from_user.id, sid, "mods.upload"):
        await message.answer("⛔ Откройте сервер с правом загрузки модов.")
        return
    doc: Document = message.document
    safe_name = _safe_upload_name(doc.file_name or "")
    if not doc.file_name or not doc.file_name.lower().endswith(".jar") or safe_name is None:
        await message.answer("❌ Разрешена загрузка только безопасных файлов .jar.")
        return
    if doc.file_size and doc.file_size > MAX_MOD_UPLOAD_MB * 1024 * 1024:
        await message.answer(f"❌ Файл больше лимита {MAX_MOD_UPLOAD_MB} MB.")
        return
    if server.ssh_remote is not None:
        status = await message.answer(f"⏳ Загружаю {_addon_labels(server)[2]} на удалённый сервер…")
        fd, temp_path = tempfile.mkstemp(prefix=".remote-mod-", suffix=".jar")
        os.close(fd)
        try:
            await message.bot.download(doc, destination=temp_path)
            await server.ssh_remote.upload_mod(safe_name, temp_path)
            files = await _files_for(server) or []
            await status.edit_text(
                f"✅ <code>{html.escape(safe_name)}</code> загружен на удалённый сервер.\n\n"
                f"{_text(files, message.from_user.id, sid, software=_addon_software(server))}",
                parse_mode="HTML",
                reply_markup=mods_list_keyboard(files, message.from_user.id, sid),
            )
        except Exception as exc:  # noqa: BLE001
            await status.edit_text(
                f"❌ Ошибка удалённой загрузки: <code>{html.escape(str(exc))}</code>",
                parse_mode="HTML",
            )
        finally:
            os.remove(temp_path)
        return
    os.makedirs(server.mods_dir, exist_ok=True)
    target = os.path.join(server.mods_dir, safe_name)
    root = os.path.realpath(server.mods_dir)
    real_target = os.path.realpath(target)
    try:
        inside_mods = os.path.commonpath([root, real_target]) == root
    except ValueError:
        inside_mods = False
    if not inside_mods:
        await message.answer("❌ Недопустимый путь.")
        return
    if os.path.lexists(target):
        await message.answer("⚠️ Такой файл уже существует.")
        return
    status = await message.answer(f"⏳ Загружаю {_addon_labels(server)[2]}…")
    fd, temp_path = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=server.mods_dir)
    os.close(fd)
    try:
        await message.bot.download(doc, destination=temp_path)
        os.link(temp_path, target)
    except FileExistsError:
        await status.edit_text("❌ Файл появился во время загрузки.")
        return
    except Exception as exc:  # noqa: BLE001
        await status.edit_text(f"❌ Ошибка загрузки: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
    files = list_addons(server.mods_dir) or []
    await status.edit_text(
        f"✅ <code>{html.escape(safe_name)}</code> загружен.\n\n"
        f"{_text(files, message.from_user.id, sid)}",
        parse_mode="HTML",
        reply_markup=mods_list_keyboard(files, message.from_user.id, sid),
    )
