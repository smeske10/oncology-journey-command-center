from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.auth.dependencies import current_actor, get_current_demo_session_service
from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService
from app.config import settings
from app.db.models import (
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    NavigationTask,
    Organization,
    PathwayDefinition,
    ReportedNeed,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from app.db.session import get_session
from app.domain.enums import (
    CheckInStatus,
    NavigationTaskStatus,
    NeedStatus,
    SubmissionSource,
    UserRole,
)
from app.main import app


def _database_is_reachable(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.host:
        return False
    try:
        with socket.create_connection((url.host, url.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def db_session() -> Iterator[Session]:
    if not _database_is_reachable(settings.database_url):
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for outcome route tests")
    engine = create_engine(settings.database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def _seed_need(
    session: Session,
) -> tuple[Organization, User, RoleAssignment, SyntheticPatient, ReportedNeed, NavigationTask]:
    now = datetime.now(UTC)
    organization = Organization(name=f"Outcome route organization {uuid4()}")
    navigator = User(email=f"navigator-{uuid4()}@example.test", display_name="Navigator")
    patient_author = User(email=f"patient-{uuid4()}@example.test", display_name="Patient")
    session.add_all([organization, navigator, patient_author])
    session.flush()
    role = RoleAssignment(
        organization_id=organization.id,
        user_id=navigator.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(minutes=5),
    )
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"outcome-patient-{uuid4()}",
        display_name="Synthetic outcome patient",
    )
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug=f"outcome-{uuid4()}",
        version=1,
        name="Outcome pathway",
    )
    session.add_all([role, patient, pathway])
    session.flush()
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=patient.id,
        status="active",
    )
    definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition_id=pathway.id,
        slug=f"outcome-check-in-{uuid4()}",
        version=1,
        title="Outcome check-in",
    )
    session.add_all([episode, definition])
    session.flush()
    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers={},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=patient_author.id,
        submitted_at=now,
    )
    session.add(submission)
    session.flush()
    need_values = {
        "organization_id": organization.id,
        "patient_id": patient.id,
        "source_submission_id": submission.id,
        "kind": "transportation",
        "status": NeedStatus.OPEN,
        "evidence": [{"field": "transportation", "text": "yes"}],
    }
    if hasattr(ReportedNeed, "care_episode_id"):
        need_values["care_episode_id"] = episode.id
    need = ReportedNeed(**need_values)
    session.add(need)
    session.flush()
    task = NavigationTask(
        organization_id=organization.id,
        patient_id=patient.id,
        reported_need_id=need.id,
        title="Arrange transportation",
        status=NavigationTaskStatus.OPEN,
    )
    session.add(task)
    session.flush()
    return organization, navigator, role, patient, need, task


def _request(
    method: str,
    path: str,
    *,
    json: dict[str, str | None] | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", cookies=cookies
        ) as client:
            return await client.request(method, path, json=json, headers=headers)

    return asyncio.run(send())


def _override_actor_and_session(session: Session, actor: CurrentActor) -> None:
    app.dependency_overrides[current_actor] = lambda: actor
    app.dependency_overrides[get_session] = lambda: session


def test_outcome_preview_lists_only_tasks_that_would_be_cancelled(db_session: Session) -> None:
    organization, navigator, _, _, need, open_task = _seed_need(db_session)
    completed_task = NavigationTask(
        organization_id=organization.id,
        patient_id=need.patient_id,
        reported_need_id=need.id,
        title="Already completed",
        status=NavigationTaskStatus.OPEN,
    )
    db_session.add(completed_task)
    db_session.flush()
    completed_task.status = NavigationTaskStatus.COMPLETED
    completed_task.completed_at = datetime.now(UTC)
    db_session.flush()
    _override_actor_and_session(
        db_session,
        CurrentActor(
            user_id=navigator.id,
            organization_id=organization.id,
            role=Role.NAVIGATOR,
        ),
    )
    try:
        response = _request("GET", f"/v1/navigator/needs/{need.id}/outcome-preview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert response.json() == {
        "need_id": str(need.id),
        "tasks": [
            {
                "id": str(open_task.id),
                "title": "Arrange transportation",
                "status": "open",
            }
        ],
    }


def test_outcome_command_is_idempotent_and_reports_trigger_cancelled_tasks(
    db_session: Session,
) -> None:
    organization, navigator, _, _, need, task = _seed_need(db_session)
    actor = CurrentActor(
        user_id=navigator.id,
        organization_id=organization.id,
        role=Role.NAVIGATOR,
    )
    _override_actor_and_session(db_session, actor)
    path = f"/v1/navigator/needs/{need.id}/outcomes"
    try:
        first = _request(
            "POST",
            path,
            json={"disposition": "resolved", "note": "Ride confirmed."},
            headers={"Idempotency-Key": "resolve-transportation-once"},
        )
        replay = _request(
            "POST",
            path,
            json={"disposition": "resolved", "note": "Ride confirmed."},
            headers={"Idempotency-Key": "resolve-transportation-once"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    assert first.json()["cancelled_task_ids"] == [str(task.id)]
    assert first.json()["recorded_by_user_id"] == str(navigator.id)


def test_outcome_command_rejects_idempotency_key_reuse_for_a_different_payload(
    db_session: Session,
) -> None:
    organization, navigator, _, _, need, _ = _seed_need(db_session)
    _override_actor_and_session(
        db_session,
        CurrentActor(
            user_id=navigator.id,
            organization_id=organization.id,
            role=Role.NAVIGATOR,
        ),
    )
    path = f"/v1/navigator/needs/{need.id}/outcomes"
    try:
        first = _request(
            "POST",
            path,
            json={"disposition": "resolved", "note": "Ride confirmed."},
            headers={"Idempotency-Key": "payload-conflict"},
        )
        conflict = _request(
            "POST",
            path,
            json={"disposition": "closed_unresolved", "note": "Unable to arrange."},
            headers={"Idempotency-Key": "payload-conflict"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409


def test_outcome_command_rejects_idempotency_key_reuse_for_a_different_need(
    db_session: Session,
) -> None:
    organization, navigator, _, _, need, _ = _seed_need(db_session)
    second_need = ReportedNeed(
        organization_id=organization.id,
        patient_id=need.patient_id,
        care_episode_id=need.care_episode_id,
        source_submission_id=need.source_submission_id,
        kind="financial_support",
        status=NeedStatus.OPEN,
        evidence=[],
    )
    db_session.add(second_need)
    db_session.flush()
    _override_actor_and_session(
        db_session,
        CurrentActor(
            user_id=navigator.id,
            organization_id=organization.id,
            role=Role.NAVIGATOR,
        ),
    )
    try:
        first = _request(
            "POST",
            f"/v1/navigator/needs/{need.id}/outcomes",
            json={"disposition": "resolved", "note": "Same payload."},
            headers={"Idempotency-Key": "need-conflict"},
        )
        conflict = _request(
            "POST",
            f"/v1/navigator/needs/{second_need.id}/outcomes",
            json={"disposition": "resolved", "note": "Same payload."},
            headers={"Idempotency-Key": "need-conflict"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409


def test_outcome_routes_reject_non_navigator_and_cross_organization_actors(
    db_session: Session,
) -> None:
    organization, navigator, _, _, need, _ = _seed_need(db_session)
    try:
        _override_actor_and_session(
            db_session,
            CurrentActor(
                user_id=navigator.id,
                organization_id=organization.id,
                role=Role.ADMINISTRATOR,
            ),
        )
        assert (
            _request("GET", f"/v1/navigator/needs/{need.id}/outcome-preview").status_code
            == 403
        )

        app.dependency_overrides[current_actor] = lambda: CurrentActor(
            user_id=navigator.id,
            organization_id=uuid4(),
            role=Role.NAVIGATOR,
        )
        assert (
            _request("GET", f"/v1/navigator/needs/{need.id}/outcome-preview").status_code
            == 404
        )
        assert (
            _request(
                "POST",
                f"/v1/navigator/needs/{need.id}/outcomes",
                json={"disposition": "resolved", "note": None},
                headers={"Idempotency-Key": "cross-org"},
            ).status_code
            == 404
        )
    finally:
        app.dependency_overrides.clear()


def test_revoked_navigator_cannot_record_an_outcome(db_session: Session) -> None:
    organization, navigator, role, _, need, _ = _seed_need(db_session)
    actor = CurrentActor(
        user_id=navigator.id,
        organization_id=organization.id,
        role=Role.NAVIGATOR,
    )
    service = DemoSessionService(
        actor_repository=None,
        secret="outcome-route-test-secret",
        ttl_minutes=30,
        organization_id=None,
    )
    token = service.create_token(actor)
    role.revoked_at = datetime.now(UTC)
    db_session.flush()
    app.dependency_overrides[get_current_demo_session_service] = lambda: service
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        response = _request(
            "POST",
            f"/v1/navigator/needs/{need.id}/outcomes",
            json={"disposition": "resolved", "note": None},
            headers={"Idempotency-Key": "revoked-navigator"},
            cookies={"ojcc_session": token},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "Demo session is no longer authorized"}


def test_outcome_command_requires_an_idempotency_key(db_session: Session) -> None:
    organization, navigator, _, _, need, _ = _seed_need(db_session)
    _override_actor_and_session(
        db_session,
        CurrentActor(
            user_id=navigator.id,
            organization_id=organization.id,
            role=Role.NAVIGATOR,
        ),
    )
    try:
        response = _request(
            "POST",
            f"/v1/navigator/needs/{need.id}/outcomes",
            json={"disposition": "resolved", "note": None},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
