from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias
from uuid import UUID

from app.auth.models import Role


class DemoActorConfigurationError(ValueError):
    """Raised when the explicit demo-actor roster is missing or malformed."""


@dataclass(frozen=True)
class DemoActorSelection:
    user_id: UUID
    patient_id: UUID | None = None


JsonValue: TypeAlias = object


class _JsonObject(list[tuple[str, JsonValue]]):
    pass


def _preserve_object_pairs(pairs: list[tuple[str, JsonValue]]) -> _JsonObject:
    return _JsonObject(pairs)


def parse_demo_actors(value: str | None) -> dict[Role, DemoActorSelection]:
    """Parse an explicit, role-keyed roster without accepting ambiguous JSON."""
    if value is None or not value.strip():
        raise DemoActorConfigurationError("DEMO_ACTORS_JSON is not configured")
    try:
        raw_roster = json.loads(value, object_pairs_hook=_preserve_object_pairs)
        if not isinstance(raw_roster, _JsonObject):
            raise ValueError
        roster: dict[Role, DemoActorSelection] = {}
        seen_roles: set[str] = set()
        for role_name, raw_selection in raw_roster:
            if role_name in seen_roles:
                raise ValueError
            seen_roles.add(role_name)
            role = Role(role_name)
            roster[role] = _parse_selection(role, raw_selection)
        return roster
    except (json.JSONDecodeError, TypeError, ValueError):
        raise DemoActorConfigurationError("DEMO_ACTORS_JSON is invalid") from None


def _parse_selection(role: Role, value: JsonValue) -> DemoActorSelection:
    if not isinstance(value, _JsonObject):
        raise ValueError
    fields: dict[str, JsonValue] = {}
    for name, raw_field in value:
        if name in fields:
            raise ValueError
        fields[name] = raw_field

    expected_fields = {"user_id", "patient_id"} if role == Role.SUPPORTING_ACTOR else {"user_id"}
    if set(fields) != expected_fields:
        raise ValueError

    user_id = _uuid_field(fields["user_id"])
    patient_id = _uuid_field(fields["patient_id"]) if role == Role.SUPPORTING_ACTOR else None
    return DemoActorSelection(user_id=user_id, patient_id=patient_id)


def _uuid_field(value: JsonValue) -> UUID:
    if not isinstance(value, str):
        raise ValueError
    return UUID(value)
