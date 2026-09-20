import asyncio
import logging
import struct

from config import RCON_HOST, RCON_PASSWORD, RCON_PORT

logger = logging.getLogger(__name__)

_MAX_RESPONSE_LEN = 3800
_MAX_PACKET_BYTES = 4 * 1024 * 1024
_TYPE_AUTH = 3
_TYPE_CMD = 2
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 10.0


def _pack(req_id: int, pkt_type: int, payload: str) -> bytes:
    body = payload.encode("utf-8")
    length = 4 + 4 + len(body) + 2
    return struct.pack("<iii", length, req_id, pkt_type) + body + b"\x00\x00"


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    header = await asyncio.wait_for(reader.readexactly(4), timeout=_READ_TIMEOUT)
    length = struct.unpack("<i", header)[0]
    if length < 10 or length > _MAX_PACKET_BYTES:
        raise ValueError(f"Invalid RCON packet length: {length}")

    data = await asyncio.wait_for(reader.readexactly(length), timeout=_READ_TIMEOUT)
    req_id, pkt_type = struct.unpack_from("<ii", data)
    payload = data[8:-2].decode("utf-8", errors="replace")
    return req_id, pkt_type, payload


async def send_rcon_command(command: str) -> str:
    if not RCON_PASSWORD:
        return "❌ RCON_PASSWORD не настроен."

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(RCON_HOST, RCON_PORT),
            timeout=_CONNECT_TIMEOUT,
        )
    except (ConnectionRefusedError, OSError) as exc:
        return f"❌ RCON недоступен ({RCON_HOST}:{RCON_PORT}): {exc}"
    except asyncio.TimeoutError:
        return "❌ Таймаут подключения к RCON."

    try:
        writer.write(_pack(1, _TYPE_AUTH, RCON_PASSWORD))
        await writer.drain()
        req_id, _, _ = await _read_packet(reader)
        if req_id == -1:
            return "❌ Неверный RCON-пароль."

        writer.write(_pack(2, _TYPE_CMD, command))
        await writer.drain()
        req_id, _, response = await _read_packet(reader)
        if req_id not in {2, -1}:
            logger.warning("Unexpected RCON response id: %s", req_id)

        text = response.strip() or "(нет ответа)"
        if len(text) > _MAX_RESPONSE_LEN:
            text = text[:_MAX_RESPONSE_LEN] + "\n…(обрезано)"
        return text

    except asyncio.TimeoutError:
        return "❌ Таймаут ожидания ответа RCON."
    except (ValueError, asyncio.IncompleteReadError) as exc:
        logger.warning("Malformed RCON response: %s", exc)
        return "❌ Сервер вернул некорректный RCON-ответ."
    except Exception as exc:  # noqa: BLE001
        logger.exception("RCON error")
        return f"❌ Ошибка RCON: {exc}"
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
