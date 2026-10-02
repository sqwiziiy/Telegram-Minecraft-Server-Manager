from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config import ACCESS_USERS_FILE, DEFAULT_SERVER_ID, LEGACY_ADMIN_IDS, OWNER_IDS

logger = logging.getLogger(__name__)

ALL_PERMISSIONS = frozenset({
    "server.status",
    "server.start",
    "server.stop",
    "server.restart",
    "server.autostop",
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
        "server.autostop",
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
    servers: dict[str, "UserAccess"] | None = None


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

            name = raw_user.get("name", "")
            if not isinstance(name, str):
                name = ""

            def parse_access(raw: object, *, default_role: str = "custom") -> UserAccess | None:
                if not isinstance(raw, dict):
                    return None
                role = raw.get("role", default_role)
                if not isinstance(role, str) or role not in ROLE_PERMISSIONS:
                    logger.warning("Ignoring access entry %s with unknown role %r", user_id, role)
                    return None
                return UserAccess(
                    user_id=user_id,
                    name=name.strip(),
                    role=role,
                    allow=self._known_permissions(raw.get("allow", [])),
                    deny=self._known_permissions(raw.get("deny", [])),
                )

            raw_servers = raw_user.get("servers")
            if isinstance(raw_servers, dict):
                server_access: dict[str, UserAccess] = {}
                for raw_server_id, raw_policy in raw_servers.items():
                    if not isinstance(raw_server_id, str) or not raw_server_id.strip():
                        continue
                    policy = parse_access(raw_policy)
                    if policy is not None:
                        server_access[raw_server_id.strip()] = policy
                # A server-map user is valid even when all entries are malformed,
                # but malformed policies grant no permissions.
                users[user_id] = UserAccess(
                    user_id=user_id, name=name.strip(), role="custom",
                    allow=frozenset(), deny=frozenset(), servers=server_access,
                )
                continue

            legacy = parse_access(raw_user, default_role="custom")
            if legacy is None:
                continue

            users[user_id] = UserAccess(
                user_id=legacy.user_id, name=legacy.name, role=legacy.role,
                allow=legacy.allow, deny=legacy.deny,
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
        return self.permissions_for_server(user_id, DEFAULT_SERVER_ID)

    def permissions_for_server(self, user_id: int, server_id: str) -> frozenset[str]:
        if user_id in self.owner_ids or user_id in self.legacy_admin_ids:
            return ALL_PERMISSIONS

        user = self._users.get(user_id)
        if user is None:
            return frozenset()

        if user.servers is not None:
            policy = user.servers.get(server_id)
            if policy is None:
                return frozenset()
            user = policy
        elif server_id != DEFAULT_SERVER_ID:
            # v1.0 policies were global, but granting them to newly added
            # servers would be an unsafe privilege expansion.
            return frozenset()

        permissions = set(ROLE_PERMISSIONS[user.role])
        permissions.update(user.allow)
        permissions.difference_update(user.deny)
        return frozenset(permissions)

    def can(self, user_id: int, permission: str) -> bool:
        return self.can_server(user_id, DEFAULT_SERVER_ID, permission)

    def can_server(self, user_id: int, server_id: str, permission: str) -> bool:
        if permission not in ALL_PERMISSIONS:
            return False
        if permission == "system.view":
            return self.can_system(user_id)
        return permission in self.permissions_for_server(user_id, server_id)

    def can_system(self, user_id: int) -> bool:
        """Host information is global and restricted to owners/admins."""
        return user_id in self.owner_ids or user_id in self.legacy_admin_ids

    def server_ids_for(self, user_id: int, available_server_ids: Iterable[str]) -> list[str]:
        return [
            server_id for server_id in available_server_ids
            if self.permissions_for_server(user_id, server_id)
        ]

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

    def users_with_server_permission(self, server_id: str, permission: str) -> frozenset[int]:
        if permission not in ALL_PERMISSIONS:
            return frozenset()
        result = set(self.owner_ids) | set(self.legacy_admin_ids)
        result.update(
            user_id for user_id in self._users
            if self.can_server(user_id, server_id, permission)
        )
        return frozenset(result)

    def user(self, user_id: int) -> UserAccess | None:
        return self._users.get(user_id)


access_control = AccessControl(
    owner_ids=OWNER_IDS,
    legacy_admin_ids=LEGACY_ADMIN_IDS,
    users_file=ACCESS_USERS_FILE,
)
