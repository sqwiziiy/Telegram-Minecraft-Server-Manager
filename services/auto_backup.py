"""Persistent, per-server offline backup scheduling.

A due backup is kept pending while Minecraft is running. Missed intervals
collapse into one backup after the server stops; the clock restarts on success.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

from config import AUTO_BACKUP_STATE_FILE
from services.backup import create_backup
from services.event_history import event_history

if TYPE_CHECKING:
    from services.server_registry import ManagedServer

logger = logging.getLogger(__name__)

BACKUP_INTERVALS = (6 * 3600, 12 * 3600, 24 * 3600, 3 * 86400, 7 * 86400)
CHECK_INTERVAL_SECONDS = 15
RETRY_INTERVAL_SECONDS = 300


@dataclass
class BackupSchedule:
    interval_seconds: int
    next_due_at: float
    pending: bool = False


class AutoBackupManager:
    def __init__(self, state_file: str | Path = AUTO_BACKUP_STATE_FILE) -> None:
        self.state_file = Path(state_file)
        self._schedules = self._load_state()
        self._write_lock = asyncio.Lock()
        self._server_locks: dict[str, asyncio.Lock] = {}
        self._retry_not_before: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._stop_requested = asyncio.Event()

    def _load_state(self) -> dict[str, BackupSchedule]:
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Cannot load auto-backup state %s: %s", self.state_file, exc)
            return {}
        servers = payload.get("servers") if isinstance(payload, dict) else None
        if not isinstance(servers, dict):
            return {}
        schedules = {}
        for server_id, item in servers.items():
            if not isinstance(server_id, str) or not isinstance(item, dict):
                continue
            try:
                interval = int(item["interval_seconds"])
                due = float(item["next_due_at"])
                pending = item.get("pending", False)
            except (ValueError, TypeError, KeyError):
                continue
            if interval not in BACKUP_INTERVALS or not 0 < due < 1e12 or not isinstance(pending, bool):
                continue
            schedules[server_id] = BackupSchedule(interval, due, pending)
        return schedules

    @staticmethod
    def _write_state_sync(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, path)

    async def _persist(self) -> None:
        async with self._write_lock:
            payload = {
                "servers": {
                    sid: {
                        "interval_seconds": value.interval_seconds,
                        "next_due_at": value.next_due_at,
                        "pending": value.pending,
                    }
                    for sid, value in sorted(self._schedules.items())
                }
            }
            await asyncio.to_thread(self._write_state_sync, self.state_file, payload)

    def status(self, server: ManagedServer) -> dict:
        schedule = self._schedules.get(server.server_id)
        if schedule is None:
            return {
                "enabled": False,
                "interval_seconds": 0,
                "next_due_at": None,
                "pending": False,
            }
        return {
            "enabled": True,
            "interval_seconds": schedule.interval_seconds,
            "next_due_at": schedule.next_due_at,
            "pending": schedule.pending,
        }

    async def set_interval(self, server: ManagedServer, seconds: int) -> dict:
        if seconds != 0 and seconds not in BACKUP_INTERVALS:
            raise ValueError("Unsupported auto-backup interval")
        async with self._server_locks.setdefault(server.server_id, asyncio.Lock()):
            if seconds == 0:
                self._schedules.pop(server.server_id, None)
            else:
                self._schedules[server.server_id] = BackupSchedule(
                    interval_seconds=seconds,
                    next_due_at=time.time() + seconds,
                )
            self._retry_not_before.pop(server.server_id, None)
            await self._persist()
        return self.status(server)

    async def check_server(self, server: ManagedServer) -> None:
        """Run one scheduler check; useful for recovery and for unit tests."""
        sid = server.server_id
        async with self._server_locks.setdefault(sid, asyncio.Lock()):
            schedule = self._schedules.get(sid)
            if schedule is None:
                return
            if not schedule.pending and time.time() < schedule.next_due_at:
                return
            if time.monotonic() < self._retry_not_before.get(sid, 0):
                return

            process = await server.manager.status()
            if process.running:
                if not schedule.pending:
                    schedule.pending = True
                    await self._persist()
                    await event_history.record(
                        server,
                        kind="auto_backup_pending",
                        text="Автобэкап ожидает полной остановки сервера",
                    )
                return

            # Hold the server process manager's start/stop lock while archiving.
            # A start request through Telegram/API must wait for the backup.
            # Recheck the process while holding the lock to avoid a TOCTOU race.
            ran, result = await server.manager.run_if_stopped(
                lambda: create_backup(server, automatic=True)
            )
            if not ran:
                if not schedule.pending:
                    schedule.pending = True
                    await self._persist()
                return

            if not isinstance(result, str) or not result.startswith("✅"):
                schedule.pending = True
                await self._persist()
                self._retry_not_before[sid] = time.monotonic() + RETRY_INTERVAL_SECONDS
                await event_history.record(
                    server,
                    kind="auto_backup_error",
                    text="Автобэкап не создан; повторная попытка через 5 минут",
                )
                logger.error("Auto-backup for %s failed: %s", sid, result)
                return

            schedule.pending = False
            schedule.next_due_at = time.time() + schedule.interval_seconds
            self._retry_not_before.pop(sid, None)
            await self._persist()
            await event_history.record(
                server,
                kind="auto_backup",
                text="Автоматический бэкап создан после остановки сервера · "
                + result.replace("<code>", "").replace("</code>", "").replace("<b>", "").replace("</b>", "").replace("\n", " "),
            )
            logger.info("Auto-backup completed for %s", sid)

    async def _run(self, servers: list[ManagedServer]) -> None:
        while not self._stop_requested.is_set():
            for server in servers:
                try:
                    await self.check_server(server)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Auto-backup check failed for %s", server.server_id)
            try:
                await asyncio.wait_for(self._stop_requested.wait(), timeout=CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def start(self, servers: Iterable[ManagedServer]) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_requested.clear()
        self._task = asyncio.create_task(
            self._run(list(servers)), name="auto_backup_scheduler"
        )

    async def shutdown(self) -> None:
        if self._task is not None:
            # Do not cancel an active archive: its worker thread would keep writing
            # after the async task releases the server start/stop lock.
            self._stop_requested.set()
            await self._task
            self._task = None


auto_backup_manager = AutoBackupManager()
