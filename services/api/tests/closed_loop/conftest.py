from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.auth.dependencies import current_actor
from app.auth.models import CurrentActor
from app.config import settings
from app.db.models import (
    ApprovalDecision,
    ApprovalPolicy,
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    EpisodePathwayAssignment,
    NavigationTask,
    Organization,
    PathwayDefinition,
    PatientIdentityLink,
    ProposedChange,
    ReportedNeed,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from app.db.session import get_session
from app.domain.enums import (
    ApprovalChangeType,
    ApprovalDecisionValue,
    CheckInStatus,
    NavigationTaskStatus,
    NeedStatus,
    SubmissionSource,
    UserRole,
)
from app.main import app


@dataclass(frozen=True)
class ClosedLoopCase:
    organization_id: UUID
    patient_id: UUID
    care_episode_id: UUID
    navigator_user_id: UUID
    navigator_role_assignment_id: UUID
    proposer_user_id: UUID
    proposer_role_assignment_id: UUID
    patient_user_id: UUID
    patient_role_assignment_id: UUID
    patient_identity_link_id: UUID
    source_submission_id: UUID
    reported_need_id: UUID
    navigation_task_id: UUID
    proposed_change_id: UUID
    approved_at: datetime | None = None


class ClosedLoopClient:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request(
        self,
        method: str,
        path: str,
        *,
        actor: CurrentActor | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        raise_app_exceptions: bool = True,
    ) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=app,
                raise_app_exceptions=raise_app_exceptions,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                cookies=cookies,
            ) as client:
                return await client.request(method, path, json=json, headers=headers)

        app.dependency_overrides[get_session] = lambda: self._session
        if actor is not None:
            app.dependency_overrides[current_actor] = lambda: actor
        try:
            return asyncio.run(send())
        finally:
            app.dependency_overrides.clear()


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
def closed_loop_session() -> Iterator[Session]:
    if not _database_is_reachable(settings.database_url):
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for closed-loop tests")
    engine = create_engine(settings.database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def _seed_closed_loop_case(session: Session, *, approve: bool) -> ClosedLoopCase:
    now = datetime.now(UTC)
    organization = Organization(name=f"Closed-loop organization {uuid4()}")
    navigator = User(
        email=f"closed-loop-navigator-{uuid4()}@example.test",
        display_name="Closed-loop navigator",
    )
    proposer = User(
        email=f"closed-loop-proposer-{uuid4()}@example.test",
        display_name="Closed-loop proposer",
    )
    patient_user = User(
        email=f"closed-loop-patient-{uuid4()}@example.test",
        display_name="Closed-loop patient",
    )
    session.add_all([organization, navigator, proposer, patient_user])
    session.flush()
    for user in (navigator, proposer, patient_user):
        user.primary_organization_id = organization.id

    navigator_role = RoleAssignment(
        organization_id=organization.id,
        user_id=navigator.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=2),
    )
    proposer_role = RoleAssignment(
        organization_id=organization.id,
        user_id=proposer.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=2),
    )
    patient_role = RoleAssignment(
        organization_id=organization.id,
        user_id=patient_user.id,
        role=UserRole.SUPPORTING_ACTOR,
        granted_at=now - timedelta(hours=2),
    )
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"closed-loop-patient-{uuid4()}",
        display_name="Synthetic closed-loop patient",
    )
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug=f"closed-loop-{uuid4()}",
        version=1,
        name="Closed-loop pathway",
    )
    session.add_all([navigator_role, proposer_role, patient_role, patient, pathway])
    session.flush()

    patient_link = PatientIdentityLink(
        organization_id=organization.id,
        user_id=patient_user.id,
        patient_id=patient.id,
        linked_at=now - timedelta(hours=1),
    )
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=patient.id,
        status="active",
        started_at=now - timedelta(days=7),
    )
    definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition_id=pathway.id,
        slug=f"closed-loop-check-in-{uuid4()}",
        version=1,
        title="Closed-loop check-in",
        questionnaire={"questions": [{"id": "transportation", "type": "boolean"}]},
    )
    session.add_all([patient_link, episode, definition])
    session.flush()

    assignment = EpisodePathwayAssignment(
        organization_id=organization.id,
        care_episode_id=episode.id,
        pathway_definition_id=pathway.id,
        effective_from=now - timedelta(days=7),
        migration_reason="Initial synthetic pathway assignment.",
        authored_by_user_id=proposer.id,
    )
    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": True},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=patient_user.id,
        submitted_at=now - timedelta(minutes=30),
    )
    session.add_all([assignment, submission])
    session.flush()

    need = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=submission.id,
        kind="transportation",
        status=NeedStatus.OPEN,
        evidence=[{"field": "transportation", "text": "yes"}],
    )
    session.add(need)
    session.flush()
    task = NavigationTask(
        organization_id=organization.id,
        patient_id=patient.id,
        reported_need_id=need.id,
        title="Unapproved placeholder title",
        status=NavigationTaskStatus.OPEN,
    )
    policy = ApprovalPolicy(
        organization_id=organization.id,
        change_type=ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK,
        version=1,
        effective_from=now - timedelta(days=1),
        deterministic_severity_threshold=None,
        allow_self_approval=False,
        required_approval_count=1,
        required_approver_role=UserRole.NAVIGATOR,
    )
    session.add_all([task, policy])
    session.flush()
    proposal = ProposedChange(
        organization_id=organization.id,
        proposed_by_user_id=proposer.id,
        proposed_at=now - timedelta(minutes=10),
        change_type=ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK,
        proposed_value={"title": "Arrange synthetic transportation support"},
        rationale="Synthetic transportation support is requested.",
        value_schema_id="ojcc.authorize-navigation-task",
        value_schema_version=1,
        navigation_task_id=task.id,
        approval_policy_id=policy.id,
        approval_policy_version=policy.version,
        deterministic_severity_threshold_snapshot=None,
        allow_self_approval_snapshot=policy.allow_self_approval,
        required_approval_count_snapshot=policy.required_approval_count,
        required_approver_role_snapshot=policy.required_approver_role,
    )
    session.add(proposal)
    session.flush()

    approved_at = None
    if approve:
        approved_at = now - timedelta(minutes=5)
        session.add(
            ApprovalDecision(
                organization_id=organization.id,
                proposed_change_id=proposal.id,
                authorized_by_user_id=navigator.id,
                qualifying_role_assignment_id=navigator_role.id,
                qualifying_role_snapshot=UserRole.NAVIGATOR,
                decision=ApprovalDecisionValue.APPROVED,
                authorized_at=approved_at,
            )
        )
        session.flush()

    return ClosedLoopCase(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        navigator_user_id=navigator.id,
        navigator_role_assignment_id=navigator_role.id,
        proposer_user_id=proposer.id,
        proposer_role_assignment_id=proposer_role.id,
        patient_user_id=patient_user.id,
        patient_role_assignment_id=patient_role.id,
        patient_identity_link_id=patient_link.id,
        source_submission_id=submission.id,
        reported_need_id=need.id,
        navigation_task_id=task.id,
        proposed_change_id=proposal.id,
        approved_at=approved_at,
    )


@pytest.fixture
def closed_loop_case(closed_loop_session: Session) -> ClosedLoopCase:
    return _seed_closed_loop_case(closed_loop_session, approve=False)


@pytest.fixture
def approved_closed_loop_case(closed_loop_session: Session) -> ClosedLoopCase:
    return _seed_closed_loop_case(closed_loop_session, approve=True)


@pytest.fixture
def closed_loop_client(closed_loop_session: Session) -> ClosedLoopClient:
    return ClosedLoopClient(closed_loop_session)
