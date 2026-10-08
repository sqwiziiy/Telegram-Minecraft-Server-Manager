"""Safely unregister a Minecraft server without touching the installation.

This wizard changes only the bot's local configuration and persisted tasks.
It never connects to SSH, sends RCON, stops Java or deletes world files.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from scripts.manage_users import ConfigurationError, configured_path, load_users
from scripts.manage_servers import (
    SERVER_ID_PATTERN,
    VALID_ENV_NAME,
    apply_files,
    ask_text,
    read_bytes,
    update_env,
)


def remove_env_key(contents: str, key: str) -> str:
    """Delete only complete assignments for a validated variable name."""
    if not VALID_ENV_NAME.fullmatch(key):
        raise ConfigurationError(f"Некорректный ключ в конфигурации: {key!r}")
    lines = contents.splitlines(keepends=True)
    expression = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    kept = [line for line in lines if not (
        (match := expression.match(line)) and match.group(1) == key
    )]
    return "".join(kept)


def remove_server_state(original: bytes | None, server_id: str, label: str) -> bytes | None:
    """Remove one server's persisted automation; preserve all other entries."""
    if original is None:
        return None
    try:
        data = json.loads(original)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ConfigurationError(f"{label}: некорректный JSON; файлы не изменены: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
        raise ConfigurationError(f"{label}: ожидается объект с полем servers")
    if server_id not in data["servers"]:
        return original
    data["servers"].pop(server_id)
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def remove_server_permissions(
    data: dict, server_id: str, *, default_changed: bool
) -> bool:
    """Prevent an old default-server role from migrating to the new default."""
    changed = False
    for user in data["users"].values():
        if not isinstance(user, dict):
            raise ConfigurationError("users.json: неверная запись пользователя")
        server_policies = user.get("servers")
        if isinstance(server_policies, dict):
            if server_id in server_policies:
                del server_policies[server_id]
                changed = True
        elif default_changed:
            # Legacy role/allow/deny implicitly apply to DEFAULT_SERVER_ID.
            # Remove them rather than silently granting those rights on
            # another Minecraft server when the default changes.
            if any(key in user for key in ("role", "allow", "deny")):
                for key in ("role", "allow", "deny"):
                    user.pop(key, None)
                user["servers"] = {}
                changed = True
    return changed


def remove_registered_server(
    project_dir: Path,
    servers_path: Path,
    config_original: bytes | None,
    document: dict | list,
    servers: list[dict],
    env: dict[str, str],
) -> bool:
    if len(servers) < 2:
        print(
            "❌ Нельзя удалить последний сервер: пустой servers.json "
            "включает устаревший fallback из .env."
        )
        return False

    print("\n🗑 Удаление сервера из Telegram-бота")
    for index, server in enumerate(servers, 1):
        transport = "SSH" if server.get("type") == "ssh" else "локальный"
        print(f"  {index}. {server.get('name') or server.get('id')} "
              f"({server.get('id')}) · {transport}")
    while True:
        selection = input("Номер сервера (0 — назад): ").strip()
        if selection == "0":
            return False
        if selection.isascii() and selection.isdecimal() and 1 <= int(selection) <= len(servers):
            break
        print("Введи номер сервера из списка.")

    server = servers[int(selection) - 1]
    server_id = server.get("id")
    if not isinstance(server_id, str) or not SERVER_ID_PATTERN.fullmatch(server_id):
        raise ConfigurationError("Невозможно безопасно удалить сервер с некорректным ID")
    if sum(entry.get("id") == server_id for entry in servers) != 1:
        raise ConfigurationError("В servers.json обнаружен повторяющийся ID сервера")

    remaining = [item for item in servers if item.get("id") != server_id]
    existing_ids = {item["id"] for item in remaining}
    env_path = project_dir / ".env"
    original_env = read_bytes(env_path)
    if original_env is None:
        raise ConfigurationError(".env не найден: безопасное удаление отменено")
    try:
        env_text = original_env.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(".env не в UTF-8; файлы не изменены") from exc

    # Match config.py's fallback chain using the .env used by the service.
    old_default = (
        env.get("DEFAULT_SERVER_ID") or env.get("SERVER_ID") or "minecraft"
    ).strip()
    new_default = old_default
    if old_default not in existing_ids:
        suggestion = remaining[0]["id"]
        new_default = ask_text("Новый сервер по умолчанию (ID)", suggestion)
        if new_default not in existing_ids:
            raise ConfigurationError("Новый DEFAULT_SERVER_ID должен быть одним из оставшихся ID")

    # A shell-injected DEFAULT_SERVER_ID would override the .env when used
    # by a service, so it must not point at the removed server.
    if os.environ.get("DEFAULT_SERVER_ID") == server_id:
        raise ConfigurationError(
            "DEFAULT_SERVER_ID задан во внешнем окружении и указывает на удаляемый сервер. "
            "Измени переменную в конфигурации службы перед удалением."
        )

    key = server.get("rcon_password_env", "")
    if key and (not isinstance(key, str) or not VALID_ENV_NAME.fullmatch(key)):
        raise ConfigurationError("Некорректная переменная RCON-пароля; удаление отменено")
    shared_key = bool(key and any(item.get("rcon_password_env") == key for item in remaining))
    next_env = env_text
    if key and not shared_key:
        next_env = remove_env_key(next_env, key)
    if new_default != old_default:
        next_env = update_env(next_env, "DEFAULT_SERVER_ID", new_default)

    users_path = configured_path(project_dir, env, "ACCESS_USERS_FILE", "users.json")
    stop_path = configured_path(project_dir, env, "AUTO_STOP_STATE_FILE", "auto_stop_state.json")
    backup_path = configured_path(project_dir, env, "AUTO_BACKUP_STATE_FILE", "auto_backup_state.json")
    tracked_paths = [servers_path, env_path, users_path, stop_path, backup_path]
    if len(set(tracked_paths)) != len(tracked_paths):
        raise ConfigurationError("Два конфигурационных файла указывают на один путь. Удаление отменено.")

    users_data, users_original = load_users(users_path)
    permissions_changed = False
    if users_original is not None:
        permissions_changed = remove_server_permissions(
            users_data, server_id, default_changed=new_default != old_default
        )
    stop_original = read_bytes(stop_path)
    stop_new = remove_server_state(stop_original, server_id, "auto-stop")
    backup_original = read_bytes(backup_path)
    backup_new = remove_server_state(backup_original, server_id, "auto-backup")

    print("\n── Проверка перед удалением ──")
    print(f"Сервер:  {server.get('name') or server_id} ({server_id})")
    print(f"Тип:     {'SSH' if server.get('type') == 'ssh' else 'локальный'}")
    print(f"Папка:   {server.get('server_dir')}")
    print("Удаляем: запись из servers.json, права и состояния автозадач этого сервера.")
    if key:
        print("RCON-переменная в .env: " +
              ("оставляется (используется другим сервером)" if shared_key else "удаляется"))
    if new_default != old_default:
        print(f"DEFAULT_SERVER_ID: {old_default} → {new_default}")
        print("Старые права пользователей для удалённого default-сервера не перейдут к новому.")
    if permissions_changed:
        print("Настройки users.json для выбранного сервера будут обновлены.")
    print("✅ НЕ удаляем: папку Minecraft, файлы мира, плагины/моды, "
          "ZIP-архивы, логи и SSH-ключи.")
    print("⚠️ Работающий Minecraft НЕ будет остановлен. "
          "Перед очисткой автозадач останови службу Telegram-бота.")
    print("Все изменённые конфиги будут скопированы в config_backups/.")
    confirmation = input(f"Для подтверждения введи ID «{server_id}» (0 — отмена): ").strip()
    if confirmation != server_id:
        print("Отменено. Файлы не изменены.")
        return False

    # Keep list/dict JSON formats intact. Do not alter remaining entries.
    servers[:] = remaining
    config_new = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    changes: list[tuple[Path, bytes | None, bytes, bool]] = []
    if original_env != next_env.encode("utf-8"):
        changes.append((env_path, original_env, next_env.encode("utf-8"), True))
    if permissions_changed:
        changes.append((
            users_path, users_original,
            (json.dumps(users_data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            True,
        ))
    labels = {}
    for path, old, new, label in (
        (stop_path, stop_original, stop_new, "auto_stop_state.json"),
        (backup_path, backup_original, backup_new, "auto_backup_state.json"),
    ):
        if old is not None and new is not None and old != new:
            changes.append((path, old, new, True))
            labels[path] = label
    # servers.json is written last so an earlier write error can roll back.
    changes.append((servers_path, config_original, config_new, False))
    copies = apply_files(
        changes, backup_dir=project_dir / "config_backups", backup_labels=labels
    )
    print(f"\n✅ Сервер {server_id} отключён от бота. Minecraft-файлы не затронуты.")
    for copy in copies:
        print(f"   Резервная копия: {copy}")
    print("Чтобы применить изменения: sudo systemctl start telegram-minecraft-manager.service")
    return True
