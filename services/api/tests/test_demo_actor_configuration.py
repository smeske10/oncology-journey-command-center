from __future__ import annotations

import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from app.auth.demo_actors import (
    DemoActorConfigurationError,
    DemoActorSelection,
    parse_demo_actors,
)
from app.auth.models import Role


def test_non_integer_ttl_does_not_break_config_import() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "DEMO_SESSION_TTL_MINUTES"}
    }
    environment["DATABASE_URL"] = (
        "postgresql+psycopg://runtime:synthetic@target.invalid/ojcc_import"
    )
    environment["DEMO_SESSION_TTL_MINUTES"] = "not-an-integer"

    result = subprocess.run(
        [sys.executable, "-c", "import app.config; print('CONFIG_IMPORTED')"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "CONFIG_IMPORTED" in result.stdout
    assert "Traceback" not in result.stderr
    assert "not-an-integer" not in result.stderr


@pytest.mark.parametrize(
    ("configured_value", "expected"),
    [(None, 30), ("", None), ("   ", None), ("not-an-integer", None)],
)
def test_ttl_environment_parsing_is_import_safe(
    monkeypatch: pytest.MonkeyPatch,
    configured_value: str | None,
    expected: int | None,
) -> None:
    from app.config import Settings

    if configured_value is None:
        monkeypatch.delenv("DEMO_SESSION_TTL_MINUTES", raising=False)
    else:
        monkeypatch.setenv("DEMO_SESSION_TTL_MINUTES", configured_value)

    assert Settings().demo_session_ttl_minutes == expected


@pytest.mark.parametrize(
    ("role", "requires_patient"),
    [
        (Role.ADMINISTRATOR, False),
        (Role.NAVIGATOR, False),
        (Role.SUPPORTING_ACTOR, True),
    ],
)
def test_roster_accepts_each_valid_role(role: Role, requires_patient: bool) -> None:
    user_id = uuid4()
    patient_id = uuid4()
    selection = {"user_id": str(user_id)}
    if requires_patient:
        selection["patient_id"] = str(patient_id)

    roster = parse_demo_actors(json.dumps({role.value: selection}))

    assert roster == {
        role: DemoActorSelection(
            user_id=user_id,
            patient_id=patient_id if requires_patient else None,
        )
    }


def test_roster_accepts_a_subset_of_roles() -> None:
    user_id = uuid4()

    roster = parse_demo_actors(json.dumps({"navigator": {"user_id": str(user_id)}}))

    assert set(roster) == {Role.NAVIGATOR}


def test_roster_rejects_duplicate_role_keys() -> None:
    raw = (
        '{"navigator":{"user_id":"00000000-0000-0000-0000-000000000001"},'
        '"navigator":{"user_id":"00000000-0000-0000-0000-000000000002"}}'
    )

    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


def test_roster_rejects_duplicate_selection_keys() -> None:
    raw = (
        '{"navigator":{"user_id":"00000000-0000-0000-0000-000000000001",'
        '"user_id":"00000000-0000-0000-0000-000000000002"}}'
    )

    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


def test_roster_rejects_unknown_role() -> None:
    raw = json.dumps({"clinician": {"user_id": str(uuid4())}})

    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        '"navigator"',
        "42",
        json.dumps({"navigator": []}),
        json.dumps({"navigator": None}),
        json.dumps({"navigator": "not-an-object"}),
    ],
)
def test_roster_rejects_wrong_json_shape_or_type(raw: str) -> None:
    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


def test_roster_rejects_malformed_json_without_echoing_it() -> None:
    sentinel = "malformed-roster-secret-sentinel"

    with pytest.raises(DemoActorConfigurationError) as exc_info:
        parse_demo_actors(f"{{{sentinel}")

    assert sentinel not in str(exc_info.value)


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"navigator": {"user_id": "not-a-uuid"}}),
        json.dumps(
            {
                "supporting_actor": {
                    "user_id": str(uuid4()),
                    "patient_id": "not-a-uuid",
                }
            }
        ),
        json.dumps({"navigator": {"user_id": 7}}),
    ],
)
def test_roster_rejects_invalid_uuid_fields(raw: str) -> None:
    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


def test_roster_rejects_unexpected_selection_fields() -> None:
    raw = json.dumps(
        {"navigator": {"user_id": str(uuid4()), "organization_id": str(uuid4())}}
    )

    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_roster_rejects_absent_json(raw: str | None) -> None:
    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


def test_roster_rejects_missing_patient_for_supporting_actor() -> None:
    raw = json.dumps({"supporting_actor": {"user_id": str(uuid4())}})

    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


@pytest.mark.parametrize("role", [Role.ADMINISTRATOR, Role.NAVIGATOR])
def test_roster_rejects_patient_field_for_staff_roles(role: Role) -> None:
    raw = json.dumps(
        {
            role.value: {
                "user_id": str(uuid4()),
                "patient_id": str(uuid4()),
            }
        }
    )

    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)


def test_roster_allows_one_user_selected_for_two_roles() -> None:
    user_id = uuid4()
    raw = json.dumps(
        {
            "administrator": {"user_id": str(user_id)},
            "navigator": {"user_id": str(user_id)},
        }
    )

    roster = parse_demo_actors(raw)

    assert roster[Role.ADMINISTRATOR].user_id == user_id
    assert roster[Role.NAVIGATOR].user_id == user_id
