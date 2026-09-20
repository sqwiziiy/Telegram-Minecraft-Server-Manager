import html
import os
import re
import tempfile

from aiogram import F, Router
from aiogram.types import CallbackQuery, Document, Message

from config import MAX_MOD_UPLOAD_MB, MODS_DIR
from keyboards.inline import mod_delete_confirm_keyboard, mods_list_keyboard

router = Router()

_SAFE_NAME_RE = re.compile(r'^[\w\-. +\[\]()@#]+\.jar$', re.ASCII)


def _is_safe_jar_name(name: str) -> bool:
    return bool(_SAFE_NAME_RE.fullmatch(name)) and "/" not in name and "\\" not in name


def _sorted_jars() -> list[str] | None:
    try:
        return sorted(
            f for f in os.listdir(MODS_DIR)
            if f.lower().endswith(".jar") and os.path.isfile(os.path.join(MODS_DIR, f))
        )
    except FileNotFoundError:
        return None


def _mods_text(files: list[str]) -> str:
    if not files:
        return (
            "📂 <b>Папка модов пуста.</b>\n\n"
            "Отправьте <b>.jar</b> в этот чат, чтобы добавить мод."
        )
    lines = "\n".join(
        f"{i + 1}. <code>{html.escape(name)}</code>"
        for i, name in enumerate(files)
    )
    return (
        f"🧩 <b>Установленные моды ({len(files)})</b>\n\n"
        f"{lines}\n\n"
        "Нажмите <b>🗑 N</b> для удаления или отправьте новый <b>.jar</b>."
    )


def _parse_index(data: str, prefix: str) -> int | None:
    if not data.startswith(prefix):
        return None
    try:
        value = int(data[len(prefix):])
        return value if value >= 0 else None
    except ValueError:
        return None


@router.message(F.text == "🧩 Моды")
async def list_mods(message: Message) -> None:
    files = _sorted_jars()
    if files is None:
        await message.answer(
            f"❌ Папка модов не найдена:\n<code>{html.escape(MODS_DIR)}</code>",
            parse_mode="HTML",
        )
        return
    await message.answer(
        _mods_text(files),
        parse_mode="HTML",
        reply_markup=mods_list_keyboard(len(files)) if files else None,
    )


@router.callback_query(F.data.startswith("dm:"))
async def ask_delete_mod(callback: CallbackQuery) -> None:
    idx = _parse_index(callback.data, "dm:")
    files = _sorted_jars()
    if idx is None or files is None or idx >= len(files):
        await callback.answer("❌ Список модов изменился. Откройте его снова.", show_alert=True)
        return

    mod_name = files[idx]
    await callback.message.answer(
        f"🗑 Удалить <code>{html.escape(mod_name)}</code>?",
        parse_mode="HTML",
        reply_markup=mod_delete_confirm_keyboard(idx),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dm_ok:"))
async def confirm_delete_mod(callback: CallbackQuery) -> None:
    idx = _parse_index(callback.data, "dm_ok:")
    files = _sorted_jars()
    if idx is None or files is None or idx >= len(files):
        await callback.answer("❌ Список модов изменился.", show_alert=True)
        return

    mod_name = files[idx]
    if not _is_safe_jar_name(mod_name):
        await callback.answer("❌ Некорректное имя файла.", show_alert=True)
        return

    real_mods = os.path.realpath(MODS_DIR)
    real_target = os.path.realpath(os.path.join(MODS_DIR, mod_name))
    try:
        inside_mods = os.path.commonpath([real_mods, real_target]) == real_mods
    except ValueError:
        inside_mods = False

    if not inside_mods:
        await callback.answer("❌ Недопустимый путь.", show_alert=True)
        return

    try:
        os.remove(real_target)
    except FileNotFoundError:
        await callback.answer("❌ Файл уже удалён.", show_alert=True)
        return
    except OSError as exc:
        await callback.answer(f"❌ Ошибка удаления: {exc}", show_alert=True)
        return

    new_files = _sorted_jars() or []
    await callback.message.edit_text(
        f"✅ <code>{html.escape(mod_name)}</code> удалён.\n\n{_mods_text(new_files)}",
        parse_mode="HTML",
        reply_markup=mods_list_keyboard(len(new_files)) if new_files else None,
    )
    await callback.answer()


@router.callback_query(F.data == "dm_cancel")
async def cancel_delete_mod(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer()


@router.message(F.document)
async def upload_mod(message: Message) -> None:
    doc: Document = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".jar"):
        await message.answer("❌ Разрешена загрузка только файлов <b>.jar</b>.", parse_mode="HTML")
        return

    if doc.file_size and doc.file_size > MAX_MOD_UPLOAD_MB * 1024 * 1024:
        await message.answer(f"❌ Файл больше лимита {MAX_MOD_UPLOAD_MB} MB.")
        return

    safe_name = os.path.basename(doc.file_name)
    if safe_name != doc.file_name or not _is_safe_jar_name(safe_name):
        await message.answer("❌ Имя файла содержит недопустимые символы.")
        return

    os.makedirs(MODS_DIR, exist_ok=True)
    target = os.path.join(MODS_DIR, safe_name)
    if os.path.lexists(target):
        await message.answer(
            f"⚠️ <code>{html.escape(safe_name)}</code> уже существует. "
            "Удалите старую версию через меню перед загрузкой.",
            parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ Загружаю мод…")
    fd, temp_path = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=MODS_DIR)
    os.close(fd)

    try:
        await message.bot.download(doc, destination=temp_path)
        # Hard-link creation is atomic and fails instead of following/overwriting a symlink.
        os.link(temp_path, target)
    except FileExistsError:
        await status_msg.edit_text("❌ Файл с таким именем появился во время загрузки.")
        return
    except Exception as exc:  # noqa: BLE001
        await status_msg.edit_text(f"❌ Ошибка загрузки: <code>{html.escape(str(exc))}</code>", parse_mode="HTML")
        return
    finally:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass

    files = _sorted_jars() or []
    await status_msg.edit_text(
        f"✅ Мод <code>{html.escape(safe_name)}</code> загружен.\n"
        "🔁 Для применения перезапустите сервер.\n\n"
        + _mods_text(files),
        parse_mode="HTML",
        reply_markup=mods_list_keyboard(len(files)) if files else None,
    )
