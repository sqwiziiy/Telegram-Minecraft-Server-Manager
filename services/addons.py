"""Safe, reversible .jar toggles for local Minecraft mods and plugins."""
from __future__ import annotations

import asyncio
import os
import re
import stat
from pathlib import Path

# Disabled addons deliberately do not end with .jar and cannot be loaded
# automatically by Minecraft mod/plugin loaders.
_ADDON_RE = re.compile(r"^[A-Za-z0-9_\-. +\[\]()@#]+\.jar(?:\.disabled)?$")


def is_addon_name(name: str) -> bool:
    return bool(_ADDON_RE.fullmatch(name))


def is_disabled(name: str) -> bool:
    return name.endswith(".jar.disabled")


def list_addons(folder: str) -> list[str] | None:
    root = Path(folder)
    if not root.is_dir():
        return None
    return sorted(
        file.name for file in root.iterdir()
        if is_addon_name(file.name) and file.is_file() and not file.is_symlink()
    )


def _toggle_sync(folder: str, filename: str) -> str:
    """Never unlink the only copy or overwrite a pre-existing target.

    Linking then unlinking is safe for files on a single Linux filesystem.
    Holding the Minecraft manager's start/stop lock keeps the brief
    two-name window from overlapping any bot-driven server start.
    """
    if not is_addon_name(filename):
        raise ValueError("Invalid addon filename")
    root = Path(folder).resolve(strict=True)
    source = root / filename
    target = root / (
        filename.removesuffix(".disabled")
        if is_disabled(filename) else filename + ".disabled"
    )
    source_stat = source.lstat()  # fails closed for missing files
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("Addon must be a regular file, not a symlink")
    if os.path.lexists(target):
        raise FileExistsError(f"Target already exists: {target.name}")

    # os.link refuses to overwrite an existing target even if it appears
    # between the existence check and the link call.
    os.link(source, target, follow_symlinks=False)
    try:
        linked = target.lstat()
        current = source.lstat()
        if (
            not stat.S_ISREG(linked.st_mode)
            or linked.st_ino != source_stat.st_ino
            or linked.st_dev != source_stat.st_dev
            or current.st_ino != source_stat.st_ino
            or current.st_dev != source_stat.st_dev
        ):
            raise RuntimeError("Addon changed while toggling; refusing to remove source")
        source.unlink()
    except BaseException:
        # Undo the new hard link if the source could not be removed.
        try:
            linked_now = target.lstat()
            if linked_now.st_ino == source_stat.st_ino and linked_now.st_dev == source_stat.st_dev:
                target.unlink()
        except FileNotFoundError:
            pass
        raise
    return target.name


async def toggle_addon(server, filename: str) -> tuple[bool, str | None]:
    """Require a stopped server. For SSH, host-side agent rechecks under its lock."""
    if not is_addon_name(filename):
        raise ValueError("Invalid addon filename")

    async def action() -> str:
        if server.ssh_remote is not None:
            return await server.ssh_remote.request("toggle_mod", filename=filename, timeout=40)
        return await asyncio.to_thread(_toggle_sync, server.mods_dir, filename)

    return await server.manager.run_if_stopped(action)
