# Changelog

All notable changes to this project are documented here.

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
