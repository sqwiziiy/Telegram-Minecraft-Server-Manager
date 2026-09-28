from __future__ import annotations

import html
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

_LOG_PREFIX_RE = re.compile(
    r"^(?:\[(?P<time>\d{2}:\d{2}:\d{2})\]\s*)?"
    r"(?:\[[^\]]+\]\s*)*"
    r"(?:\[[^\]]+\]:\s*)?"
    r"(?P<payload>.*)$"
)
_JOIN_RE = re.compile(r"^(?P<player>\S+) joined the game$")
_LEAVE_RE = re.compile(r"^(?P<player>\S+) left the game$")
_CHAT_RE = re.compile(r"^<(?P<player>[^>]{1,64})>\s*(?P<message>.*)$")

# Vanilla death messages are intentionally matched by phrases rather than by
# trying to maintain a complete translation table. The feed strips the noisy
# Minecraft log prefix while preserving the exact death reason.
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

_STALE_EDIT_MARKERS = (
    "message to edit not found",
    "message can't be edited",
    "message identifier is not specified",
    "message_id_invalid",
)


@dataclass(frozen=True)
class MinecraftEvent:
    kind: str
    timestamp: str
    player: str = ""
    detail: str = ""


def _extract_payload(line: str) -> tuple[str, str]:
    match = _LOG_PREFIX_RE.match(line.strip())
    if match is None:
        return datetime.now().astimezone().strftime("%H:%M"), line.strip()

    raw_time = match.group("time")
    timestamp = raw_time[:5] if raw_time else datetime.now().astimezone().strftime("%H:%M")
    return timestamp, match.group("payload").strip()


def parse_minecraft_event(line: str) -> MinecraftEvent | None:
    """Parse one interesting latest.log line into a compact feed event."""
    timestamp, payload = _extract_payload(line)
    if not payload:
        return None

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


def render_event(event: MinecraftEvent) -> str:
    timestamp = html.escape(event.timestamp)
    player = html.escape(event.player)

    if event.kind == "join":
        body = f"🟢 <b>{player}</b> зашёл на сервер"
    elif event.kind == "leave":
        body = f"🔴 <b>{player}</b> вышел с сервера"
    elif event.kind == "chat":
        body = f"💬 <b>{player}</b>: {html.escape(event.detail)}"
    elif event.kind == "death":
        body = f"💀 {html.escape(event.detail)}"
    else:
        body = f"📋 {html.escape(event.detail)}"

    return f"<code>{timestamp}</code> {body}"


class EventFeed:
    """Maintains one editable Telegram event-feed message per user/server."""

    def __init__(self, max_events: int = 10) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self.max_events = max_events
        self._events: dict[str, deque[MinecraftEvent]] = defaultdict(
            lambda: deque(maxlen=self.max_events)
        )
        self._message_ids: dict[tuple[int, str], int] = {}

    def add_line(self, server_id: str, line: str) -> MinecraftEvent | None:
        event = parse_minecraft_event(line)
        if event is not None:
            self._events[server_id].append(event)
        return event

    def render(self, server_id: str, server_name: str) -> str:
        events = list(self._events.get(server_id, ()))
        title = f"📋 <b>События · {html.escape(server_name)}</b>"

        if not events:
            return title + "\n\n<i>Событий пока нет.</i>"

        body = "\n".join(render_event(event) for event in events)
        return (
            f"{title}\n\n{body}\n\n"
            f"<i>Последние {len(events)} из {self.max_events} событий</i>"
        )

    async def _send_new(self, bot, user_id: int, server_id: str, text: str) -> None:
        message = await bot.send_message(
            user_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        self._message_ids[(user_id, server_id)] = message.message_id

    async def _upsert(
        self,
        bot,
        user_id: int,
        server_id: str,
        text: str,
    ) -> None:
        key = (user_id, server_id)
        message_id = self._message_ids.get(key)

        if message_id is None:
            await self._send_new(bot, user_id, server_id, text)
            return

        try:
            await bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as exc:
            error = str(exc).lower()
            if "message is not modified" in error:
                return
            if not any(marker in error for marker in _STALE_EDIT_MARKERS):
                raise

            logger.info(
                "Event feed message %s for user %s/server %s is stale; creating a new one",
                message_id,
                user_id,
                server_id,
            )
            self._message_ids.pop(key, None)
            await self._send_new(bot, user_id, server_id, text)

    async def publish_line(
        self,
        bot,
        *,
        server_id: str,
        server_name: str,
        line: str,
        user_ids: Iterable[int],
    ) -> bool:
        """Append an event and refresh one feed message for every recipient."""
        event = self.add_line(server_id, line)
        if event is None:
            return False

        text = self.render(server_id, server_name)
        for user_id in sorted(set(user_ids)):
            try:
                await self._upsert(bot, user_id, server_id, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to update event feed for user %s/server %s: %s",
                    user_id,
                    server_id,
                    exc,
                )
        return True


event_feed = EventFeed(max_events=10)
