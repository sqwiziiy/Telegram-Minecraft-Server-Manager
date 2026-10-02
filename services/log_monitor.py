import asyncio
import logging
import os
from typing import AsyncGenerator

from services.auto_stop import is_server_ready_line
from services.event_feed import parse_minecraft_event

logger = logging.getLogger(__name__)


def _is_interesting(line: str) -> bool:
    return parse_minecraft_event(line) is not None or is_server_ready_line(line)


async def tail_log(path: str) -> AsyncGenerator[str, None]:
    """Follow latest.log and yield join/leave/chat/death/server-ready events.

    The first open starts at EOF so historical events are not replayed. Log
    rotation and in-place truncation are handled without following stale data.
    """
    fh = None
    identity: tuple[int, int] | None = None
    first_open = True
    try:
        while True:
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                if fh is None:
                    raise
                # Stop following the renamed old inode during rotation.
                fh.close()
                fh = None
                identity = None
                await asyncio.sleep(0.5)
                continue

            current_identity = (stat.st_dev, stat.st_ino)
            if fh is None or identity != current_identity:
                if fh is not None:
                    fh.close()
                try:
                    fh = open(path, encoding="utf-8", errors="replace")
                except FileNotFoundError:
                    await asyncio.sleep(0.5)
                    continue
                identity = current_identity
                if first_open:
                    fh.seek(0, 2)  # Do not replay historical lines initially.
                    first_open = False
                else:
                    # A replacement log is a new stream; read its current data.
                    fh.seek(0)
            elif stat.st_size < fh.tell():
                # Minecraft may truncate the file in place on restart.
                fh.seek(0)

            line = fh.readline()
            if line:
                line = line.rstrip()
                if line and _is_interesting(line):
                    yield line
            else:
                await asyncio.sleep(0.5)
    finally:
        if fh is not None:
            fh.close()
