from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import SqlAlchemyActorRepository
from app.config import settings
from app.db.models import (
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    EpisodePathwayAssignment,
    Organization,
    PathwayDefinition,
    PatientIdentityLink,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from app.domain.enums import CheckInStatus, SubmissionSource, UserRole


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
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for integration test")
    engine = create_engine(settings.database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _identity_fixture(session: Session) -> tuple[Organization, User, SyntheticPatient]:
    organization = Organization(name=f"Organization {uuid4()}")
    user = User(email=f"patient-{uuid4()}@example.test", display_name="Patient account")
    session.add_all([organization, user])
    session.flush()
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"patient-{uuid4()}",
        display_name="Synthetic Patient",
    )
    session.add(patient)
    session.flush()
    return organization, user, patient


def test_patient_identity_link_resolves_separate_patient_actor_in_its_organization(
    db_session: Session,
) -> None:
    """A missing link, or a link in another tenant, must not authorize a patient session."""
    organization, user, patient = _identity_fixture(db_session)
    now = datetime.now(UTC)
    link = PatientIdentityLink(
        organization_id=organization.id,
        user_id=user.id,
        patient_id=patient.id,
        linked_at=now - timedelta(minutes=1),
    )
    role = RoleAssignment(
        organization_id=organization.id,
        user_id=user.id,
        role=UserRole.SUPPORTING_ACTOR,
        granted_at=now - timedelta(minutes=1),
    )
    db_session.add_all([link, role])
    db_session.flush()

    actor = SqlAlchemyActorRepository(db_session).find_active_actor(
        organization_id=organization.id,
        role=UserRole.SUPPORTING_ACTOR,
        at=now,
    )

    assert actor is not None
    assert actor.user_id != actor.patient_id
    assert actor.patient_id == patient.id
    assert actor.organization_id == link.organization_id

    other_organization = Organization(name=f"Other organization {uuid4()}")
    db_session.add(other_organization)
    db_session.flush()
    db_session.add(
        RoleAssignment(
            organization_id=other_organization.id,
            user_id=user.id,
            role=UserRole.SUPPORTING_ACTOR,
            granted_at=now - timedelta(minutes=1),
        )
    )
    db_session.flush()
    assert (
        SqlAlchemyActorRepository(db_session).find_active_actor(
            organization_id=other_organization.id,
            role=UserRole.SUPPORTING_ACTOR,
            at=now,
        )
        is None
    )


def test_revoked_role_or_patient_link_cannot_create_patient_actor(db_session: Session) -> None:
    """A revoked historical authority must not be reused to issue a present patient session."""
    organization, user, patient = _identity_fixture(db_session)
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PatientIdentityLink(
                organization_id=organization.id,
                user_id=user.id,
                patient_id=patient.id,
                linked_at=now - timedelta(hours=2),
                revoked_at=now - timedelta(hours=1),
            ),
            RoleAssignment(
                organization_id=organization.id,
                user_id=user.id,
                role=UserRole.SUPPORTING_ACTOR,
                granted_at=now - timedelta(hours=2),
                revoked_at=now - timedelta(hours=1),
            ),
        ]
    )
    db_session.flush()

    actor = SqlAlchemyActorRepository(db_session).find_active_actor(
        organization_id=organization.id,
        role=UserRole.SUPPORTING_ACTOR,
        at=now,
    )

    assert actor is None


def test_pathway_assignments_reject_overlap_and_allow_adjacent_intervals(
    db_session: Session,
) -> None:
    """Changing the half-open range to allow overlap would admit contradictory pathway history."""
    organization, user, patient = _identity_fixture(db_session)
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug="breast-active",
        version=1,
        name="Breast active treatment",
    )
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=patient.id,
        status="active",
    )
    db_session.add_all([pathway, episode])
    db_session.flush()
    start = datetime(2026, 8, 1, tzinfo=UTC)
    assignment = EpisodePathwayAssignment(
        organization_id=organization.id,
        care_episode_id=episode.id,
        pathway_definition_id=pathway.id,
        effective_from=start,
        effective_to=start + timedelta(days=7),
        migration_reason="initial pathway",
        authored_by_user_id=user.id,
    )
    adjacent = EpisodePathwayAssignment(
        organization_id=organization.id,
        care_episode_id=episode.id,
        pathway_definition_id=pathway.id,
        effective_from=start + timedelta(days=7),
        migration_reason="next pathway period",
        authored_by_user_id=user.id,
    )
    db_session.add_all([assignment, adjacent])
    db_session.flush()

    overlapping = EpisodePathwayAssignment(
        organization_id=organization.id,
        care_episode_id=episode.id,
        pathway_definition_id=pathway.id,
        effective_from=start + timedelta(days=6),
        migration_reason="invalid overlap",
        authored_by_user_id=user.id,
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(overlapping)
            db_session.flush()


def test_submission_provenance_constraints_require_an_author_or_external_source(
    db_session: Session,
) -> None:
    """Removing either provenance check would allow unauthored human or unverifiable import data."""
    organization, user, patient = _identity_fixture(db_session)
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug="breast-active",
        version=1,
        name="Breast active treatment",
    )
    episode = CareEpisode(organization_id=organization.id, patient_id=patient.id, status="active")
    definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition=pathway,
        slug="weekly",
        version=1,
        title="Weekly",
    )
    db_session.add_all([pathway, episode, definition])
    db_session.flush()

    common = {
        "organization_id": organization.id,
        "patient_id": patient.id,
        "care_episode_id": episode.id,
        "check_in_definition_id": definition.id,
        "answers": {},
        "submitted_at": datetime.now(UTC),
    }
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                CheckInSubmission(
                    **common,
                    submission_source=SubmissionSource.PATIENT,
                    status=CheckInStatus.SUBMITTED,
                )
            )
            db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                CheckInSubmission(
                    **common,
                    submission_source=SubmissionSource.IMPORT,
                    status=CheckInStatus.SUBMITTED,
                )
            )
            db_session.flush()

    predecessor = CheckInSubmission(
        **common,
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=user.id,
        status=CheckInStatus.SUBMITTED,
    )
    db_session.add(predecessor)
    db_session.flush()
    correction = CheckInSubmission(
        **common,
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=user.id,
        supersedes_submission_id=predecessor.id,
        status=CheckInStatus.SUBMITTED,
    )
    db_session.add(correction)
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                CheckInSubmission(
                    **common,
                    submission_source=SubmissionSource.PATIENT,
                    submitted_by_user_id=user.id,
                    supersedes_submission_id=predecessor.id,
                    status=CheckInStatus.SUBMITTED,
                )
            )
            db_session.flush()
