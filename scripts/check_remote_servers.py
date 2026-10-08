#!/usr/bin/env python3
"""Read-only diagnostic for configured Minecraft SSH connections.

Run with the same Python interpreter and user as the Telegram systemd service.
No credentials, RCON passwords or remote server.properties are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.server_registry import server_registry  # noqa: E402


def key_warning(key_file: str) -> str | None:
    """Warn when other users can read a dedicated SSH private key."""
    key = Path(key_file).expanduser()
    try:
        mode = key.stat().st_mode
    except OSError as exc:
        return f"SSH key cannot be inspected: {exc}"
    if not stat.S_ISREG(mode):
        return "SSH private key is not a regular file"
    if mode & 0o077:
        return f"SSH private key is group/world accessible (permissions {mode & 0o777:03o})"
    if not os.access(key, os.R_OK):
        return "SSH private key is not readable by the current service user"
    return None


async def diagnose(server) -> bool:
    remote = server.ssh_remote
    cfg = remote.settings
    print(f"\n🌐 {server.server_name} ({server.server_id})")
    print(f"   SSH: {cfg.user}@{cfg.host}:{cfg.port}")
    print(f"   Key: {Path(cfg.key_file).expanduser()}")
    warning = key_warning(cfg.key_file)
    if warning:
        print(f"   ⚠️ {warning}")

    try:
        info = await remote.request("probe", timeout=30)
        # Avoid printing server.properties, which may contain the RCON password.
        print(f"   ✅ SSH, host-key verification, Python {info['python']} and server folder")
        status = await remote.request("status", timeout=25)
        if status.get("running") and status.get("pid") is None:
            print("   ⚠️ Minecraft TCP port responds but no managed PID was found; "
                  "destructive actions are disabled")
        else:
            print("   ✅ Minecraft: " + ("running" if status["running"] else "stopped") +
                  (f"; managed PID {status['pid']}" if status.get("pid") else ""))
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"   ❌ SSH diagnosis failed: {type(exc).__name__}: {exc}")
        return False


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", help="ID of one registered remote Minecraft server")
    args = parser.parse_args()
    servers = [
        server for server in server_registry.list()
        if server.ssh_remote is not None
        and (not args.server or server.server_id == args.server)
    ]
    if not servers:
        print("No matching SSH servers configured.")
        return 2 if args.server else 0
    print(f"Running as uid={os.getuid()}; use the same Unix user as the bot service.")
    results = [await diagnose(server) for server in servers]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
