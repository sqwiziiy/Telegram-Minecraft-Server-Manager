from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.server_registry import ManagedServer

_LOG_PREFIX_RE = re.compile(
    r"^(?:\[(?P<time>\d{2}:\d{2}:\d{2})\]\s*)?"
    r"(?:\[[^\]]+\]\s*)*"
    r":?\s*(?P<payload>.*)$"
)
_HISTORY_LINE_RE = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
    r"\[(?P<kind>[a-z_]+)\] (?P<text>.*)$"
)
_JOIN_RE = re.compile(r"^(?P<player>\S+) joined the game$")
_LEAVE_RE = re.compile(r"^(?P<player>\S+) left the game$")
_CHAT_RE = re.compile(r"^<(?P<player>[^>]{1,64})>\s*(?P<message>.*)$")

_DEATH_MARKERS = (
    " was slain",
    " was shot",
    " drowned",
    " starved to death",
    " fell",
    " burned",
    " died",
    " hit the ground",
    " was killed",
    " suffocated",
    " was squashed",
    " blew up",
    " was blown up",
    " was fireballed",
    " was pricked",
    " walked into",
    " tried to swim in lava",
    " went up in flames",
    " was impaled",
    " was pummeled",
    " froze to death",
    " was stung to death",
    " experienced kinetic energy",
    " discovered the floor was lava",
    " was struck by lightning",
    " was killed by magic",
    " withered away",
)


@dataclass(frozen=True)
class MinecraftEvent:
    kind: str
    timestamp: str
    player: str = ""
    detail: str = ""


@dataclass(frozen=True)
class HistoryEntry:
    timestamp: str
    kind: str
    text: str


def _extract_payload(line: str) -> tuple[str, str]:
    match = _LOG_PREFIX_RE.match(line.strip())
    if match is None:
        return datetime.now().astimezone().strftime("%H:%M:%S"), line.strip()

    raw_time = match.group("time")
    timestamp = raw_time if raw_time else datetime.now().astimezone().strftime("%H:%M:%S")
    return timestamp, match.group("payload").strip()


def parse_minecraft_event(line: str) -> MinecraftEvent | None:
    """Parse one useful latest.log line into a server-history event."""
    timestamp, payload = _extract_payload(line)
    if not payload:
        return None

    if "Done (" in payload and "For help, type" in payload:
        return MinecraftEvent("server_ready", timestamp, detail="Сервер полностью запущен и готов")

    if payload == "Stopping server":
        return MinecraftEvent("server_stopping", timestamp, detail="Minecraft начал остановку сервера")

    match = _JOIN_RE.fullmatch(payload)
    if match:
        return MinecraftEvent("join", timestamp, player=match.group("player"))

    match = _LEAVE_RE.fullmatch(payload)
    if match:
        return MinecraftEvent("leave", timestamp, player=match.group("player"))

    match = _CHAT_RE.fullmatch(payload)
    if match:
        return MinecraftEvent(
            "chat",
            timestamp,
            player=match.group("player"),
            detail=match.group("message"),
        )

    lowered = payload.lower()
    if any(marker in lowered for marker in _DEATH_MARKERS):
        player = payload.split(" ", 1)[0] if " " in payload else payload
        return MinecraftEvent("death", timestamp, player=player, detail=payload)

    return None


def event_plain_text(event: MinecraftEvent) -> str:
    if event.kind == "join":
        return f"{event.player} зашёл на сервер"
    if event.kind == "leave":
        return f"{event.player} вышел с сервера"
    if event.kind == "chat":
        return f"{event.player}: {event.detail}"
    if event.kind == "death":
        return event.detail
    return event.detail


_KIND_ICON = {
    "join": "🟢",
    "leave": "🔴",
    "chat": "💬",
    "death": "💀",
    "server_start": "▶️",
    "server_stop": "⏹",
    "server_restart": "🔁",
    "server_ready": "✅",
    "server_stopping": "⏳",
    "auto_stop": "🌙",
    "auto_stop_setting": "⏱",
    "manager_detected": "🔎",
}


class EventHistory:
    """Persistent per-server history stored as one human-readable log per day."""

    def log_dir(self, server: "ManagedServer") -> Path:
        return server.manager.server_dir / "logs" / "events"

    def today_path(self, server: "ManagedServer") -> Path:
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        return self.log_dir(server) / f"{today}.log"

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(str(text).replace("\x00", "").splitlines()).strip()

    def _append_sync(
        self,
        server: "ManagedServer",
        *,
        kind: str,
        text: str,
        timestamp: datetime | None = None,
    ) -> HistoryEntry:
        now = timestamp or datetime.now().astimezone()
        safe_kind = re.sub(r"[^a-z_]", "_", kind.lower()).strip("_") or "event"
        safe_text = self._clean_text(text)

        directory = self.log_dir(server)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{now.strftime('%Y-%m-%d')}.log"
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] [{safe_kind}] {safe_text}\n")

        return HistoryEntry(stamp, safe_kind, safe_text)

    async def record(
        self,
        server: "ManagedServer",
        *,
        kind: str,
        text: str,
        timestamp: datetime | None = None,
    ) -> HistoryEntry:
        return await asyncio.to_thread(
            self._append_sync,
            server,
            kind=kind,
            text=text,
            timestamp=timestamp,
        )

    async def record_line(self, server: "ManagedServer", line: str) -> HistoryEntry | None:
        event = parse_minecraft_event(line)
        if event is None:
            return None

        now = datetime.now().astimezone()
        try:
            hour, minute, second = (int(part) for part in event.timestamp.split(":"))
            occurred_at = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        except (TypeError, ValueError):
            occurred_at = now

        return await self.record(
            server,
            kind=event.kind,
            text=event_plain_text(event),
            timestamp=occurred_at,
        )

    async def record_action(
        self,
        server: "ManagedServer",
        *,
        action: str,
        result: str,
        source: str,
        actor: str = "",
    ) -> HistoryEntry | None:
        changed = {
            "start": {"started"},
            "stop": {"stopped", "killed"},
            "restart": {"started"},
        }
        if action not in changed or result not in changed[action]:
            return None

        kind = {
            "start": "server_start",
            "stop": "server_stop",
            "restart": "server_restart",
        }[action]
        label = {
            "start": "Сервер запущен",
            "stop": "Сервер остановлен",
            "restart": "Сервер перезапущен",
        }[action]
        if result == "killed":
            label += " принудительно"

        context = source
        if actor:
            context += f" · {actor}"
        return await self.record(server, kind=kind, text=f"{label} · {context}")

    async def record_auto_stop(
        self,
        server: "ManagedServer",
        *,
        timeout_seconds: int,
        result: str,
    ) -> HistoryEntry:
        if timeout_seconds % 60 == 0:
            duration = f"{timeout_seconds // 60} мин"
        else:
            duration = f"{timeout_seconds} сек"
        return await self.record(
            server,
            kind="auto_stop",
            text=f"Сервер автоматически остановлен после {duration} без игроков · result={result}",
        )

    def _recent_sync(self, server: "ManagedServer", limit: int) -> list[HistoryEntry]:
        limit = max(1, min(int(limit), 100))
        directory = self.log_dir(server)
        if not directory.exists():
            return []

        result: list[HistoryEntry] = []
        for path in sorted(directory.glob("*.log"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for line in reversed(lines):
                match = _HISTORY_LINE_RE.match(line)
                if match is None:
                    continue
                result.append(
                    HistoryEntry(
                        timestamp=match.group("timestamp"),
                        kind=match.group("kind"),
                        text=match.group("text"),
                    )
                )
                if len(result) >= limit:
                    return list(reversed(result))

        return list(reversed(result))

    async def recent(self, server: "ManagedServer", limit: int = 20) -> list[HistoryEntry]:
        return await asyncio.to_thread(self._recent_sync, server, limit)

    @staticmethod
    def render(entries: list[HistoryEntry], max_chars: int = 3200) -> str:
        if not entries:
            return "<i>Событий пока нет.</i>"

        rendered: list[str] = []
        used = 0
        for entry in entries:
            short_time = entry.timestamp[11:16] if len(entry.timestamp) >= 16 else entry.timestamp
            icon = _KIND_ICON.get(entry.kind, "📌")
            text = html.escape(entry.text)
            if len(text) > 240:
                text = text[:237] + "…"
            line = f"<code>{html.escape(short_time)}</code> {icon} {text}"
            if used + len(line) + 1 > max_chars:
                while rendered and used + len(line) + 1 > max_chars:
                    removed = rendered.pop(0)
                    used -= len(removed) + 1
            rendered.append(line)
            used += len(line) + 1

        return "\n".join(rendered)


event_history = EventHistory()
