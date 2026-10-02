# External Control API

The Control API is an **optional integration layer** for Telegram Minecraft Server Manager.

The manager itself, the Telegram UI, process control, event monitoring, backups and
event-driven auto-stop do **not** require the API. Enable it only when another program
needs to control or inspect the manager.

Typical clients include:

- AI assistants and agents;
- Open WebUI / Jarvis;
- other Telegram or Discord bots;
- automation services;
- dashboards;
- local scripts.

The API is deliberately client-agnostic. Jarvis is only one possible client.

## Enable

Use the generic v1.1+ variables:

```env
CONTROL_API_ENABLED=true
CONTROL_API_HOST=127.0.0.1
CONTROL_API_PORT=8765
CONTROL_API_TOKEN=replace_with_a_long_random_token
```

Generate a token with:

```bash
openssl rand -hex 32
```

Existing installations using `JARVIS_API_ENABLED`, `JARVIS_API_HOST`,
`JARVIS_API_PORT` and `JARVIS_API_TOKEN` continue to work. Do not define both
families unless you intentionally want `CONTROL_API_*` to take precedence.

By default the API should stay bound to `127.0.0.1`. Use a trusted reverse proxy,
VPN or SSH tunnel for remote access instead of exposing the port directly to the
public Internet.

## Authentication

Every control endpoint requires a bearer token:

```http
Authorization: Bearer <CONTROL_API_TOKEN>
```

Health is intentionally unauthenticated:

```text
GET /health
```

The OpenAPI schema is available at:

```text
GET /openapi.json
```

This makes the API directly usable as a tool source for clients that import OpenAPI
functions, including AI/agent systems.

## Read-only operations

These operations inspect state and files without changing the Minecraft server:

| Operation ID | Method | Path | Purpose |
| --- | --- | --- | --- |
| `minecraft_list_servers` | GET | `/v1/minecraft/servers` | List configured servers |
| `minecraft_status` | GET | `/v1/minecraft/servers/{server_id}/status` | Process state, players and auto-stop state |
| `minecraft_get_auto_stop` | GET | `/v1/minecraft/servers/{server_id}/auto-stop` | Read auto-stop configuration/state |
| `minecraft_logs` | GET | `/v1/minecraft/servers/{server_id}/logs` | Read recent manager output |
| `minecraft_list_files` | GET | `/v1/minecraft/servers/{server_id}/files` | Browse files below the server root |
| `minecraft_read_file` | GET | `/v1/minecraft/servers/{server_id}/files/read` | Read diagnostic text files and `.log.gz` |

File access is restricted to the configured Minecraft server directory. Traversal and
symlink escapes are rejected, common secret/key files are blocked, and obvious
password/token assignments are redacted.

## Mutating operations

These operations change server state and should be granted only to trusted clients:

| Operation ID | Method | Path | Purpose |
| --- | --- | --- | --- |
| `minecraft_start` | POST | `/v1/minecraft/servers/{server_id}/start` | Start a server |
| `minecraft_stop` | POST | `/v1/minecraft/servers/{server_id}/stop` | Stop a server |
| `minecraft_restart` | POST | `/v1/minecraft/servers/{server_id}/restart` | Restart a server |
| `minecraft_set_auto_stop` | POST | `/v1/minecraft/servers/{server_id}/auto-stop` | Change empty-server timeout |
| `minecraft_rcon` | POST | `/v1/minecraft/servers/{server_id}/rcon` | Execute one raw RCON command |

For AI clients it is useful to expose read-only and mutating functions as separate
tool groups. Raw RCON belongs in the mutating group.

## Auto-stop is a core feature, not an AI feature

Auto-stop runs locally inside the manager:

```text
last player leaves
        ↓
local timer starts
        ↓
player joins → timer is cancelled
        ↓
timeout expires
        ↓
one RCON "list" verification
        ↓
0 players → stop server
```

No AI model, Open WebUI session or recurring API polling is required. Telegram and
the Control API only change the policy.

For example, setting `120` seconds through Telegram or
`minecraft_set_auto_stop` produces the same stored manager setting.

## Examples

List servers:

```bash
curl -s \
  -H "Authorization: Bearer $CONTROL_API_TOKEN" \
  http://127.0.0.1:8765/v1/minecraft/servers
```

Set two-minute auto-stop:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $CONTROL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"timeout_seconds":120}' \
  http://127.0.0.1:8765/v1/minecraft/servers/storm-survival/auto-stop
```

Run RCON:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $CONTROL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"command":"list"}' \
  http://127.0.0.1:8765/v1/minecraft/servers/storm-survival/rcon
```

## Legacy single-server endpoints

Older single-server endpoints remain available for backwards compatibility and are
marked deprecated in OpenAPI. New clients should use the multi-server
`/v1/minecraft/servers/{server_id}/...` endpoints.
