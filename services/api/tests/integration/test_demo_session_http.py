from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import traceback
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import demo_sessions
from app.auth import dependencies
from app.auth.demo_actors import DemoActorSelection
from app.auth.dependencies import SESSION_COOKIE_NAME
from app.auth.models import CurrentActor, ResolvedAuthority, Role
from app.auth.service import DemoSessionService
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
DATABASE_FAILURE_SENTINELS = (
    "secret-sentinel",
    "postgresql://sentinel.invalid/private",
    "SELECT private_sentinel FROM hidden_table",
    "parameter-sentinel",
    "token-sentinel",
)
DATABASE_FAILURE_SENTINEL = " | ".join(DATABASE_FAILURE_SENTINELS)


class _FailingSession:
    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise SQLAlchemyError(DATABASE_FAILURE_SENTINEL)


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


def _resign_token(
    token: str,
    claim_update: Mapping[str, object],
    *,
    remove_claims: tuple[str, ...] = (),
) -> str:
    encoded_header, encoded_payload, _ = token.split(".")
    payload = _decode_token_payload(token)
    payload.update(claim_update)
    for claim in remove_claims:
        payload.pop(claim, None)
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
    "demo_actors_json",
    [pytest.param(None, id="missing"), pytest.param('{"navigator":', id="malformed")],
)
def test_http_issuance_sanitizes_missing_or_malformed_roster(
    monkeypatch: pytest.MonkeyPatch,
    demo_actors_json: str | None,
) -> None:
    configured_settings = Settings(
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=uuid4(),
        demo_actors_json=demo_actors_json,
    )
    monkeypatch.setattr(demo_sessions, "settings", configured_settings)

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/navigator")

    response = asyncio.run(issue_session())

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}
    assert "set-cookie" not in response.headers


def test_http_issuance_treats_missing_role_entry_as_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_settings = Settings(
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=uuid4(),
        demo_actors_json=json.dumps(
            {Role.ADMINISTRATOR.value: {"user_id": str(uuid4())}}
        ),
    )

    def database_must_not_be_used() -> Iterator[object]:
        yield _FailingSession()

    monkeypatch.setattr(demo_sessions, "settings", configured_settings)
    app.dependency_overrides[get_session] = database_must_not_be_used

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}
    assert "set-cookie" not in response.headers


def test_http_issuance_sanitizes_ambiguous_selected_actor(
    demo_session_database: DisposableDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    owner_engine = create_engine(demo_session_database.migration_url)
    try:
        with Session(owner_engine) as owner:
            owner.add(Organization(id=organization_id, name=f"HTTP ambiguous {uuid4()}"))
            owner.flush()
            owner.add(
                User(
                    id=user_id,
                    email=f"http-{uuid4()}@example.test",
                    display_name="HTTP ambiguous actor",
                    primary_organization_id=None,
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
                        revoked_at=datetime.now(UTC) + timedelta(hours=1),
                    ),
                    RoleAssignment(
                        id=uuid4(),
                        organization_id=organization_id,
                        user_id=user_id,
                        role=Role.NAVIGATOR,
                        granted_at=granted_at,
                    ),
                ]
            )
            owner.commit()
    finally:
        owner_engine.dispose()

    runtime_engine = _configure_runtime_dependencies(
        demo_session_database,
        monkeypatch,
        organization_id=organization_id,
        roster={Role.NAVIGATOR.value: {"user_id": str(user_id)}},
    )

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()
        runtime_engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo actor is unavailable"}
    assert "set-cookie" not in response.headers


def test_http_issuance_sanitizes_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    configured_settings = Settings(
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=organization_id,
        demo_actors_json=json.dumps(
            {Role.NAVIGATOR.value: {"user_id": str(user_id)}}
        ),
    )

    def failing_session() -> Iterator[object]:
        yield _FailingSession()

    monkeypatch.setattr(demo_sessions, "settings", configured_settings)
    app.dependency_overrides[get_session] = failing_session

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo authentication is unavailable"}
    assert "set-cookie" not in response.headers


def test_http_reauthorization_sanitizes_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = CurrentActor(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=Role.NAVIGATOR,
    )
    authority = ResolvedAuthority(actor=actor, role_assignment_id=uuid4())
    roster = {Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)}
    token = DemoSessionService(
        actor_repository=None,
        secret=SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors=roster,
    ).create_token(authority)
    configured_settings = Settings(
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=actor.organization_id,
        demo_actors_json=json.dumps(
            {Role.NAVIGATOR.value: {"user_id": str(actor.user_id)}}
        ),
    )

    def failing_session() -> Iterator[object]:
        yield _FailingSession()

    monkeypatch.setattr(dependencies, "settings", configured_settings)
    app.dependency_overrides[get_session] = failing_session

    async def load_queue() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies={SESSION_COOKIE_NAME: token},
        ) as client:
            return await client.get("/v1/navigator/queue")

    try:
        response = asyncio.run(load_queue())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo authentication is unavailable"}


def test_http_reauthorization_rejects_missing_organization_as_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = CurrentActor(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=Role.NAVIGATOR,
    )
    authority = ResolvedAuthority(actor=actor, role_assignment_id=uuid4())
    token = DemoSessionService(
        actor_repository=None,
        secret=SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors={Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)},
    ).create_token(authority)
    configured_settings = Settings(
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=None,
        demo_actors_json=json.dumps(
            {Role.NAVIGATOR.value: {"user_id": str(actor.user_id)}}
        ),
    )

    def database_must_not_be_used() -> Iterator[object]:
        yield _FailingSession()

    monkeypatch.setattr(dependencies, "settings", configured_settings)
    app.dependency_overrides[get_session] = database_must_not_be_used

    async def load_queue() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies={SESSION_COOKIE_NAME: token},
        ) as client:
            return await client.get("/v1/navigator/queue")

    try:
        response = asyncio.run(load_queue())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo sessions are not configured"}


def test_database_failure_sentinels_do_not_escape_http_logs_or_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    actor = CurrentActor(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=Role.NAVIGATOR,
    )
    roster = {Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)}
    service = DemoSessionService(
        actor_repository=demo_sessions.SqlAlchemyActorRepository(_FailingSession()),  # type: ignore[arg-type]
        secret=SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors=roster,
    )
    app.dependency_overrides[demo_sessions.get_demo_session_service] = lambda: service

    async def issue_session() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/v1/demo/session/navigator")

    try:
        response = asyncio.run(issue_session())
    finally:
        app.dependency_overrides.clear()

    with pytest.raises(HTTPException) as exc_info:
        demo_sessions.create_demo_session(Role.NAVIGATOR, Response(), service)
    rendered = "".join(traceback.format_exception(exc_info.value))
    inspected = f"{response.text}\n{caplog.text}\n{rendered}"

    assert response.status_code == 503
    assert response.json() == {"detail": "Demo authentication is unavailable"}
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    for sentinel in DATABASE_FAILURE_SENTINELS:
        assert sentinel not in inspected


@pytest.mark.parametrize(
    ("claim_update", "remove_claims"),
    [
        pytest.param({}, ("ver",), id="missing-version"),
        pytest.param({"ver": 1}, (), id="legacy-version"),
        pytest.param({"ver": "2"}, (), id="wrong-type-version"),
        pytest.param({}, ("ra",), id="missing-role-assignment"),
        pytest.param({"ra": "not-a-uuid"}, (), id="invalid-role-assignment"),
        pytest.param({"pil": str(uuid4())}, (), id="staff-link-claim"),
    ],
)
def test_http_reauthorization_sanitizes_incompatible_token_claims_before_database(
    monkeypatch: pytest.MonkeyPatch,
    claim_update: dict[str, object],
    remove_claims: tuple[str, ...],
) -> None:
    actor = CurrentActor(
        user_id=uuid4(),
        organization_id=uuid4(),
        role=Role.NAVIGATOR,
    )
    authority = ResolvedAuthority(actor=actor, role_assignment_id=uuid4())
    token = DemoSessionService(
        actor_repository=None,
        secret=SESSION_SECRET,
        ttl_minutes=30,
        organization_id=actor.organization_id,
        demo_actors={Role.NAVIGATOR: DemoActorSelection(user_id=actor.user_id)},
    ).create_token(authority)
    incompatible_token = _resign_token(
        token,
        claim_update,
        remove_claims=remove_claims,
    )
    configured_settings = Settings(
        environment="local",
        demo_session_secret=SESSION_SECRET,
        demo_organization_id=actor.organization_id,
        demo_actors_json=json.dumps(
            {Role.NAVIGATOR.value: {"user_id": str(actor.user_id)}}
        ),
    )

    def database_must_not_be_used() -> Iterator[object]:
        yield _FailingSession()

    monkeypatch.setattr(dependencies, "settings", configured_settings)
    app.dependency_overrides[get_session] = database_must_not_be_used

    async def load_queue() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies={SESSION_COOKIE_NAME: incompatible_token},
        ) as client:
            return await client.get("/v1/navigator/queue")

    try:
        response = asyncio.run(load_queue())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired demo session"}


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
