# Telegram Minecraft Server Manager v3.0.0
## Server Management & Recovery Update
**Release date:** 13 November 2026

Version 3.0.0 packages the existing code from [PR #14](https://github.com/sqwiziiy/Telegram-Minecraft-Server-Manager/pull/14) into a stable GitHub release. It does **not** introduce new Python application changes beyond the already merged work.

### What's new

- **Automatic backup status:** the bot displays when a scheduled backup is pending, creating or failed; it also shows the time and size of the latest completed backup.
- **Enable or disable mods/plugins without deleting them:** rename installed `.jar` files to `.jar.disabled` and back. Available for local and SSH servers; changes are blocked while the corresponding Minecraft server is running.
- **Backup browser:** manually create an archive, list existing ZIPs (six per page), and inspect their date, type, size, file count and entries without unpacking them. Local and SSH backends are supported.
- **World recovery:** restore a world from an existing ZIP only while its Minecraft server is stopped. The action requires the `backup.restore` permission and two Telegram confirmations.

### Restore safety

Before swapping world directories the manager validates archive integrity, rejects path traversal and symbolic/special files, checks available space, and creates a separate `world_pre_restore_*.zip` safety backup of the current world. It retains the original source archive and uses Linux `renameat2(RENAME_EXCHANGE)` to exchange the directories atomically. Unsupported filesystems abort without replacing the world. SSH restore is performed on the remote host.

### Testing and scope

- Python compile/unit/security checks passed for the feature branch, and CI passed again on `main` after the merge.
- The project maintainer reports that the manual validation has been completed.
- **Not included in v3.0.0:** the proposed dynamic UI overhaul showing running/stopped/unreachable states with disabled actions; this work may be delivered separately.

### Upgrade

1. Preserve your existing `.env`, `servers.json`, `users.json`, and server/world backups.
2. In your existing bot checkout, run:
   ```bash
   git fetch origin
   git switch main
   git pull --ff-only origin main
   ```
3. Use the same Python environment already configured for the bot, and install dependencies from `requirements.txt` if needed.
4. Restart **the Telegram bot** using your existing supervisor or launch method.

The manager starts and controls local Minecraft processes directly through the configured start command, or remotely over SSH. **Minecraft does not need a systemd unit.** The optional `deploy/systemd/telegram-minecraft-server-manager.service` is only an example for supervising the Telegram bot itself.

### Notes in Russian / Кратко

Релиз v3.0.0 включает улучшенные статусы автобэкапов, переключение модов/плагинов без удаления, каталог ZIP-бэкапов и безопасное восстановление мира на остановленном сервере. Реализованный ранее код слит в `main`. Для Minecraft отдельный systemd-сервис не нужен, запуском занимается бот. Переработка динамических статусов Telegram-интерфейса в этот релиз не входит.
