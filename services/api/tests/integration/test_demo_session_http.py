from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import demo_sessions
from app.auth import dependencies
from app.auth.dependencies import SESSION_COOKIE_NAME
from app.auth.models import Role
from app.config import Settings
from app.db.models import (
    Organization,
    PatientIdentityLink,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from app.db.session import get_session
from app.main import app
from tests.database_support import DisposableDatabase, disposable_database

SESSION_SECRET = "demo-session-http-signing-secret"


@pytest.fixture(scope="module")
def demo_session_database() -> Iterator[DisposableDatabase]:
    with disposable_database(prefix="ojcc_task7_", migrate_to="head") as database:
        yield database


def _decode_token_payload(token: str) -> dict[str, object]:
    encoded_payload = token.split(".")[1]
    decoded = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
    payload = json.loads(decoded)
    assert isinstance(payload, dict)
    return payload


def _resign_token(token: str, claim_update: dict[str, object]) -> str:
    encoded_header, encoded_payload, _ = token.split(".")
    payload = _decode_token_payload(token)
    payload.update(claim_update)
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    signature = hmac.new(SESSION_SECRET.encode(), signing_input, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def _configure_runtime_dependencies(
    database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
    *,
    organization_id: UUID,
    roster: dict[str, dict[str, str]],
):
    configured_settings = Settings(
        database_url=database.application_url,
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=organization_id,
        demo_actors_json=json.dumps(roster),
    )
    runtime_engine = create_engine(database.application_url)

    def runtime_session() -> Iterator[Session]:
        with Session(runtime_engine) as session:
            yield session

    monkeypatch.setattr(demo_sessions, "settings", configured_settings)
    monkeypatch.setattr(dependencies, "settings", configured_settings)
    app.dependency_overrides[get_session] = runtime_session
    return runtime_engine


@pytest.mark.parametrize(
    ("configured_user_id", "other_user_id", "insertion_order"),
    [
        (
            UUID("00000000-0000-0000-0000-000000000900"),
            UUID("00000000-0000-0000-0000-000000000100"),
            ("configured", "other"),
        ),
        (
            UUID("00000000-0000-0000-0000-000000000101"),
            UUID("00000000-0000-0000-0000-000000000901"),
            ("other", "configured"),
        ),
    ],
    ids=["configured-higher-and-first", "configured-lower-and-last"],
)
def test_http_issuance_selects_explicit_navigator_from_ambiguous_role_pool(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
    configured_user_id: UUID,
    other_user_id: UUID,
    insertion_order: tuple[str, str],
) -> None:
    organization_id = uuid4()
    user_ids = {"configured": configured_user_id, "other": other_user_id}
    granted_at = datetime.now(UTC) - timedelta(hours=1)
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP issuance {uuid4()}"))
            owner.flush()
            for label in insertion_order:
                user_id = user_ids[label]
                owner.add(
                    User(
                        id=user_id,
                        email=f"http-{uuid4()}@example.test",
                        display_name=f"HTTP {label}",
                        primary_organization_id=None,
                    )
                )
                owner.flush()
                owner.add(
                    RoleAssignment(
                        id=uuid4(),
                        organization_id=organization_id,
                        user_id=user_id,
                        role=Role.NAVIGATOR,
                        granted_at=granted_at,
                    )
                )
            owner.commit()
    finally:
        owner_engine.dispose()

    runtime_engine = create_engine(demo_session_database.application_url)

    def runtime_session() -> Iterator[Session]:
        with Session(runtime_engine) as session:
            yield session

    configured_settings = Settings(
        database_url=demo_session_database.application_url,
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=organization_id,
        demo_actors_json=json.dumps(
            {Role.NAVIGATOR.value: {"user_id": str(configured_user_id)}}
        ),
    )
    monkeypatch.setattr(demo_sessions, "settings", configured_settings)
    app.dependency_overrides[get_session] = runtime_session

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()
        runtime_engine.dispose()

    assert response.status_code == 204
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    assert _decode_token_payload(token)["sub"] == str(configured_user_id)


@pytest.mark.parametrize("role", [Role.ADMINISTRATOR, Role.SUPPORTING_ACTOR])
def test_http_issuance_selects_explicit_role_specific_actor(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
) -> None:
    organization_id = uuid4()
    configured_user_id = uuid4()
    other_user_id = uuid4()
    configured_patient_id = uuid4() if role == Role.SUPPORTING_ACTOR else None
    other_patient_id = uuid4() if role == Role.SUPPORTING_ACTOR else None
    granted_at = datetime.now(UTC) - timedelta(hours=1)
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP role {uuid4()}"))
            owner.flush()
            owner.add_all(
                [
                    User(
                        id=user_id,
                        email=f"http-{uuid4()}@example.test",
                        display_name="HTTP role actor",
                        primary_organization_id=None,
                    )
                    for user_id in (configured_user_id, other_user_id)
                ]
            )
            if configured_patient_id is not None and other_patient_id is not None:
                owner.add_all(
                    [
                        SyntheticPatient(
                            id=patient_id,
                            organization_id=organization_id,
                            external_ref=f"http-patient-{uuid4()}",
                            display_name="HTTP patient",
                        )
                        for patient_id in (configured_patient_id, other_patient_id)
                    ]
                )
            owner.flush()
            owner.add_all(
                [
                    RoleAssignment(
                        id=uuid4(),
                        organization_id=organization_id,
                        user_id=user_id,
                        role=role,
                        granted_at=granted_at,
                    )
                    for user_id in (configured_user_id, other_user_id)
                ]
            )
            if configured_patient_id is not None and other_patient_id is not None:
                owner.add_all(
                    [
                        PatientIdentityLink(
                            id=uuid4(),
                            organization_id=organization_id,
                            user_id=user_id,
                            patient_id=patient_id,
                            linked_at=granted_at,
                        )
                        for user_id, patient_id in (
                            (configured_user_id, configured_patient_id),
                            (other_user_id, other_patient_id),
                        )
                    ]
                )
            owner.commit()
    finally:
        owner_engine.dispose()

    selection: dict[str, str] = {"user_id": str(configured_user_id)}
    if configured_patient_id is not None:
        selection["patient_id"] = str(configured_patient_id)
    configured_settings = Settings(
        database_url=demo_session_database.application_url,
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=organization_id,
        demo_actors_json=json.dumps({role.value: selection}),
    )
    runtime_engine = create_engine(demo_session_database.application_url)

    def runtime_session() -> Iterator[Session]:
        with Session(runtime_engine) as session:
            yield session

    monkeypatch.setattr(demo_sessions, "settings", configured_settings)
    app.dependency_overrides[get_session] = runtime_session

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(f"/v1/demo/session/{role.value}")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()
        runtime_engine.dispose()

    assert response.status_code == 204
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    payload = _decode_token_payload(token)
    assert payload["sub"] == str(configured_user_id)
    if configured_patient_id is None:
        assert "patient" not in payload
    else:
        assert payload["patient"] == str(configured_patient_id)


@pytest.mark.parametrize(
    "authority_change",
    ["revoked-role", "inactive-user", "ambiguous-role"],
)
def test_cookie_session_refuses_revoked_or_inactive_authority(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
    authority_change: str,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    role_assignment_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP revocation {uuid4()}"))
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"http-{uuid4()}@example.test",
                    display_name="HTTP revoked actor",
                    primary_organization_id=None,
                )
            )
            owner.flush()
            owner.add(
                RoleAssignment(
                    id=role_assignment_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.NAVIGATOR,
                    granted_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            owner.commit()

        runtime_engine = _configure_runtime_dependencies(
            demo_session_database,
            monkeypatch,
            organization_id=organization_id,
            roster={Role.NAVIGATOR.value: {"user_id": str(user_id)}},
        )

        async def exercise_revocation() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (await client.post("/v1/demo/session/navigator")).status_code == 204
                assert (await client.get("/v1/navigator/queue")).status_code == 200
                with Session(owner_engine) as owner:
                    if authority_change == "revoked-role":
                        assignment = owner.get(RoleAssignment, role_assignment_id)
                        assert assignment is not None
                        assignment.revoked_at = datetime.now(UTC)
                    elif authority_change == "inactive-user":
                        user = owner.get(User, user_id)
                        assert user is not None
                        user.is_active = False
                    else:
                        owner.add(
                            RoleAssignment(
                                id=uuid4(),
                                organization_id=organization_id,
                                user_id=user_id,
                                role=Role.NAVIGATOR,
                                granted_at=datetime.now(UTC) - timedelta(minutes=1),
                                revoked_at=datetime.now(UTC) + timedelta(hours=1),
                            )
                        )
                    owner.commit()
                response = await client.get("/v1/navigator/queue")
                assert response.status_code == 401
                assert response.json() == {
                    "detail": "Demo session is no longer authorized"
                }

        try:
            asyncio.run(exercise_revocation())
        finally:
            app.dependency_overrides.clear()
            runtime_engine.dispose()
    finally:
        owner_engine.dispose()


def test_old_cookie_stays_invalid_after_adjacent_role_replacement(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    original_role_assignment_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP replacement {uuid4()}"))
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"http-{uuid4()}@example.test",
                    display_name="HTTP replacement actor",
                    primary_organization_id=None,
                )
            )
            owner.flush()
            owner.add(
                RoleAssignment(
                    id=original_role_assignment_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.NAVIGATOR,
                    granted_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            owner.commit()

        runtime_engine = _configure_runtime_dependencies(
            demo_session_database,
            monkeypatch,
            organization_id=organization_id,
            roster={Role.NAVIGATOR.value: {"user_id": str(user_id)}},
        )

        async def exercise_replacement() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (await client.post("/v1/demo/session/navigator")).status_code == 204
                assert (await client.get("/v1/navigator/queue")).status_code == 200
                replaced_at = datetime.now(UTC)
                with Session(owner_engine) as owner:
                    assignment = owner.get(RoleAssignment, original_role_assignment_id)
                    assert assignment is not None
                    assignment.revoked_at = replaced_at
                    owner.add(
                        RoleAssignment(
                            id=uuid4(),
                            organization_id=organization_id,
                            user_id=user_id,
                            role=Role.NAVIGATOR,
                            granted_at=replaced_at,
                        )
                    )
                    owner.commit()
                response = await client.get("/v1/navigator/queue")
                assert response.status_code == 401
                assert response.json() == {
                    "detail": "Demo session is no longer authorized"
                }
                assert (await client.post("/v1/demo/session/navigator")).status_code == 204
                assert (await client.get("/v1/navigator/queue")).status_code == 200

        try:
            asyncio.run(exercise_replacement())
        finally:
            app.dependency_overrides.clear()
            runtime_engine.dispose()
    finally:
        owner_engine.dispose()


def test_old_cookie_stays_invalid_after_adjacent_patient_link_replacement(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    patient_id = uuid4()
    original_link_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP link replacement {uuid4()}"))
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"http-{uuid4()}@example.test",
                    display_name="HTTP link replacement actor",
                    primary_organization_id=None,
                )
            )
            owner.add(
                SyntheticPatient(
                    id=patient_id,
                    organization_id=organization_id,
                    external_ref=f"http-patient-{uuid4()}",
                    display_name="HTTP replacement patient",
                )
            )
            owner.flush()
            granted_at = datetime.now(UTC) - timedelta(hours=1)
            owner.add(
                RoleAssignment(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.SUPPORTING_ACTOR,
                    granted_at=granted_at,
                )
            )
            owner.add(
                PatientIdentityLink(
                    id=original_link_id,
                    organization_id=organization_id,
                    user_id=user_id,
                    patient_id=patient_id,
                    linked_at=granted_at,
                )
            )
            owner.commit()

        runtime_engine = _configure_runtime_dependencies(
            demo_session_database,
            monkeypatch,
            organization_id=organization_id,
            roster={
                Role.SUPPORTING_ACTOR.value: {
                    "user_id": str(user_id),
                    "patient_id": str(patient_id),
                }
            },
        )

        async def exercise_replacement() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (
                    await client.post("/v1/demo/session/supporting_actor")
                ).status_code == 204
                assert (await client.get("/v1/patient/follow-ups")).status_code == 200
                replaced_at = datetime.now(UTC)
                with Session(owner_engine) as owner:
                    link = owner.get(PatientIdentityLink, original_link_id)
                    assert link is not None
                    link.revoked_at = replaced_at
                    owner.add(
                        PatientIdentityLink(
                            id=uuid4(),
                            organization_id=organization_id,
                            user_id=user_id,
                            patient_id=patient_id,
                            linked_at=replaced_at,
                        )
                    )
                    owner.commit()
                response = await client.get("/v1/patient/follow-ups")
                assert response.status_code == 401
                assert response.json() == {
                    "detail": "Demo session is no longer authorized"
                }
                assert (
                    await client.post("/v1/demo/session/supporting_actor")
                ).status_code == 204
                assert (await client.get("/v1/patient/follow-ups")).status_code == 200

        try:
            asyncio.run(exercise_replacement())
        finally:
            app.dependency_overrides.clear()
            runtime_engine.dispose()
    finally:
        owner_engine.dispose()


def test_roster_change_invalidates_existing_cookie(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    original_user_id = uuid4()
    replacement_user_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP roster change {uuid4()}"))
            owner.flush()
            for user_id in (original_user_id, replacement_user_id):
                owner.add(
                    User(
                        id=user_id,
                        email=f"http-{uuid4()}@example.test",
                        display_name="HTTP roster actor",
                        primary_organization_id=None,
                    )
                )
                owner.flush()
                owner.add(
                    RoleAssignment(
                        id=uuid4(),
                        organization_id=organization_id,
                        user_id=user_id,
                        role=Role.NAVIGATOR,
                        granted_at=datetime.now(UTC) - timedelta(hours=1),
                    )
                )
            owner.commit()

        runtime_engine = _configure_runtime_dependencies(
            demo_session_database,
            monkeypatch,
            organization_id=organization_id,
            roster={Role.NAVIGATOR.value: {"user_id": str(original_user_id)}},
        )

        async def exercise_roster_change() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (await client.post("/v1/demo/session/navigator")).status_code == 204
                assert (await client.get("/v1/navigator/queue")).status_code == 200
                monkeypatch.setattr(
                    dependencies,
                    "settings",
                    Settings(
                        database_url=demo_session_database.application_url,
                        environment="local",
                        demo_session_secret=SESSION_SECRET,
                        demo_organization_id=organization_id,
                        demo_actors_json=json.dumps(
                            {
                                Role.NAVIGATOR.value: {
                                    "user_id": str(replacement_user_id)
                                }
                            }
                        ),
                    ),
                )
                response = await client.get("/v1/navigator/queue")
                assert response.status_code == 401
                assert response.json() == {
                    "detail": "Demo session is no longer authorized"
                }

        try:
            asyncio.run(exercise_roster_change())
        finally:
            app.dependency_overrides.clear()
            runtime_engine.dispose()
    finally:
        owner_engine.dispose()


def test_staff_claim_drift_is_rejected_while_other_organization_membership_is_valid(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    other_organization_id = uuid4()
    user_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add_all(
                [
                    Organization(id=organization_id, name=f"HTTP claim org {uuid4()}"),
                    Organization(
                        id=other_organization_id,
                        name=f"HTTP other membership {uuid4()}",
                    ),
                ]
            )
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"http-{uuid4()}@example.test",
                    display_name="HTTP claim actor",
                    primary_organization_id=other_organization_id,
                )
            )
            owner.flush()
            granted_at = datetime.now(UTC) - timedelta(hours=1)
            owner.add_all(
                [
                    RoleAssignment(
                        id=uuid4(),
                        organization_id=organization_id,
                        user_id=user_id,
                        role=Role.NAVIGATOR,
                        granted_at=granted_at,
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        organization_id=other_organization_id,
                        user_id=user_id,
                        role=Role.NAVIGATOR,
                        granted_at=granted_at,
                    ),
                ]
            )
            owner.commit()

        runtime_engine = _configure_runtime_dependencies(
            demo_session_database,
            monkeypatch,
            organization_id=organization_id,
            roster={Role.NAVIGATOR.value: {"user_id": str(user_id)}},
        )

        async def exercise_claim_drift() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (await client.post("/v1/demo/session/navigator")).status_code == 204
                assert (await client.get("/v1/navigator/queue")).status_code == 200
                token = client.cookies.get(SESSION_COOKIE_NAME)
                assert token is not None

            for claim_update in (
                {"org": str(other_organization_id)},
                {"sub": str(uuid4())},
                {"role": Role.ADMINISTRATOR.value},
                {"ra": str(uuid4())},
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    cookies={SESSION_COOKIE_NAME: _resign_token(token, claim_update)},
                ) as drifted_client:
                    response = await drifted_client.get("/v1/navigator/queue")
                    assert response.status_code == 401
                    assert response.json() == {
                        "detail": "Demo session is no longer authorized"
                    }

        try:
            asyncio.run(exercise_claim_drift())
        finally:
            app.dependency_overrides.clear()
            runtime_engine.dispose()
    finally:
        owner_engine.dispose()


def test_patient_and_link_claim_drift_is_rejected(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    patient_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP patient drift {uuid4()}"))
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"http-{uuid4()}@example.test",
                    display_name="HTTP patient drift actor",
                    primary_organization_id=None,
                )
            )
            owner.add(
                SyntheticPatient(
                    id=patient_id,
                    organization_id=organization_id,
                    external_ref=f"http-patient-{uuid4()}",
                    display_name="HTTP drift patient",
                )
            )
            owner.flush()
            granted_at = datetime.now(UTC) - timedelta(hours=1)
            owner.add(
                RoleAssignment(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    role=Role.SUPPORTING_ACTOR,
                    granted_at=granted_at,
                )
            )
            owner.add(
                PatientIdentityLink(
                    id=uuid4(),
                    organization_id=organization_id,
                    user_id=user_id,
                    patient_id=patient_id,
                    linked_at=granted_at,
                )
            )
            owner.commit()

        runtime_engine = _configure_runtime_dependencies(
            demo_session_database,
            monkeypatch,
            organization_id=organization_id,
            roster={
                Role.SUPPORTING_ACTOR.value: {
                    "user_id": str(user_id),
                    "patient_id": str(patient_id),
                }
            },
        )

        async def exercise_claim_drift() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (
                    await client.post("/v1/demo/session/supporting_actor")
                ).status_code == 204
                token = client.cookies.get(SESSION_COOKIE_NAME)
                assert token is not None

            for claim_update in (
                {"patient": str(uuid4())},
                {"pil": str(uuid4())},
            ):
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                    cookies={SESSION_COOKIE_NAME: _resign_token(token, claim_update)},
                ) as drifted_client:
                    response = await drifted_client.get("/v1/patient/follow-ups")
                    assert response.status_code == 401
                    assert response.json() == {
                        "detail": "Demo session is no longer authorized"
                    }

        try:
            asyncio.run(exercise_claim_drift())
        finally:
            app.dependency_overrides.clear()
            runtime_engine.dispose()
    finally:
        owner_engine.dispose()
