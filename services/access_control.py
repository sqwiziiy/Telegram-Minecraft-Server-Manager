from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import ACCESS_USERS_FILE, LEGACY_ADMIN_IDS, OWNER_IDS

logger = logging.getLogger(__name__)

ALL_PERMISSIONS = frozenset({
    "server.status",
    "server.start",
    "server.stop",
    "server.restart",
    "system.view",
    "logs.view",
    "console.use",
    "mods.view",
    "mods.upload",
    "mods.delete",
    "backup.create",
    "backup.view",
})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS,
    "operator": frozenset({
        "server.status",
        "server.start",
        "server.stop",
        "server.restart",
        "mods.view",
    }),
    "viewer": frozenset({
        "server.status",
        "mods.view",
    }),
    "custom": frozenset(),
}


@dataclass(frozen=True)
class UserAccess:
    user_id: int
    name: str
    role: str
    allow: frozenset[str]
    deny: frozenset[str]


class AccessControl:
    """Loads Telegram users and resolves effective permissions.

    Security rules:
    - owners and legacy admins always have full access;
    - unknown users have no access;
    - unknown roles do not grant permissions;
    - unknown permission names are ignored;
    - deny always overrides role/default/allow permissions.
    """

    def __init__(
        self,
        *,
        owner_ids: Iterable[int],
        legacy_admin_ids: Iterable[int] = (),
        users_file: str | Path,
    ) -> None:
        self.owner_ids = frozenset(int(i) for i in owner_ids)
        self.legacy_admin_ids = frozenset(int(i) for i in legacy_admin_ids)
        self.users_file = Path(users_file)
        self._users: dict[int, UserAccess] = {}
        self.reload()

    @staticmethod
    def _known_permissions(values: object) -> frozenset[str]:
        if not isinstance(values, list):
            return frozenset()
        return frozenset(
            value
            for value in values
            if isinstance(value, str) and value in ALL_PERMISSIONS
        )

    def reload(self) -> None:
        users: dict[int, UserAccess] = {}

        if not self.users_file.exists():
            self._users = users
            return

        try:
            payload = json.loads(self.users_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Failed to load access users file %s: %s", self.users_file, exc)
            self._users = users
            return

        raw_users = payload.get("users") if isinstance(payload, dict) else None
        if not isinstance(raw_users, dict):
            logger.error("Access users file %s must contain an object named 'users'", self.users_file)
            self._users = users
            return

        for raw_id, raw_user in raw_users.items():
            try:
                user_id = int(raw_id)
            except (TypeError, ValueError):
                logger.warning("Ignoring access entry with invalid Telegram id: %r", raw_id)
                continue

            if user_id <= 0 or not isinstance(raw_user, dict):
                logger.warning("Ignoring malformed access entry for Telegram id %r", raw_id)
                continue

            role = raw_user.get("role", "custom")
            if not isinstance(role, str) or role not in ROLE_PERMISSIONS:
                logger.warning("Ignoring access entry %s with unknown role %r", user_id, role)
                continue

            name = raw_user.get("name", "")
            if not isinstance(name, str):
                name = ""

            users[user_id] = UserAccess(
                user_id=user_id,
                name=name.strip(),
                role=role,
                allow=self._known_permissions(raw_user.get("allow", [])),
                deny=self._known_permissions(raw_user.get("deny", [])),
            )

        self._users = users

    def is_owner(self, user_id: int) -> bool:
        return user_id in self.owner_ids

    def is_authorized(self, user_id: int) -> bool:
        return (
            user_id in self.owner_ids
            or user_id in self.legacy_admin_ids
            or user_id in self._users
        )

    def permissions_for(self, user_id: int) -> frozenset[str]:
        if user_id in self.owner_ids or user_id in self.legacy_admin_ids:
            return ALL_PERMISSIONS

        user = self._users.get(user_id)
        if user is None:
            return frozenset()

        permissions = set(ROLE_PERMISSIONS[user.role])
        permissions.update(user.allow)
        permissions.difference_update(user.deny)
        return frozenset(permissions)

    def can(self, user_id: int, permission: str) -> bool:
        if permission not in ALL_PERMISSIONS:
            return False
        return permission in self.permissions_for(user_id)

    def users_with_permission(self, permission: str) -> frozenset[int]:
        if permission not in ALL_PERMISSIONS:
            return frozenset()

        result = set(self.owner_ids) | set(self.legacy_admin_ids)
        result.update(
            user_id
            for user_id in self._users
            if self.can(user_id, permission)
        )
        return frozenset(result)

    def user(self, user_id: int) -> UserAccess | None:
        return self._users.get(user_id)


access_control = AccessControl(
    owner_ids=OWNER_IDS,
    legacy_admin_ids=LEGACY_ADMIN_IDS,
    users_file=ACCESS_USERS_FILE,
)
