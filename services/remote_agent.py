"""Standalone SSH-side Minecraft agent (Python 3.10+, no pip dependencies).

Executed over an authenticated SSH connection. Commands are read from stdin
as JSON; all credentials remain out of shell command arguments. The agent is
NOT a network daemon and never listens on an HTTP port.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import struct
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

_AUTO_RE = re.compile(r"^world_auto_backup_\d{8}_\d{6}_\d{6}\.zip$")
_MOD_RE = re.compile(r"^[A-Za-z0-9_\-. +\[\]()@#]+\.jar$")


def path(value):
    p = Path(value).expanduser()
    if not p.is_absolute():
        raise ValueError("Only absolute remote paths are supported")
    return p


def process_alive(pid, started):
    try:
        stat = Path("/proc") / str(pid) / "stat"
        raw = stat.read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return fields[0] != "Z" and int(fields[19]) == int(started)
    except (OSError, IndexError, ValueError):
        return False


def pid_record(config):
    name = path(config["pid_file"])
    try:
        record = json.loads(name.read_text())
        pid = int(record["pid"])
        started = int(record["start_ticks"])
        if process_alive(pid, started):
            return pid, started
    except (OSError, KeyError, ValueError, TypeError):
        pass
    return None


def rcon_available(config):
    # Also test Minecraft's game port: a JVM launched outside this manager
    # with RCON disabled must not be mistaken for an offline world.
    ports = (int(config["rcon_port"]), int(config.get("minecraft_port", 25565)))
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.6):
                return True
        except OSError:
            pass
    return False


def get_status(config):
    record = pid_record(config)
    if record is None:
        if rcon_available(config):
            return {"running": True, "pid": None, "uptime_seconds": 0, "memory_mb": 0.0}
        return {"running": False, "pid": None, "uptime_seconds": 0, "memory_mb": 0.0}
    pid, started = record
    uptime = 0
    rss = 0
    try:
        ticks = os.sysconf("SC_CLK_TCK")
        uptime = int(float(Path("/proc/uptime").read_text().split()[0]) - started / ticks)
        statm = Path(f"/proc/{pid}/statm").read_text().split()
        rss = int(statm[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576
    except (OSError, ValueError, IndexError):
        pass
    return {"running": True, "pid": pid, "uptime_seconds": max(0, uptime), "memory_mb": rss}


def ticks_for(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    return int(raw[raw.rfind(")") + 2:].split()[19])


def launch(config):
    if get_status(config)["running"]:
        return "already_running"
    folder = path(config["server_dir"])
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))
    argv = shlex.split(config["start_command"])
    if not argv:
        raise ValueError("Empty start command")
    script = Path(argv[0])
    if script.suffix.lower() in (".sh", ".bash"):
        script = script if script.is_absolute() else folder / script
        if not script.is_file():
            raise FileNotFoundError(f"Start script missing: {script}")
        argv = ["/bin/bash", str(script), *argv[1:]]
    log = path(config["output_log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab", buffering=0) as output:
        child = subprocess.Popen(
            argv, cwd=folder, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
        )
    time.sleep(0.8)
    if child.poll() is not None:
        raise RuntimeError(f"Minecraft exited early ({child.returncode}); check {log}")
    record_path = path(config["pid_file"])
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = record_path.with_name(record_path.name + ".tmp")
    temporary.write_text(json.dumps({"pid": child.pid, "start_ticks": ticks_for(child.pid)}))
    os.replace(temporary, record_path)
    return "started"


def _recv(sock, n):
    result = b""
    while len(result) < n:
        part = sock.recv(n - len(result))
        if not part:
            raise ConnectionError("RCON connection closed")
        result += part
    return result


def rcon(config, command):
    secret = config.get("rcon_password")
    if not secret:
        return "❌ RCON-пароль не настроен."
    def packet(rid, ptype, data):
        value = data.encode("utf-8")
        return struct.pack("<iii", len(value) + 10, rid, ptype) + value + b"\x00\x00"
    def response(sock):
        length = struct.unpack("<i", _recv(sock, 4))[0]
        if length < 10 or length > 4194304:
            raise ValueError("Invalid RCON packet length")
        data = _recv(sock, length)
        rid, _ = struct.unpack_from("<ii", data)
        return rid, data[8:-2].decode("utf-8", "replace")
    try:
        with socket.create_connection(("127.0.0.1", int(config["rcon_port"])), 10) as sock:
            sock.settimeout(10)
            sock.sendall(packet(1, 3, secret))
            rid, _ = response(sock)
            if rid == -1:
                return "❌ Неверный RCON-пароль."
            sock.sendall(packet(2, 2, command))
            rid, text = response(sock)
            if rid == -1:
                return "❌ RCON-команда отклонена."
            return (text.strip() or "(нет ответа)")[:3800]
    except (OSError, ValueError) as exc:
        return f"❌ RCON недоступен: {exc}"


def terminate(config):
    state = get_status(config)
    if not state["running"]:
        return "already_stopped"
    answer = rcon(config, "stop")
    pid = state["pid"]
    if pid is None:
        if answer.startswith("❌"):
            raise RuntimeError("Unmanaged running server: RCON stop failed")
        deadline = time.monotonic() + max(5, float(config.get("stop_timeout", 45)))
        while time.monotonic() < deadline:
            if not rcon_available(config):
                return "stopped"
            time.sleep(0.5)
        raise RuntimeError("Unmanaged server is still running")
    started = pid_record(config)[1]
    timeout = max(5, float(config.get("stop_timeout", 45)))
    if not answer.startswith("❌"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not process_alive(pid, started):
                return "stopped"
            time.sleep(0.5)
    if answer.startswith("❌"):
        # RCON could close after receiving 'stop'. Give Minecraft a chance
        # to finish saving before falling back to operating-system signals.
        deadline = time.monotonic() + min(timeout, 8.0)
        while time.monotonic() < deadline:
            if not process_alive(pid, started):
                return "stopped"
            time.sleep(0.5)
    for sig, delay in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        if not process_alive(pid, started):
            return "stopped"
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            return "stopped"
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if not process_alive(pid, started):
                return "stopped" if sig == signal.SIGTERM else "killed"
            time.sleep(0.25)
    return "killed"


def list_mods(config):
    folder = path(config["mods_dir"])
    if not folder.is_dir():
        return None
    return sorted(p.name for p in folder.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() == ".jar")


def mod_path(config, filename):
    if not _MOD_RE.fullmatch(filename):
        raise ValueError("Invalid mod filename")
    folder = path(config["mods_dir"]).resolve(strict=True)
    dest = folder / filename
    if dest.is_symlink() or dest.exists() and not dest.is_file():
        raise ValueError("Unexpected mod target")
    return folder, dest


def remove_mod(config, filename):
    _, dest = mod_path(config, filename)
    if not dest.exists():
        return False
    dest.unlink()
    return True


def commit_mod(config, filename, temporary_name):
    _, dest = mod_path(config, filename)
    folder = path(config["mods_dir"]).resolve(strict=True)
    if not re.fullmatch(r"\.upload-[a-f0-9]{24}", temporary_name):
        raise ValueError("Invalid upload temporary name")
    temp = folder / temporary_name
    if temp.is_symlink() or not temp.is_file():
        raise ValueError("Missing temporary uploaded mod")
    try:
        os.link(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return filename


def tail_file(config, lines, key):
    filename = path(config[key])
    if not filename.exists():
        return ""
    with filename.open("r", encoding="utf-8", errors="replace") as fh:
        return "".join(fh.readlines()[-max(1, min(100, int(lines))):]).strip()


def make_backup(config, automatic):
    if automatic and get_status(config)["running"]:
        raise RuntimeError("Automatic backup requires an offline server")
    if not automatic and get_status(config)["running"]:
        answer = rcon(config, "save-off")
        if answer.startswith("❌"):
            raise RuntimeError("Server active, cannot pause saves via RCON")
        paused = True
    else:
        paused = False
    try:
        if paused:
            flush = rcon(config, "save-all flush")
            if flush.startswith("❌"):
                raise RuntimeError("Minecraft world flush failed")
        world = path(config["world_dir"])
        if not world.is_dir():
            raise FileNotFoundError(f"World directory not found: {world}")
        backups = path(config["backup_dir"])
        backups.mkdir(parents=True, exist_ok=True)
        prefix = "world_auto_backup_" if automatic else "world_backup_"
        archive = backups / (prefix + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".zip")
        try:
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zipf:
                for root, directories, files in os.walk(world, followlinks=False):
                    directories[:] = [d for d in directories if not (Path(root) / d).is_symlink()]
                    for file in files:
                        item = Path(root) / file
                        if item.is_symlink() or not item.is_file():
                            continue
                        zipf.write(item, arcname=str(item.relative_to(world.parent)))
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
        size = archive.stat().st_size
        removed = 0
        within = True
        if automatic:
            max_count = int(config.get("backup_retention_max_count", 0))
            max_gb = float(config.get("backup_retention_max_gb", 0))
            entries = sorted((f for f in backups.iterdir() if _AUTO_RE.fullmatch(f.name) and f.is_file() and not f.is_symlink()), key=lambda p: p.name)
            total = sum(item.stat().st_size for item in entries)
            while (max_count > 0 and len(entries) > max_count) or (max_gb > 0 and total > max_gb * 1024**3):
                oldest = next((f for f in entries if f != archive), None)
                if oldest is None:
                    within = False
                    break
                amount = oldest.stat().st_size
                oldest.unlink()
                total -= amount
                entries.remove(oldest)
                removed += 1
        return {"path": str(archive), "size_mb": round(size / 1048576, 1), "deleted_count": removed, "within_limits": within}
    finally:
        if paused:
            result = rcon(config, "save-on")
            if result.startswith("❌"):
                print("WARNING: Minecraft saves could not be re-enabled", file=sys.stderr)


def properties(config):
    folder = path(config["server_dir"])
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))
    filename = folder / "server.properties"
    return filename.read_text(encoding="utf-8") if filename.is_file() else ""


def configure_properties(config, content):
    folder = path(config["server_dir"])
    if not folder.is_dir():
        raise FileNotFoundError(str(folder))
    filename = folder / "server.properties"
    original = filename.read_text(encoding="utf-8") if filename.exists() else ""
    if original != config.get("expected_properties", ""):
        raise RuntimeError("server.properties changed during setup")
    file_mode = filename.stat().st_mode & 0o777 if filename.exists() else 0o644
    tmp = folder / ".server.properties.manager.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, filename)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def _safe_file(config, relative):
    root = path(config["server_dir"]).resolve(strict=True)
    item = Path(relative)
    if item.is_absolute():
        raise ValueError("path must be relative")
    target = (root / item).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path escapes server directory")
    return root, target


def _sensitive_file(target):
    if ".git" in {part.casefold() for part in target.parts}:
        return True
    if target.name.casefold() in {".env", "credentials.json", "token.json", "accounts.json"}:
        return True
    return target.suffix.casefold() in {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}


def browse_files(config, relative, recursive, limit):
    root, folder = _safe_file(config, relative)
    if not folder.is_dir():
        raise FileNotFoundError("Remote directory does not exist")
    entries = []
    listing = folder.rglob("*") if recursive else folder.iterdir()
    for entry in listing:
        if len(entries) >= limit:
            break
        try:
            candidate = entry.resolve()
            if candidate != root and root not in candidate.parents:
                continue
            if not candidate.exists():
                continue
            stat = candidate.stat()
            entries.append({
                "path": str(candidate.relative_to(root)),
                "name": candidate.name,
                "type": "directory" if candidate.is_dir() else "file",
                "size_bytes": stat.st_size if candidate.is_file() else None,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            })
        except (OSError, ValueError):
            continue
    entries.sort(key=lambda value: (value["type"] != "directory", value["path"].casefold()))
    return {"path": str(folder.relative_to(root)) or ".", "recursive": recursive,
            "truncated": len(entries) >= limit, "entries": entries}


def read_file(config, relative, max_bytes):
    import gzip
    root, target = _safe_file(config, relative)
    if not target.is_file():
        raise FileNotFoundError("Remote file does not exist")
    if _sensitive_file(target):
        raise PermissionError("Sensitive files are not available via Control API")
    opener = gzip.open if target.suffix.lower() == ".gz" else open
    with opener(target, "rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("File is too large to read")
    if b"\x00" in raw[:8192]:
        raise ValueError("Binary file is not readable via this endpoint")
    return {"path": str(target.relative_to(root)),
            "gzip_decompressed": target.suffix.lower() == ".gz",
            "content": raw.decode("utf-8", "replace")}


def dispatch(request):
    action = request["action"]
    config = request["config"]
    if action == "probe":
        folder = path(config["server_dir"])
        if not folder.is_dir():
            raise FileNotFoundError(str(folder))
        return {"properties": properties(config), "launchers": [name for name in ("start-server.sh", "start.sh", "run.sh") if (folder / name).is_file()], "python": sys.version.split()[0]}
    if action == "port_available":
        port = int(request["port"])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid TCP port")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False
    if action == "configure_properties":
        expected_config = {**config, "expected_properties": request["expected_properties"]}
        return configure_properties(expected_config, request["content"])
    if action == "status":
        return get_status(config)
    if action == "start":
        return launch(config)
    if action == "stop":
        return terminate(config)
    if action == "restart":
        terminate(config)
        return launch(config)
    if action == "rcon":
        return rcon(config, request["command"])
    if action == "tail_output":
        return tail_file(config, request.get("lines", 30), "output_log")
    if action == "list_mods":
        return list_mods(config)
    if action == "delete_mod":
        return remove_mod(config, request["filename"])
    if action == "commit_mod":
        return commit_mod(config, request["filename"], request["temporary_name"])
    if action == "backup":
        return make_backup(config, bool(request.get("automatic", False)))
    if action == "list_files":
        return browse_files(config, request.get("path", "."), bool(request.get("recursive", False)),
                            max(1, min(1000, int(request.get("max_entries", 200)))))
    if action == "read_file":
        return read_file(config, request["path"], max(1024, min(4*1024*1024, int(request.get("max_bytes", 1048576)))))
    raise ValueError(f"Unsupported remote action: {action}")


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        print(json.dumps({"ok": True, "value": dispatch(payload)}, ensure_ascii=False))
    except BaseException as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(1)
