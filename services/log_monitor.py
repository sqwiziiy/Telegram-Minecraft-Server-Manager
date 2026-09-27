import asyncio
import logging
import os
import re
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# Паттерны строк, которые нужно пересылать в чат
_PATTERNS: list[re.Pattern] = [
    re.compile(r"joined the game"),
    re.compile(r"left the game"),
    # Сообщения чата вида: <PlayerName> текст
    re.compile(r"\[Server thread/INFO\].*?:\s+<\w"),
    # Смерти игроков (стандартные фразы Minecraft)
    re.compile(
        r"\[Server thread/INFO\].*?: \w.+? (was slain|was shot|drowned|starved to death"
        r"|fell|burned|died|hit the ground|was killed|suffocated|was squashed"
        r"|blew up|was fireballed|was pricked|walked into)",
        re.IGNORECASE,
    ),
]


def _is_interesting(line: str) -> bool:
    return any(p.search(line) for p in _PATTERNS)


async def tail_log(path: str) -> AsyncGenerator[str, None]:
    """Асинхронный генератор: читает лог-файл как `tail -f` и
    отдаёт строки, соответствующие паттернам (вход/выход, чат, смерти).

    При отсутствии файла или ошибке чтения поднимает исключение
    (обработка — на стороне вызывающего кода).
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
