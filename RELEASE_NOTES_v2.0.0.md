# Telegram Minecraft Server Manager v2.0.0
## Multi-Server & Remote Management Update

This is the largest update since v1.0.0. One Telegram bot can now manage several existing Minecraft servers — on the same Linux computer **or on other Linux hosts over SSH** — with independent access permissions, server-specific software profiles, automation and backups.

### Highlights

- **Multiple servers, one bot.** Per-server process, RCON, status, ports, backups, events and access policies.
- **Remote Linux hosts via SSH.** Start/stop/restart, process status, RCON, logs, live events, auto-stop, world backups and mod/plugin transfer over SSH/SFTP. No permanent agent or HTTP daemon to install on the Minecraft host. RCON stays on remote loopback and is not exposed to the Internet.
- **Vanilla / Mods / Plugins profiles.** `vanilla` hides addon controls; `mods` manages `mods/` (Fabric, Forge, NeoForge and Quilt); `plugins` manages `plugins/` (Paper, Spigot and Purpur). The interactive wizard detects these directories on local or SSH hosts and suggests a profile, with manual override.
- **Interactive configuration tools.** Add existing local or SSH servers; change a registered server's profile without recreating it; manage users, roles and per-server permissions; automatic private snapshots in `config_backups/`.
- **Access control.** `OWNER_IDS`, user roles, permission-aware buttons and server-side authorization checks. New server access is not automatically granted to other users.
- **Automation and history.** Empty-server auto-stop, scheduled offline ZIP backups with retention, and persistent Minecraft events with a Telegram activity view.
- **Control API.** Optional bearer-authenticated API for external applications and automation, with guarded file inspection and secret redaction.
- **Remote reliability hardening.** Host-side operation locks stop overlapping start/stop/restart/backup actions; managed PID identity includes the Linux boot ID; remote backups publish ZIPs atomically, never as incomplete archives; better errors for unreachable SSH or uncertain operations.

### Upgrading from v1.0.0

1. Back up your local `.env`, `servers.json` and `users.json` (if present). Back up Minecraft worlds separately.
2. Update your checkout to `main`. Install updated dependencies in the **same virtual environment as your systemd service**, for example `./venv/bin/python -m pip install -r requirements.txt`.
3. If already using `servers.json`, existing entries remain valid. Connection `type` defaults to `local`; `server_software` defaults to `mods` for backward compatibility.
4. Run `python3 scripts/manage_servers.py` to register servers or choose option 3 to change an existing server's Vanilla/Mods/Plugins profile. Use `python3 scripts/manage_users.py` to grant others access to new servers.
5. For SSH servers, set up a dedicated SSH key, verify the remote host fingerprint in `known_hosts`, and ensure the service account can connect **non-interactively**. Never expose RCON publicly. Run `./venv/bin/python scripts/check_remote_servers.py --server SERVER_ID` for read-only diagnosis.
6. For **locally launched Java servers**, retain `KillMode=process` in the Telegram manager systemd unit: `KillMode=control-group` risks killing Minecraft on bot restart.
7. Restart the Telegram bot service and verify the status, permissions and one disposable world's backup before enabling automation.

### Compatibility and limitations

- Remote Minecraft host: Linux, SSH server, Python 3.10+ and an existing Minecraft installation.
- SSH transport uses key authentication and verified `known_hosts`; the bot-host service user must be able to read the chosen key.
- The manager will not stop or send destructive RCON commands to an unrecognized remote process just because a port is listening.
- A lost SSH connection during a start/stop/backup does not necessarily cancel the remote operation; check the status or archives before retrying.
- If the remote agent is forcibly killed during a live backup after `save-off`, confirm saves are enabled with `save-on`.
- World archives remain on each remote host. Minecraft itself is not automatically started after a remote-host reboot unless independently configured.
- `KillMode=process` intentionally leaves local Java running after a bot restart; systemd may print a harmless leftover-process warning. Isolating local Java into separate units/scopes would require a separate migration.

### Quality checks

- GitHub Actions: Python compilation, **138 unit tests** and accidental-secret detection passed.
- The maintainer confirmed **all eight manual release-readiness checks** on the real test setup, including SSH recovery/reboot, process identity, backups, software profiles and local-server compatibility.

Thanks for testing and helping shape the release!
