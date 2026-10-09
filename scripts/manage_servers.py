#!/usr/bin/env python3
"""Interactive Minecraft server registration and removal without hand-editing JSON.

Only registers an EXISTING Minecraft installation; does not install Minecraft.
Updates servers.json, server.properties and the bot's private .env together.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import socket
import stat
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
from scripts.manage_users import ConfigurationError, configured_path, read_simple_env  # noqa: E402
from scripts.config_backups import save_config_backup  # noqa: E402
from services.server_software import choose_software, SOFTWARE_NAMES  # noqa: E402

SERVER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VALID_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
LAUNCHERS = ("start-server.sh", "start.sh", "run.sh")


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def parse_properties(contents: str) -> dict[str, str]:
    """Read the simple key=value options we update, without interpreting comments."""
    result: dict[str, str] = {}
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def update_properties(contents: str, settings: dict[str, str]) -> str:
    """Replace server settings in place; do not erase unrelated Minecraft options."""
    lines = contents.splitlines()
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "!")) and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in settings:
                if key not in seen:
                    updated.append(f"{key}={settings[key]}")
                    seen.add(key)
                continue  # Remove duplicate assignments to avoid ambiguity.
        updated.append(line)
    for key, value in settings.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    return "\n".join(updated) + "\n"


def update_env(contents: str, key: str, value: str) -> str:
    """Create or replace one .env assignment; never print password."""
    if not VALID_ENV_NAME.fullmatch(key) or "\n" in value or "\r" in value:
        raise ConfigurationError("Недопустимый ключ или пароль RCON")
    # json-style double quotes are accepted by python-dotenv, including '#'.
    encoded = json.dumps(value, ensure_ascii=False)
    assignment = f"{key}={encoded}"
    lines = contents.splitlines()
    result: list[str] = []
    found = False
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) == key:
            if not found:
                result.append(assignment)
                found = True
            continue
        result.append(line)
    if not found:
        result.append(assignment)
    return "\n".join(result) + "\n"


def load_server_config(raw: bytes | None) -> tuple[dict | list, list[dict]]:
    if raw is None:
        return {"servers": []}, []
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"Повреждённый servers.json. Файл не изменён: {exc}") from exc
    servers = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(servers, list) or not all(isinstance(x, dict) for x in servers):
        raise ConfigurationError("servers.json должен содержать список серверов")
    return data, servers


def validate_port(value: str | int, label: str) -> int:
    try:
        port = int(value)
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(f"{label}: требуется число от 1 до 65535") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"{label}: порт должен быть от 1 до 65535")
    return port


def existing_ports(servers: list[dict]) -> set[int]:
    taken = set()
    for server in servers:
        # Ports on remote hosts do not conflict with local Minecraft instances.
        if server.get("type", "local") == "ssh":
            continue
        root = Path(str(server.get("server_dir", "/nonexistent"))).expanduser()
        props_path = root / "server.properties"
        try:
            props = parse_properties(props_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError):
            props = {}
        taken.add(validate_port(props.get("server-port", 25565), "Minecraft server-port"))
        taken.add(validate_port(server.get("rcon_port", props.get("rcon.port", 25575)), "RCON port"))
        query_port = props.get("query.port")
        if props.get("enable-query", "false").lower() == "true" and query_port:
            taken.add(validate_port(query_port, "Query port"))
    return taken


def available_port(port: int, *, host: str = "127.0.0.1") -> bool:
    # TCP binding catches most active local game/RCON listeners.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def suggest_port(start: int, reserved: set[int]) -> int:
    for port in range(start, 65536):
        if port not in reserved and available_port(port):
            return port
    raise ConfigurationError("Не найден свободный порт, введи другой вручную")


def ask_text(prompt: str, default: str = "") -> str:
    value = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return value or default


def ask_port(label: str, default: int, reserved: set[int]) -> int:
    while True:
        answer = ask_text(label, str(default))
        try:
            port = validate_port(answer, label)
            if port in reserved:
                print("❌ Этот порт уже используется другим настроенным сервером.")
            elif not available_port(port):
                print("❌ Порт занят работающим приложением.")
            else:
                return port
        except ConfigurationError as exc:
            print(f"❌ {exc}")


def ask_yes_no(prompt: str, *, default: bool = True) -> bool:
    hint = "Д/н" if default else "д/Н"
    while True:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "д", "да"}:
            return True
        if answer in {"n", "no", "н", "нет"}:
            return False
        print("Введи да или нет.")


def safe_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as file:
            os.fchmod(file.fileno(), mode)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_files(
    files: list[tuple[Path, bytes | None, bytes, bool]],
    backup_dir: Path | None = None,
    *,
    server_id: str | None = None,
    backup_labels: dict[Path, str] | None = None,
) -> list[Path]:
    """Check originals, create backups and atomically replace staged files.

    servers.json is provided last; failure rolls back earlier changes.
    A true fourth element marks .env containing credentials (mode 0600).
    """
    changed = [(path, old, new, secret) for path, old, new, secret in files if old != new]
    for path, old, _, _ in changed:
        if read_bytes(path) != old:
            raise ConfigurationError(f"{path} изменился во время настройки; запись отменена")

    backups: list[Path] = []
    backup_dir = backup_dir or PROJECT_DIR / "config_backups"
    for path, old, _, secret in changed:
        if old is None:
            continue
        try:
            backup = save_config_backup(
                path, old, backup_dir, server_id=server_id,
                backup_label=(backup_labels or {}).get(path),
            )
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"Не удалось сохранить резервную копию {path}: {exc}") from exc
        backups.append(backup)

    written: list[tuple[Path, bytes | None, int]] = []
    try:
        for path, old, new, secret in changed:
            mode = 0o600 if secret else (
                stat.S_IMODE(path.stat().st_mode) if old is not None else 0o644
            )
            safe_write(path, new, mode)
            written.append((path, old, mode))
    except OSError as exc:
        for path, old, mode in reversed(written):
            try:
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    safe_write(path, old, mode)
            except OSError:
                print(f"⚠️ Не удалось откатить {path}; восстанови .bak вручную.")
        raise ConfigurationError(f"Запись не завершена (выполнен откат): {exc}") from exc
    return backups


def find_launcher(directory: Path) -> str:
    for name in LAUNCHERS:
        if (directory / name).is_file():
            return "./" + name
    return "./start-server.sh"


def check_launcher(directory: Path, command: str) -> None:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ConfigurationError(f"Некорректная команда запуска: {exc}") from exc
    if not argv:
        raise ConfigurationError("Команда запуска не может быть пустой")
    executable = argv[0]
    if executable.startswith("./") or executable.startswith("../"):
        if not (directory / executable).is_file():
            raise ConfigurationError(f"Скрипт запуска не найден: {directory / executable}")
    elif executable in {"bash", "sh"} and len(argv) > 1 and not Path(argv[1]).is_absolute():
        if not (directory / argv[1]).is_file():
            raise ConfigurationError(f"Скрипт запуска не найден: {directory / argv[1]}")


def build_entry(
    sid: str, name: str, directory: Path, command: str,
    rcon_port: int, env_key: str, world_name: str, server_software: str = "mods",
) -> dict:
    result = {
        "id": sid, "name": name,
        "server_dir": str(directory), "start_command": command,
        "rcon_host": "127.0.0.1", "rcon_port": rcon_port,
        "rcon_password_env": env_key,
        "server_software": server_software,
        "auto_stop_seconds": 0,
        "backup_retention_max_count": 0,
        "backup_retention_max_gb": 0,
    }
    if world_name != "world":
        result["world_dir"] = str(directory / world_name)
    return result


def edit_existing_software(
    project_dir: Path, servers_path: Path,
    config_original: bytes | None, document: dict | list, servers: list[dict],
) -> bool:
    """Change only the software profile of an already registered server."""
    if not servers:
        print("❌ Сначала добавь хотя бы один Minecraft-сервер.")
        return False
    print("\n🎮 Выбери сервер для изменения типа:")
    for i, entry in enumerate(servers, 1):
        label = entry.get("name") or entry.get("id")
        software = entry.get("server_software", "mods")
        print(f"  {i}. {label} ({entry.get('id')}) · сейчас {software}")
    while True:
        answer = input("Номер сервера (0 — назад): ").strip()
        if answer == "0":
            return False
        if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(servers):
            break
        print("Введи номер из списка.")

    selected = servers[int(answer) - 1]
    directory = str(selected["server_dir"])
    if selected.get("type", "local") == "ssh":
        from services.remote_ssh import SSHRemote, SSHSettings
        import asyncio

        params = selected.get("ssh") or {}
        try:
            settings = SSHSettings(
                host=params["host"], user=params["user"],
                port=int(params.get("port", 22)),
                key_file=params["key_file"], known_hosts=params["known_hosts"],
            )
            remote = SSHRemote(settings, {"server_dir": directory})
            info = asyncio.run(remote.request("probe", timeout=40))
        except Exception as exc:
            raise ConfigurationError(f"Не удалось определить папки по SSH: {exc}") from exc
        has_mods = bool(info.get("has_mods", False))
        has_plugins = bool(info.get("has_plugins", False))
    else:
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise ConfigurationError(f"Папка Minecraft не найдена: {root}")
        has_mods = (root / "mods").is_dir()
        has_plugins = (root / "plugins").is_dir()

    software = choose_software(has_mods, has_plugins)
    previous = str(selected.get("server_software", "mods"))
    if software == previous:
        print("ℹ️ Тип уже установлен, файл не изменён.")
        return False
    print(f"Изменить {selected.get('id')}: {previous} → {software}?")
    if not ask_yes_no("Сохранить новое меню дополнений?", default=False):
        print("Отменено.")
        return False
    selected["server_software"] = software
    new_content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    copies = apply_files(
        [(servers_path, config_original, new_content, False)],
        backup_dir=project_dir / "config_backups",
    )
    print(f"✅ Тип сервера обновлён: {SOFTWARE_NAMES[software]}")
    for copy in copies:
        print(f"   Резервная копия: {copy}")
    print("Перезапусти бота: sudo systemctl restart telegram-minecraft-manager.service")
    return True


def run_wizard(project_dir: Path = PROJECT_DIR) -> bool:
    """Ask questions and apply changes only after a preview and confirmation."""
    env = read_simple_env(project_dir)
    servers_path = configured_path(project_dir, env, "MINECRAFT_SERVERS_FILE", "servers.json")
    env_path = project_dir / ".env"
    config_original = read_bytes(servers_path)
    document, servers = load_server_config(config_original)

    print("🎮 Добавление Minecraft-сервера в Telegram Bot Manager")
    print(f"Конфигурация: {servers_path}")
    print("Уже подключены: " + (", ".join(s.get("id", "?") for s in servers) or "нет"))
    print("Подключение существующей установки Minecraft, без скачивания файлов.")
    print("\n  1. 💻 Локальный сервер (папка на этом ПК)")
    print("  2. 🌐 Удалённый Linux-сервер (SSH)")
    print("  3. 🛠 Изменить тип уже подключённого сервера")
    print("  4. 🗑 Отключить сервер от бота (без удаления мира)")
    while True:
        mode = ask_text("Режим", "1")
        if mode in {"1", "2", "3", "4"}:
            break
        print("Выбери 1, 2, 3 или 4.")
    if mode == "4":
        from scripts.remove_server import remove_registered_server
        return remove_registered_server(
            project_dir, servers_path, config_original, document, servers, env
        )
    if mode == "3":
        return edit_existing_software(project_dir, servers_path, config_original, document, servers)
    if mode == "2":
        from scripts.manage_remote_servers import run_remote_wizard

        return run_remote_wizard(project_dir, servers_path, config_original, document, servers)

    raw_dir = ask_text("\nПолный путь к папке Minecraft-сервера (0 — выход)")
    if raw_dir == "0":
        return False
    directory = Path(raw_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ConfigurationError(f"Папка сервера не найдена: {directory}")
    if any(s.get("type", "local") != "ssh" and Path(str(s.get("server_dir", ""))).expanduser().resolve() == directory for s in servers):
        raise ConfigurationError("Этот каталог уже подключён в servers.json")

    default_id = re.sub(r"[^a-z0-9_-]+", "-", directory.name.lower()).strip("-_")[:32]
    sid = ask_text("ID сервера (латиница, без пробелов)", default_id)
    if not SERVER_ID_PATTERN.fullmatch(sid):
        raise ConfigurationError("ID: 1–32 символа, a-z/0-9/_/-, начинается с буквы или цифры")
    if any(s.get("id") == sid for s in servers):
        raise ConfigurationError(f"Сервер {sid!r} уже существует")
    name = ask_text("Отображаемое название", directory.name.replace("-", " ").title())
    if not name:
        raise ConfigurationError("Название не может быть пустым")

    default_command = find_launcher(directory)
    start_command = ask_text("Команда запуска Minecraft", default_command)
    check_launcher(directory, start_command)
    server_software = choose_software(
        (directory / "mods").is_dir(), (directory / "plugins").is_dir()
    )
    props_path = directory / "server.properties"
    props_original = read_bytes(props_path)
    try:
        original_text = props_original.decode("utf-8") if props_original else ""
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"server.properties не UTF-8: {exc}") from exc
    props = parse_properties(original_text)
    reserved = existing_ports(servers)
    used_new = set(reserved)
    default_game_port = validate_port(props.get("server-port", 25565), "server-port")
    if default_game_port in reserved or not available_port(default_game_port):
        default_game_port = suggest_port(25565, reserved)
    game_port = ask_port("Порт подключения Minecraft", default_game_port, used_new)
    used_new.add(game_port)

    default_rcon_port = validate_port(props.get("rcon.port", 25575), "rcon.port")
    if default_rcon_port in used_new or not available_port(default_rcon_port):
        default_rcon_port = suggest_port(25575, used_new)
    rcon_port = ask_port("Локальный порт RCON", default_rcon_port, used_new)

    password = props.get("rcon.password", "")
    if password and ask_yes_no("Использовать существующий RCON-пароль из server.properties?"):
        pass
    else:
        if not ask_yes_no("Сгенерировать надёжный пароль RCON автоматически?"):
            import getpass

            password = getpass.getpass("Пароль RCON (не отображается): ").strip()
            if not password or any(c in password for c in ("\n", "\r")):
                raise ConfigurationError("Пароль RCON должен быть непустым")
        else:
            password = secrets.token_hex(24)

    key = sid.upper().replace("-", "_") + "_RCON_PASSWORD"
    if not VALID_ENV_NAME.fullmatch(key):
        raise ConfigurationError("Невозможно сформировать переменную окружения для RCON")
    if any(s.get("rcon_password_env") == key for s in servers):
        raise ConfigurationError(f"Переменная окружения {key} уже используется")
    current_env = read_bytes(env_path)
    if current_env is None:
        raise ConfigurationError(f"Не найден .env бота: {env_path}; сначала настрой бота")
    try:
        env_text = current_env.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f".env не в UTF-8: {exc}") from exc
    env_new = update_env(env_text, key, password).encode("utf-8")

    world_name = props.get("level-name", "world").strip() or "world"
    if world_name in {".", ".."} or Path(world_name).is_absolute() or ".." in Path(world_name).parts:
        raise ConfigurationError("Недопустимый level-name в server.properties")
    props_new = update_properties(original_text, {
        "server-port": str(game_port),
        "enable-rcon": "true",
        "rcon.port": str(rcon_port),
        "rcon.password": password,
    }).encode("utf-8")
    entry = build_entry(sid, name, directory, start_command, rcon_port, key, world_name, server_software)
    servers.append(entry)

    print("\n── Проверка перед сохранением ──")
    print(f"Название:       {name}")
    print(f"ID:             {sid}")
    print(f"Папка:          {directory}")
    print(f"Запуск:         {start_command}")
    print(f"Тип Minecraft:  {SOFTWARE_NAMES[server_software]}")
    print(f"Minecraft:      {game_port}/TCP")
    print(f"RCON:           127.0.0.1:{rcon_port}")
    print(f"RCON-пароль:    скрыт (ключ {key} в .env)")
    print(f"Мир:            {world_name}")
    print("Автостоп и удаление старых автобэкапов: отключены")
    print("Изменяем: servers.json, server.properties, .env (с резервными копиями)")
    if not (directory / "eula.txt").exists():
        print("⚠️ eula.txt не найден: сервер может потребовать принятия EULA при первом запуске.")
    if not ask_yes_no("Добавить сервер и сохранить файлы?", default=False):
        print("Отменено. Файлы не изменены.")
        return False

    config_new = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    backups = apply_files([
        (props_path, props_original, props_new, False),
        (env_path, current_env, env_new, True),
        (servers_path, config_original, config_new, False),
    ], backup_dir=project_dir / "config_backups", server_id=sid)
    print(f"\n✅ Сервер «{name}» подключён.")
    for backup in backups:
        print(f"   Резервная копия: {backup}")
    print("Для обновления бота: sudo systemctl restart telegram-minecraft-manager.service")
    print(f"Для подключения к Minecraft: IP_СЕРВЕРА:{game_port}")
    return True


def main() -> int:
    try:
        run_wizard()
        return 0
    except ConfigurationError as exc:
        print(f"\n❌ {exc}")
        return 1
    except (OSError, KeyboardInterrupt, EOFError) as exc:
        print(f"\nВыход / ошибка ввода: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
