"""Minecraft server software selector for local and SSH-managed installations.

The connection type is local or ssh.
The gameplay distribution is vanilla, mods, or plugins.
"""
from __future__ import annotations

SOFTWARE_OPTIONS = ("vanilla", "mods", "plugins")
SOFTWARE_NAMES = {
    "vanilla": "🍃 Vanilla (без модов и плагинов)",
    "mods": "🧩 Mods (Fabric / Forge / NeoForge / Quilt)",
    "plugins": "🔌 Plugins (Paper / Spigot / Purpur)",
}


def suggest_software(has_mods: bool, has_plugins: bool) -> tuple[str | None, str]:
    """Folder-based *hint* only; an empty server may not have either folder."""
    if has_mods and has_plugins:
        return None, "Найдены обе папки mods/ и plugins/: выбери тип вручную."
    if has_mods:
        return "mods", "Найдена папка mods/."
    if has_plugins:
        return "plugins", "Найдена папка plugins/."
    return "vanilla", "Папок mods/ и plugins/ нет. Vanilla — лишь предположение."


def choose_software(has_mods: bool, has_plugins: bool) -> str:
    suggestion, reason = suggest_software(has_mods, has_plugins)
    print("\n🎮 Тип Minecraft-сервера (что можно загружать через Telegram)")
    print(f"Проверка папок: mods/ {'✅' if has_mods else '❌'} · plugins/ {'✅' if has_plugins else '❌'}")
    print(reason)
    print("Тип влияет на меню бота, не устанавливает Fabric/Paper и не меняет серверное ПО.")
    for i, option in enumerate(SOFTWARE_OPTIONS, 1):
        print(f"  {i}. {SOFTWARE_NAMES[option]}" + (" · рекомендовано" if option == suggestion else ""))
    default = str(SOFTWARE_OPTIONS.index(suggestion) + 1) if suggestion else ""
    while True:
        prompt = f"Выбери тип [{default}]: " if default else "Выбери тип (1/2/3): "
        answer = input(prompt).strip() or default
        if answer in {"1", "2", "3"}:
            selected = SOFTWARE_OPTIONS[int(answer) - 1]
            missing = (selected == "mods" and not has_mods) or (selected == "plugins" and not has_plugins)
            if missing:
                print("⚠️ Выбранной папки пока нет. Сервер может создать её при первом запуске.")
            return selected
        print("Введи 1, 2 или 3.")
