# Telegram Minecraft Server Manager v1.0.0

First release-ready version of the project.

## Highlights

- Direct Minecraft process control without a dedicated Minecraft systemd unit.
- Launch through an existing `start.sh` or a direct Java command.
- PID + process creation-time tracking so the bot can reconnect to the managed server process after its own restart.
- Graceful shutdown through RCON with a safe wait window and SIGTERM/SIGKILL fallback.
- RCON console from Telegram.
- Server status with PID, uptime, player list and process-tree RAM usage.
- Launch stdout/stderr viewer.
- Mod upload/list/delete flow with path validation, size limits and overwrite protection.
- Consistent live-world ZIP backups using `save-off` → `save-all flush` → archive → `save-on`.
- Selected Minecraft events forwarded from `latest.log`.
- Refreshed Telegram UI and rewritten English/Russian documentation.

## Security hardening

- No `shell=True` for server launch.
- No passwordless `sudo systemctl minecraft ...` requirement.
- Bounded RCON packet size before payload reads.
- HTML escaping for RCON output, logs, file names and errors.
- Safer mod upload finalization.
- Startup validation for `ADMIN_IDS`.
- CI checks for Python syntax, core process/RCON behavior and common accidentally committed secrets.

## Migration

Replace:

```env
SERVER_JAR=/opt/minecraft/fabric-server-launch.jar
SYSTEMD_SERVICE_NAME=minecraft
```

with:

```env
SERVER_DIR=/opt/minecraft
SERVER_START_COMMAND=./start.sh
```

Recommended `start.sh`:

```bash
#!/usr/bin/env bash
exec java -Xms2G -Xmx6G -jar fabric-server-launch.jar nogui
```

See `README.md`, `README_RU.md` and `CHANGELOG.md` for full setup and migration details.
