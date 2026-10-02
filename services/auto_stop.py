from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Iterable, TYPE_CHECKING

from config import AUTO_STOP_STATE_FILE
from services.event_feed import parse_minecraft_event

if TYPE_CHECKING:
    from services.server_registry import ManagedServer

logger = logging.getLogger(__name__)

_READY_MARKERS = ("Done (", "For help, type")
_ONLINE_COUNT_RE = re.compile(r"There are\s+(?P<count>\d+)\s+of a max of\s+\d+\s+players online", re.IGNORECASE)

AutoStopNotifier = Callable[["ManagedServer", int, str], Awaitable[None]]


def parse_online_count(response: str) -> int | None:
    """Extract the vanilla /list online-player count."""
    match = _ONLINE_COUNT_RE.search(response or "")
    if match is None:
        return None
    return int(match.group("count"))


def is_server_ready_line(line: str) -> bool:
    return all(marker in line for marker in _READY_MARKERS)


class AutoStopManager:
    """Event-driven empty-server auto-stop.

    No periodic RCON polling is used. Join/leave/server-ready log events arm or
    cancel one countdown. When the countdown expires, one local RCON `list`
    command verifies that the server is still empty before stopping it.
    """

    def __init__(self, state_file: str | Path = AUTO_STOP_STATE_FILE) -> None:
        self.state_file = Path(state_file)
        self._overrides: dict[str, int] = self._load_state()
        self._tasks: dict[str, asyncio.Task] = {}
        self._deadlines: dict[str, float] = {}
        self._players: dict[str, set[str]] = {}
        self._notifier: AutoStopNotifier | None = None
        self._write_lock = asyncio.Lock()

    def set_notifier(self, notifier: AutoStopNotifier | None) -> None:
        self._notifier = notifier

    def _load_state(self) -> dict[str, int]:
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read auto-stop state %s: %s", self.state_file, exc)
            return {}

        raw = payload.get("servers") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return {}

        result: dict[str, int] = {}
        for server_id, value in raw.items():
            if not isinstance(server_id, str):
                continue
            try:
                seconds = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= seconds <= 86400:
                result[server_id] = seconds
        return result

    def _write_state_sync(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"servers": dict(sorted(self._overrides.items()))},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.state_file)

    async def _persist(self) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._write_state_sync)

    def timeout_seconds(self, server: "ManagedServer") -> int:
        return self._overrides.get(
            server.server_id,
            int(getattr(server, "auto_stop_seconds", 0)),
        )

    def status(self, server: "ManagedServer") -> dict:
        timeout = self.timeout_seconds(server)
        task = self._tasks.get(server.server_id)
        pending = bool(task is not None and not task.done())
        remaining = None
        if pending:
            deadline = self._deadlines.get(server.server_id)
            if deadline is not None:
                remaining = max(0, math.ceil(deadline - time.monotonic()))

        return {
            "enabled": timeout > 0,
            "timeout_seconds": timeout,
            "pending": pending,
            "remaining_seconds": remaining,
            "tracked_players": sorted(self._players.get(server.server_id, set())),
        }

    def _cancel(self, server_id: str) -> None:
        task = self._tasks.pop(server_id, None)
        self._deadlines.pop(server_id, None)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _schedule(self, server: "ManagedServer") -> None:
        timeout = self.timeout_seconds(server)
        if timeout <= 0:
            self._cancel(server.server_id)
            return

        self._cancel(server.server_id)
        self._deadlines[server.server_id] = time.monotonic() + timeout
        task = asyncio.create_task(
            self._countdown(server, timeout),
            name=f"autostop:{server.server_id}",
        )
        self._tasks[server.server_id] = task
        logger.info(
            "Auto-stop armed for %s: verify emptiness in %ss",
            server.server_id,
            timeout,
        )

    async def _countdown(self, server: "ManagedServer", timeout: int) -> None:
        current = asyncio.current_task()
        try:
            await asyncio.sleep(timeout)
            await self._verify_and_stop(server, timeout)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Auto-stop countdown failed for %s", server.server_id)
        finally:
            if self._tasks.get(server.server_id) is current:
                self._tasks.pop(server.server_id, None)
                self._deadlines.pop(server.server_id, None)

    async def _verify_and_stop(self, server: "ManagedServer", timeout: int) -> None:
        process = await server.manager.status()
        if not process.running:
            return

        if not server.rcon_configured:
            logger.warning(
                "Auto-stop verification skipped for %s: RCON is not configured",
                server.server_id,
            )
            return

        response = await server.rcon("list")
        online = parse_online_count(response)
        if online is None:
            logger.warning(
                "Auto-stop verification for %s could not parse /list response: %r",
                server.server_id,
                response,
            )
            return

        if online > 0:
            logger.info(
                "Auto-stop cancelled for %s after verification: %s player(s) online",
                server.server_id,
                online,
            )
            return

        result = await server.manager.stop()
        self._players.pop(server.server_id, None)
        logger.info(
            "Auto-stop stopped %s after %ss empty: %s",
            server.server_id,
            timeout,
            result,
        )

        if self._notifier is not None:
            try:
                await self._notifier(server, timeout, result)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to send auto-stop notification for %s",
                    server.server_id,
                )

    async def arm(self, server: "ManagedServer") -> None:
        """Arm a countdown for a running server, verifying only at expiry."""
        if self.timeout_seconds(server) <= 0:
            self._cancel(server.server_id)
            return

        process = await server.manager.status()
        if process.running:
            self._schedule(server)
        else:
            self._cancel(server.server_id)

    async def bootstrap(self, servers: Iterable["ManagedServer"]) -> None:
        """Arm enabled servers once when the manager starts.

        This handles the case where the manager restarts while Minecraft is
        already running. It still performs no recurring polling.
        """
        for server in servers:
            try:
                await self.arm(server)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to bootstrap auto-stop for %s", server.server_id)

    async def set_timeout(self, server: "ManagedServer", seconds: int) -> dict:
        seconds = int(seconds)
        if not 0 <= seconds <= 86400:
            raise ValueError("auto-stop timeout must be between 0 and 86400 seconds")

        self._overrides[server.server_id] = seconds
        await self._persist()

        if seconds <= 0:
            self._cancel(server.server_id)
        else:
            await self.arm(server)

        logger.info("Auto-stop setting for %s changed to %ss", server.server_id, seconds)
        return self.status(server)

    async def handle_line(self, server: "ManagedServer", line: str) -> None:
        """Consume one interesting latest.log line."""
        if is_server_ready_line(line):
            self._players[server.server_id] = set()
            self._schedule(server)
            return

        event = parse_minecraft_event(line)
        if event is None:
            return

        players = self._players.setdefault(server.server_id, set())

        if event.kind == "join":
            players.add(event.player)
            self._cancel(server.server_id)
            logger.info(
                "Auto-stop cancelled for %s: %s joined",
                server.server_id,
                event.player,
            )
            return

        if event.kind == "leave":
            players.discard(event.player)
            if not players:
                self._schedule(server)

    async def on_server_action(
        self,
        server: "ManagedServer",
        action: str,
        result: str,
    ) -> None:
        """Keep the event timer in sync with manual/API start-stop actions."""
        if action == "stop":
            self._cancel(server.server_id)
            self._players.pop(server.server_id, None)
            return

        if action in {"start", "restart"} and result not in {"already_stopped"}:
            self._players[server.server_id] = set()
            await self.arm(server)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        self._deadlines.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


auto_stop_manager = AutoStopManager()
