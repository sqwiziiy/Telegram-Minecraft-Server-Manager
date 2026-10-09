# Changelog

All notable changes to this project are documented here.

## [v2.0.1] - 2026-10-09

### Added

- Interactive menu option 4 in `scripts/manage_servers.py` to unregister local or SSH Minecraft servers without manually editing configuration files.
- Confirmation by entering the exact server ID, with preview and an explicit guarantee that Minecraft processes, files, worlds, mods, plugins, logs and ZIP archives are left untouched.
- Cleanup of unshared RCON environment variables, per-server user permissions, and persisted auto-stop and auto-backup settings; other servers remain unchanged.
- Safe reassignment of `DEFAULT_SERVER_ID` when necessary without transferring legacy user privileges. The last server cannot be removed.
- Private timestamped config backups for all changed files, including custom JSON config/state paths, with rollback on write failure.

### Upgrade

- Stop the Telegram bot service before modifying persistent auto-task states. On local hosts use `KillMode=process` to keep Java running across bot service restarts.
- Run `python3 scripts/manage_servers.py`, choose option 4 and type the server ID to confirm. Then start the bot service again.

## [v2.0.0] - 2026-10-08

**Multi-Server & Remote Management Update.** Consolidates the previously unreleased multi-server, user-permission, event-history, automation, control-API and SSH-management features.

### Added

- Registry of independently configured Minecraft servers, each with its own local/SSH transport, process lifecycle, RCON, status, world backups and settings.
- SSH remote Linux server management: launch, stop, restart, health, console, event log monitoring, mods/plugins through SFTP, on-host world backups and log/file inspection; no permanent remote daemon required.
- Per-server `server_software` choices: `vanilla`, `mods` and `plugins` (Paper/Spigot/Purpur), with appropriate Telegram tabs and addon directories.
- Local/remote installation detection and assisted software profile selection; an editor for existing server software profiles.
- Interactive `scripts/manage_servers.py` and `scripts/manage_users.py` for registration and permission configuration; private `config_backups/` snapshots.
- `OWNER_IDS`, per-user roles (owner/admin/operator/viewer/custom), explicit per-server permissions and deny overrides.
- Event-driven empty-server auto-stop, persistent scheduled offline ZIP backups and automatic backup retention controls.
- Per-server event history, Telegram event-view tab and daily logs.
- Optional bearer-authenticated Control API with OpenAPI IDs, guarded server file inspection and secret redaction.
- Read-only SSH diagnostic tool at `scripts/check_remote_servers.py`.

### Changed

- Telegram UI shows a server selector and uses context-aware actions and permissions for each server.
- Logs and backup results edit the existing Telegram panel message with consistent back navigation.
- Live event notifications are consolidated instead of sending a new message per line.
- Host disk reporting uses `HOST_DISK_PATH` (default `/home`).
- `KillMode=process` remains the recommended manager systemd setting, preserving locally launched Minecraft across Telegram-bot restarts.
- Deprecated Jarvis-specific API configuration in favor of generic `CONTROL_API_*` environment variables, retaining legacy aliases.

### Reliability and security

- SSH host identity validated through `known_hosts`, key authentication and server-local RCON; no exposed remote RCON listener.
- Host-side Linux file lock serializes remote start/stop/restart/backup, preventing double starts and races with offline backups.
- Managed PID validation includes process start ticks and Linux boot ID, and records PID immediately after launch.
- Unmanaged/unrecognized remote processes cannot be sent destructive RCON/stop commands or live backup operations by the bot.
- Remote ZIP backups are first written as `.partial` files and published atomically after successful completion.
- Better SSH error diagnostics, upload timeouts and safe handling of ambiguous results after SSH transport drops.
- Sensitive Telegram handlers check permissions server-side; UI hiding alone is not relied on for authorization.
- Safe default roles, path confinement and redaction for remote read operations.
- CI verified 138 unit tests, Python compilation and accidental-secret checks; eight additional maintainer-run release checks passed.

### Migration from v1.0.0

- Existing single-server configuration and `ADMIN_IDS` continue to work. For dedicated owners set `OWNER_IDS` and configure access to new servers through `scripts/manage_users.py`.
- Existing `servers.json` entries default to `type: local` and `server_software: mods`, preserving previous behavior.
- Upgrade dependencies using the Python environment from the **systemd ExecStart** command; SSH support requires `asyncssh`.
- Use `scripts/manage_servers.py` to add or edit server entries, which writes recoverable configuration backups.
- Keep `KillMode=process` for locally managed worlds. Systemd may report an expected leftover Java process; do not switch to `control-group` to hide that warning.
- For SSH configure non-interactive authentication for the service user, `known_hosts` pinning, and least-privilege access; do not open RCON to the public network.
- Remote host reboot does not automatically launch Minecraft unless configured separately. If SSH dies during a destructive action, refresh state before retrying.
- See `RELEASE_NOTES_v2.0.0.md` and both READMEs for detailed upgrade instructions.

## [v1.0.0] - 2026-09-20

First release-ready version of Telegram Minecraft Server Manager.

### Added

- Direct Minecraft process management without requiring a Minecraft systemd unit.
- Support for launching an existing `start.sh` through `SERVER_START_COMMAND`.
- Support for direct Java launch commands.
- Managed PID metadata with process creation-time validation.
- Graceful server shutdown through RCON with a save-safe grace period and SIGTERM/SIGKILL fallback.
- Captured server stdout/stderr through `SERVER_OUTPUT_LOG`.
- Server PID, uptime and process-tree memory usage in the Telegram UI.
- Launch-log viewer in the System panel.
- Configurable mod upload size limit.
- GitHub Actions CI for Python compilation, core unit tests and accidental-secret checks.
- Rewritten English and Russian documentation with architecture and deployment examples.

### Changed

- Removed Minecraft control through `sudo systemctl`.
- Refreshed the Telegram main menu, System panel and welcome screen.
- Status no longer sends a `say` command as a side effect.
- Server configuration now uses `SERVER_DIR` and `SERVER_START_COMMAND`.
- Live backups coordinate with Minecraft using `save-off` + `save-all flush` + `save-on` for a consistent world snapshot.
- RCON console messaging and error presentation are clearer.

### Security

- Server launch never uses `shell=True`.
- RCON packet sizes are validated before the payload is read.
- RCON output, Minecraft logs, filenames and errors are HTML-escaped before Telegram rendering.
- Mod callback indexes are validated.
- Mod uploads reject unsafe names, enforce a size limit and refuse to overwrite existing paths.
- Mod upload finalization is atomic.
- Startup fails if no valid `ADMIN_IDS` are configured.
- CI checks for common accidentally committed Telegram tokens and RCON passwords.

### Migration

Replace the old Minecraft systemd settings:

```env
SERVER_JAR=/opt/minecraft/fabric-server-launch.jar
SYSTEMD_SERVICE_NAME=minecraft
```

with:

```env
SERVER_DIR=/opt/minecraft
SERVER_START_COMMAND=./start.sh
```

A launch script should keep the Minecraft process in the foreground, ideally:

```bash
#!/usr/bin/env bash
exec java -Xms2G -Xmx6G -jar fabric-server-launch.jar nogui
```
