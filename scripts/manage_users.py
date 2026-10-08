#!/usr/bin/env python3
"""Interactive, dependency-free editor for users.json.

Run from anywhere: python3 scripts/manage_users.py
Loads paths from the project's .env, without importing bot configuration or
requiring a running Telegram bot. Never writes malformed JSON.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]

# Keep in sync with services/access_control.py. Host system.view is intentionally
# absent: only OWNER_IDS and legacy ADMIN_IDS can access global host data.
PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("server.status", "Статус сервера"),
    ("server.start", "Запуск сервера"),
    ("server.stop", "Остановка сервера"),
    ("server.restart", "Перезапуск сервера"),
    ("server.autostop", "Настройка автостопа"),
    ("events.view", "История событий"),
    ("logs.view", "Логи запуска"),
    ("console.use", "RCON-консоль"),
    ("mods.view", "Просмотр модов"),
    ("mods.upload", "Загрузка модов"),
    ("mods.delete", "Удаление модов"),
    ("backup.create", "Ручной бэкап"),
    ("backup.view", "Просмотр бэкапов"),
    ("backup.schedule", "Расписание автобэкапа"),
)
PERMISSION_IDS = frozenset(key for key, _ in PERMISSIONS)
ROLE_RIGHTS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"server.status", "mods.view"}),
    "starter": frozenset({"server.status", "server.start"}),
    "operator": frozenset({
        "server.status", "server.start", "server.stop", "server.restart",
        "server.autostop", "events.view", "mods.view",
    }),
    "admin": PERMISSION_IDS,
    "custom": frozenset(),
}


class ConfigurationError(Exception):
    """A configuration file is unsafe to edit."""


def read_simple_env(project_dir: Path) -> dict[str, str]:
    """Read plain and quoted .env assignments needed for the editor."""
    path = project_dir / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value.startswith(("'", '"')) and value.endswith(value[:1]):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def configured_path(project_dir: Path, env: dict[str, str], name: str, fallback: str) -> Path:
    value = os.environ.get(name) or env.get(name) or fallback
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_dir / path).resolve()


def load_servers(path: Path) -> list[tuple[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Не удалось прочитать servers.json ({path}): {exc}") from exc
    raw = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        raise ConfigurationError("servers.json должен содержать непустой список servers")
    result: list[tuple[str, str]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ConfigurationError("В servers.json обнаружен сервер без id")
        sid = item["id"].strip()
        if not sid or sid in seen:
            raise ConfigurationError(f"Повторяющийся или пустой id сервера: {sid!r}")
        seen.add(sid)
        result.append((sid, str(item.get("name") or sid)))
    return result


def load_users(path: Path) -> tuple[dict[str, Any], bytes | None]:
    try:
        source = path.read_bytes()
    except FileNotFoundError:
        return {"users": {}}, None
    except OSError as exc:
        raise ConfigurationError(f"Не удалось прочитать {path}: {exc}") from exc
    try:
        result = json.loads(source)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigurationError(
            f"{path} содержит ошибку JSON. Исправь её прежде, чем запускать редактор: {exc}"
        ) from exc
    if not isinstance(result, dict) or not isinstance(result.get("users"), dict):
        raise ConfigurationError(f"{path} должен содержать объект 'users'")
    return result, source


def atomic_save(path: Path, data: dict[str, Any], original: bytes | None) -> Path | None:
    """Refuse concurrent edits; back up existing file; atomically replace it."""
    formatted = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    json.loads(formatted)  # Validate before touching disk.
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        current = None
    if current != original:
        raise ConfigurationError(
            f"{path} изменён другой программой. Перезапусти редактор, чтобы не потерять изменения."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if original is not None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)

    file_mode = stat.S_IMODE(path.stat().st_mode) if original is not None else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            os.fchmod(target.fileno(), file_mode)
            target.write(formatted)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def migrate_legacy_user(user: dict[str, Any], default_id: str) -> dict[str, Any]:
    """Move old global role into the default server without widening access."""
    if isinstance(user.get("servers"), dict):
        return user["servers"]
    legacy = {
        "role": user.pop("role", "custom"),
        "allow": user.pop("allow", []),
        "deny": user.pop("deny", []),
    }
    user["servers"] = {default_id: legacy}
    return user["servers"]


def effective_permissions(policy: dict[str, Any] | None) -> set[str]:
    if not policy:
        return set()
    role = policy.get("role", "custom")
    rights = set(ROLE_RIGHTS.get(role, frozenset()))
    rights.update(permission for permission in policy.get("allow", []) if permission in PERMISSION_IDS)
    rights.difference_update(policy.get("deny", []))
    return rights & PERMISSION_IDS


def apply_policy(
    config: dict[str, Any], user_id: str, server_id: str,
    policy: dict[str, Any] | None, default_id: str,
) -> bool:
    """Update one server without modifying policies on other servers."""
    user = config["users"][user_id]
    existing = user.get("servers")
    # Legacy policies grant access only to the default server; preserve that.
    if not isinstance(existing, dict):
        existing = migrate_legacy_user(user, default_id)
    if policy is None:
        return existing.pop(server_id, None) is not None
    if existing.get(server_id) == policy:
        return False
    existing[server_id] = policy
    return True


def selection(prompt: str, valid: set[str]) -> str:
    while True:
        answer = input(prompt).strip().lower()
        if answer in valid:
            return answer
        print("Неверный выбор. Введи номер из меню.")


def confirmation(prompt: str) -> bool:
    return input(f"{prompt} [да/нет]: ").strip().lower() in {"да", "д", "yes", "y"}


def edit_custom(existing: dict[str, Any] | None = None) -> dict[str, Any] | None:
    rights = effective_permissions(existing)
    while True:
        print("\n   Выбери права: номер переключает галочку; s — сохранить; 0 — отменить")
        for index, (key, label) in enumerate(PERMISSIONS, 1):
            print(f"   {index:2}. [{'✓' if key in rights else ' '}] {label}")
        command = input("   Выбор: ").strip().lower()
        if command == "0":
            return None
        if command == "s":
            return {"role": "custom", "allow": sorted(rights), "deny": []}
        if command.isdigit() and 1 <= int(command) <= len(PERMISSIONS):
            key = PERMISSIONS[int(command) - 1][0]
            if key in rights:
                rights.remove(key)
            else:
                rights.add(key)
        else:
            print("   Введи номер права, s или 0.")


def choose_policy(existing: dict[str, Any] | None) -> tuple[bool, dict[str, Any] | None]:
    print("\n   1. 👁 Только просмотр (статус и моды)")
    print("   2. ▶ Просмотр и запуск")
    print("   3. 🎮 Оператор (запуск, остановка, рестарт, автостоп)")
    print("   4. 🔑 Полный доступ к серверу (кроме системной информации хоста)")
    print("   5. ⚙ Выбрать права по отдельности")
    print("   6. ⛔ Убрать доступ к серверу")
    print("   0. Назад")
    answer = selection("   Выбор: ", set("0123456"))
    if answer == "0":
        return False, None
    if answer == "6":
        return True, None
    if answer == "5":
        value = edit_custom(existing)
        return (value is not None), value
    roles = {"1": "viewer", "2": "starter", "3": "operator", "4": "admin"}
    role = roles[answer]
    if role == "starter":
        return True, {"role": "custom", "allow": sorted(ROLE_RIGHTS[role]), "deny": []}
    return True, {"role": role, "allow": [], "deny": []}


class UserEditor:
    def __init__(
        self, users_path: Path, servers: list[tuple[str, str]],
        default_id: str, owner_ids: set[int] | None = None,
    ) -> None:
        self.path = users_path
        self.servers = servers
        self.default_id = default_id
        self.owner_ids = owner_ids or set()
        self.config, self.original = load_users(users_path)

    def save(self) -> None:
        backup = atomic_save(self.path, self.config, self.original)
        self.original = self.path.read_bytes()
        print(f"✅ Сохранено: {self.path}")
        if backup is not None:
            print(f"   Резервная копия: {backup}")
        print("   Чтобы бот подхватил изменения, перезапусти его службу.")

    def show_users(self) -> list[str]:
        ids = list(self.config["users"])
        print("\n═══ Пользователи ═══")
        if not ids:
            print("   Пока никого нет.")
        for i, uid in enumerate(ids, 1):
            user = self.config["users"][uid]
            name = user.get("name", "") if isinstance(user, dict) else "ошибочная запись"
            entries = user.get("servers", {}) if isinstance(user, dict) else {}
            count = len(entries) if isinstance(entries, dict) else 1
            print(f"   {i}. {name or 'Без имени'} ({uid}) — серверов: {count}")
        print("   a. ➕ Добавить пользователя")
        print("   0. Выйти")
        return ids

    def add_user(self) -> None:
        raw = input("\nTelegram ID нового пользователя (0 — назад): ").strip()
        if raw == "0":
            return
        if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
            print("❌ Нужен положительный числовой Telegram ID.")
            return
        uid = str(int(raw))
        if int(uid) in self.owner_ids:
            print("⚠️ Это владелец из .env, у него и так полный доступ.")
            return
        if uid in self.config["users"]:
            print("❌ Пользователь уже есть. Выбери его из списка.")
            return
        name = input("Имя для списка: ").strip() or "User"
        print("Выбери первый сервер и уровень доступа:")
        for i, (sid, label) in enumerate(self.servers, 1):
            print(f"   {i}. {label} ({sid})")
        print("   0. Отмена")
        answer = selection("   Сервер: ", {str(i) for i in range(len(self.servers) + 1)})
        if answer == "0":
            return
        sid = self.servers[int(answer) - 1][0]
        apply, policy = choose_policy(None)
        if not apply or policy is None:
            print("Новый пользователь не создан.")
            return
        self.config["users"][uid] = {"name": name, "servers": {sid: policy}}
        self.save()

    def edit_user(self, uid: str) -> None:
        user = self.config["users"].get(uid)
        if not isinstance(user, dict):
            print("❌ Некорректная запись пользователя.")
            return
        if uid.isdigit() and int(uid) in self.owner_ids:
            print("⚠️ Этот ID в OWNER_IDS/ADMIN_IDS: права через users.json ограничить нельзя.")
            return
        while True:
            print(f"\n═══ {user.get('name', uid)} ({uid}) ═══")
            raw_policies = user.get("servers", {})
            for i, (sid, label) in enumerate(self.servers, 1):
                policy = (raw_policies.get(sid) if isinstance(raw_policies, dict)
                          else (user if sid == self.default_id else None))
                if isinstance(policy, dict):
                    rights = effective_permissions(policy)
                    display = f"{policy.get('role', 'custom')}, {len(rights)} прав" if rights else "нет разрешений"
                else:
                    display = "нет доступа"
                print(f"   {i}. {label} ({sid}) — {display}")
            print("   n. Переименовать")
            print("   d. Удалить пользователя")
            print("   0. Назад")
            answer = selection(
                "   Выбор: ",
                {str(i) for i in range(len(self.servers) + 1)} | {"n", "d"},
            )
            if answer == "0":
                return
            if answer == "n":
                name = input("   Новое имя: ").strip()
                if name and name != user.get("name"):
                    user["name"] = name
                    self.save()
                continue
            if answer == "d":
                if confirmation(f"Удалить {uid} и все его разрешения?"):
                    del self.config["users"][uid]
                    self.save()
                    return
                continue
            sid = self.servers[int(answer) - 1][0]
            existing = raw_policies.get(sid) if isinstance(raw_policies, dict) else (
                user if sid == self.default_id else None
            )
            apply, policy = choose_policy(existing if isinstance(existing, dict) else None)
            if apply:
                if apply_policy(self.config, uid, sid, policy, self.default_id):
                    self.save()
                else:
                    print("   Ничего не изменилось.")

    def run(self) -> None:
        print("🎮 Управление доступом Telegram Minecraft Server Manager")
        print(f"Серверов в конфигурации: {len(self.servers)}")
        print(f"Файл пользователей: {self.path}")
        while True:
            ids = self.show_users()
            answer = selection("Выбор: ", {str(i) for i in range(len(ids) + 1)} | {"a"})
            if answer == "0":
                return
            if answer == "a":
                self.add_user()
            else:
                self.edit_user(ids[int(answer) - 1])


def main() -> int:
    parser = argparse.ArgumentParser(description="Интерактивное управление users.json")
    parser.add_argument("--users-file", type=Path, help="Путь к users.json вместо настроек .env")
    parser.add_argument("--servers-file", type=Path, help="Путь к servers.json вместо настроек .env")
    args = parser.parse_args()

    try:
        env = read_simple_env(PROJECT_DIR)
        users_path = args.users_file or configured_path(
            PROJECT_DIR, env, "ACCESS_USERS_FILE", "users.json"
        )
        servers_path = args.servers_file or configured_path(
            PROJECT_DIR, env, "MINECRAFT_SERVERS_FILE", "servers.json"
        )
        servers = load_servers(servers_path)
        configured_default = os.environ.get("DEFAULT_SERVER_ID") or env.get("DEFAULT_SERVER_ID")
        default_id = configured_default or servers[0][0]
        if default_id not in {sid for sid, _ in servers}:
            raise ConfigurationError(
                f"DEFAULT_SERVER_ID={default_id} отсутствует в {servers_path}"
            )
        owner_raw = ",".join(
            [os.environ.get(name, env.get(name, "")) for name in ("OWNER_IDS", "ADMIN_IDS")]
        )
        owners = {int(v.strip()) for v in owner_raw.split(",") if v.strip().isdigit()}
        editor = UserEditor(Path(users_path).expanduser().resolve(), servers, default_id, owners)
        editor.run()
        print("\nГотово. Для применения изменений после редактирования запусти:")
        print("  sudo systemctl restart telegram-minecraft-manager.service")
        return 0
    except (ConfigurationError, OSError) as exc:
        print(f"\n❌ {exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nВыход.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
