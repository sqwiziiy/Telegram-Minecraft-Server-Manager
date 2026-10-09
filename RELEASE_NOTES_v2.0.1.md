# Telegram Minecraft Server Manager v2.0.1
## Safe Server Unregistration

This maintenance update adds an interactive way to **unregister an existing Minecraft server from the Telegram manager without hand-editing configuration files and without touching Minecraft worlds or processes**.

### New: Remove a server from the manager

Run `python3 scripts/manage_servers.py` and choose **4 — Unregister server from bot**. The wizard works with both local Linux and SSH-connected servers.

- Pick the server from a list and **type its exact ID** to confirm.
- Remove its entry from `servers.json`, unused server-specific RCON credentials from `.env`, its entries in `users.json`, and saved auto-stop / auto-backup state.
- Preserve other servers, their credentials, permissions and scheduled tasks.
- If removing the default server, choose another remaining server as `DEFAULT_SERVER_ID`. Legacy permissions tied to the deleted default are not silently transferred to the replacement.
- Prevent removal of the last registered server, avoiding the deprecated single-server fallback.
- Back up changed configuration/state files into private `config_backups/` copies and roll back earlier writes if a later update fails. Custom config file paths are supported.

### Safety guarantees

**Unregistering is not uninstalling:** the wizard never connects over SSH to remove data, never sends RCON, never stops or kills Minecraft, and never deletes worlds, plugins, mods, logs, ZIP backups, Minecraft directories or SSH keys. A server already running continues to run after being removed from the manager, but is no longer controllable through the bot.

### Upgrade and usage

1. Back up important configuration files and your Minecraft worlds as usual.
2. In the bot repository, pull the updated `main` branch. No new Python dependencies are required by this feature.
3. **Stop the Telegram bot service before editing persisted auto-task files** to prevent the running service from writing back outdated state. Ensure `KillMode=process` is configured if locally launched Java worlds should stay alive when the bot stops.
4. Run `./venv/bin/python scripts/manage_servers.py`, choose option `4`, select a server and type its ID to confirm. The wizard makes local config backups.
5. Start `telegram-minecraft-manager.service` again and verify the remaining servers in Telegram.

### Validation

- CI: **151 Python unit tests**, Python compilation and accidental-secret checks passed.
- The maintainer confirmed a successful real-world check of the new unregister flow.

See `README.md`, `README_RU.md`, and `CHANGELOG.md` for details.
