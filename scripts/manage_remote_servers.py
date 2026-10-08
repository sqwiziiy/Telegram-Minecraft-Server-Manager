"""Interactive connection wizard for Minecraft on another Linux machine via SSH."""
from __future__ import annotations

import asyncio
import getpass
import json
import posixpath
import re
import secrets
from pathlib import Path

from scripts.config_backups import save_config_backup
from scripts.manage_users import ConfigurationError, read_simple_env
from scripts.manage_servers import (
    PROJECT_DIR,
    SERVER_ID_PATTERN,
    apply_files,
    ask_text,
    ask_yes_no,
    load_server_config,
    parse_properties,
    read_bytes,
    update_env,
    update_properties,
    validate_port,
)
from services.remote_ssh import SSHRemote, SSHSettings


async def _remote_setup(project_dir: Path, servers_path: Path, raw_config: bytes | None,
                        document, servers) -> bool:
    print("\n🌐 Подключение удалённого Linux Minecraft-сервера (SSH)")
    print("На удалённом компьютере нужны SSH, Python 3.10+ и установленный Minecraft.")
    print("Хост должен быть известен в ~/.ssh/known_hosts, доступ — через SSH-ключ.")

    host = ask_text("IP или домен удалённого компьютера (0 — отмена)")
    if host == "0":
        return False
    user = ask_text("Пользователь SSH")
    ssh_port = validate_port(ask_text("Порт SSH", "22"), "SSH port")
    key_file = ask_text("Путь к приватному SSH-ключу", "~/.ssh/id_ed25519")
    known_hosts = ask_text("Путь к known_hosts", "~/.ssh/known_hosts")
    settings = SSHSettings(host=host, user=user, port=ssh_port, key_file=key_file, known_hosts=known_hosts)
    try:
        settings.validate()
    except (ValueError, OSError) as exc:
        raise ConfigurationError(str(exc)) from exc

    directory = ask_text("Абсолютный путь к папке Minecraft на удалённом хосте")
    if not directory.startswith("/") or posixpath.normpath(directory) != directory:
        raise ConfigurationError("Укажи нормализованный абсолютный Linux-путь, например /home/mc/server")
    if any(
        s.get("type") == "ssh"
        and s.get("ssh", {}).get("host") == host
        and int(s.get("ssh", {}).get("port", 22)) == ssh_port
        and s.get("ssh", {}).get("user") == user
        and s.get("server_dir") == directory
        for s in servers
    ):
        raise ConfigurationError("Этот удалённый сервер уже подключён")

    remote = SSHRemote(settings, {"server_dir": directory})
    print("⏳ Проверяю SSH и папку Minecraft…")
    try:
        info = await remote.request("probe", timeout=40)
    except Exception as exc:
        raise ConfigurationError(f"Не удалось подключиться через SSH: {exc}") from exc
    props = parse_properties(info["properties"])
    candidates = info["launchers"]
    default_cmd = "./" + candidates[0] if candidates else "./start-server.sh"

    folder_name = posixpath.basename(directory)
    default_id = re.sub(r"[^a-z0-9_-]+", "-", folder_name.lower()).strip("-_")[:32]
    sid = ask_text("Уникальный ID Minecraft-сервера", default_id)
    if not SERVER_ID_PATTERN.fullmatch(sid):
        raise ConfigurationError("ID: 1–32 символа [a-z0-9_-], начинается с буквы или цифры")
    if any(s.get("id") == sid for s in servers):
        raise ConfigurationError(f"ID {sid!r} уже занят")
    name = ask_text("Название в Telegram", folder_name.replace("-", " ").title())
    command = ask_text("Команда запуска", default_cmd)
    if not command:
        raise ConfigurationError("Команда запуска не может быть пустой")

    print("Порт должен быть свободен именно НА удалённом компьютере.")
    def_port = validate_port(props.get("server-port", 25565), "Minecraft port")
    game_port = validate_port(ask_text("Порт Minecraft", str(def_port)), "Minecraft port")
    def_rcon = validate_port(props.get("rcon.port", 25575), "RCON port")
    rcon_port = validate_port(ask_text("Порт RCON на удалённом ПК", str(def_rcon)), "RCON port")
    if rcon_port == game_port:
        raise ConfigurationError("Minecraft и RCON должны иметь разные порты")

    # Existing server configs on the *same remote machine* must also be checked:
    # inactive ports can still clash even if the listener is not running.
    for previous in servers:
        ssh = previous.get("ssh") or {}
        if previous.get("type") != "ssh":
            continue
        if (ssh.get("host"), ssh.get("user"), int(ssh.get("port", 22))) != (host, user, ssh_port):
            continue
        used = {int(previous.get("rcon_port", 25575))}
        if previous.get("minecraft_port") is not None:
            used.add(int(previous["minecraft_port"]))
        if game_port in used or rcon_port in used:
            raise ConfigurationError("Порт совпадает с другим сервером на этом SSH-хосте")

    for port, label in ((game_port, "Minecraft"), (rcon_port, "RCON")):
        try:
            available = await remote.request("port_available", port=port, timeout=25)
        except Exception as exc:
            raise ConfigurationError(f"Не удалось проверить порт {port}: {exc}") from exc
        if not available:
            raise ConfigurationError(f"Порт {label} {port} занят на удалённом ПК. Останови сервер или выбери другой.")

    if props.get("rcon.password") and ask_yes_no("Использовать текущий RCON-пароль сервера?"):
        secret = props["rcon.password"]
    elif ask_yes_no("Сгенерировать безопасный пароль RCON автоматически?"):
        secret = secrets.token_hex(24)
    else:
        secret = getpass.getpass("Пароль RCON (скрыт): ").strip()
        if not secret or any(c in secret for c in "\n\r"):
            raise ConfigurationError("Пароль RCON должен быть непустым, без переводов строк")

    rcon_key = sid.upper().replace("-", "_") + "_RCON_PASSWORD"
    if any(s.get("rcon_password_env") == rcon_key for s in servers):
        raise ConfigurationError(f"Переменная {rcon_key} уже используется")

    env_path = project_dir / ".env"
    env_original = read_bytes(env_path)
    if env_original is None:
        raise ConfigurationError("Локальный .env бота не найден")
    try:
        env_new = update_env(env_original.decode("utf-8"), rcon_key, secret).encode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError("Локальный .env не в UTF-8") from exc

    world_name = props.get("level-name", "world").strip() or "world"
    if world_name in {".", ".."} or posixpath.isabs(world_name) or ".." in world_name.split("/"):
        raise ConfigurationError("Некорректное имя мира в server.properties")
    props_updated = update_properties(info["properties"], {
        "server-port": str(game_port),
        "enable-rcon": "true",
        "rcon.port": str(rcon_port),
        "rcon.password": secret,
    })

    entry = {
        "id": sid, "name": name, "type": "ssh",
        "ssh": {
            "host": host, "user": user, "port": ssh_port,
            "key_file": str(Path(key_file).expanduser().resolve()),
            "known_hosts": str(Path(known_hosts).expanduser().resolve()),
        },
        "server_dir": directory, "start_command": command,
        "minecraft_port": game_port,
        "rcon_host": "127.0.0.1", "rcon_port": rcon_port,
        "rcon_password_env": rcon_key,
        "auto_stop_seconds": 0,
        "backup_retention_max_count": 0, "backup_retention_max_gb": 0,
    }
    if world_name != "world":
        entry["world_dir"] = posixpath.join(directory, world_name)
    servers.append(entry)

    print("\n── Подтверждение удалённого сервера ──")
    print(f"SSH: {user}@{host}:{ssh_port} (проверка известного host key обязательна)")
    print(f"Папка: {directory} · Название: {name} · ID: {sid}")
    print(f"Запуск: {command} · Minecraft: {game_port}/TCP · RCON: localhost:{rcon_port}")
    print(f"RCON-пароль скрыт; локальная переменная: {rcon_key}")
    print("Автостоп и автоматическое удаление бэкапов по умолчанию выключены.")
    print("Изменяем: локальные servers.json/.env и УДАЛЁННЫЙ server.properties.")
    if not ask_yes_no("Подключить удалённый сервер?", default=False):
        return False

    backup_dir = project_dir / "config_backups"
    # Before editing a remote file, preserve its *original* contents locally.
    backup = save_config_backup(Path("server.properties"), info["properties"].encode("utf-8"),
                                backup_dir, server_id=sid)
    print(f"🔐 Копия исходного server.properties: {backup}")
    try:
        await remote.request(
            "configure_properties", content=props_updated,
            expected_properties=info["properties"], timeout=40,
        )
    except Exception as exc:
        raise ConfigurationError(f"Не удалось обновить удалённый server.properties: {exc}") from exc

    try:
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        saved = apply_files([
            (env_path, env_original, env_new, True),
            (servers_path, raw_config, payload, False),
        ], backup_dir=backup_dir, server_id=sid)
    except Exception:
        try:
            await remote.request(
                "configure_properties", content=info["properties"],
                expected_properties=props_updated, timeout=40,
            )
        except Exception:
            print("⚠️ Не удалось вернуть удалённый server.properties! Используй сохранённый бэкап.")
        raise

    print(f"\n✅ {name} подключён. Архивы миров будут храниться на удалённом хосте.")
    for item in saved:
        print(f"   Резервная копия: {item}")
    print("Перезапусти бота: sudo systemctl restart telegram-minecraft-manager.service")
    print("Права друзьям назначай через python3 scripts/manage_users.py")
    return True


def run_remote_wizard(project_dir: Path, servers_path: Path, raw_config: bytes | None,
                      document, servers) -> bool:
    return asyncio.run(_remote_setup(project_dir, servers_path, raw_config, document, servers))
