from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import (
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    Organization,
    PathwayDefinition,
    ReportedNeed,
    SyntheticPatient,
    User,
)
from app.domain.enums import SubmissionSource


def _database_is_reachable(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.host:
        return False
    try:
        with socket.create_connection((url.host, url.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _database_is_reachable(settings.database_url):
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for core-domain integration test")
    return settings.database_url


@pytest.fixture
def db_session(database_url: str) -> Iterator[Session]:
    engine = create_engine(database_url)
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


@pytest.fixture
def organization(db_session: Session) -> Organization:
    value = Organization(name="Synthetic Oncology Center")
    db_session.add(value)
    db_session.flush()
    return value


@pytest.fixture
def check_in_definition(db_session: Session, organization: Organization) -> CheckInDefinition:
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug="active-breast-cancer",
        version=1,
        name="Active breast cancer treatment",
    )
    check_in_definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition=pathway,
        slug="weekly-check-in",
        version=1,
        title="Weekly check-in",
    )
    db_session.add_all([pathway, check_in_definition])
    db_session.flush()
    return check_in_definition


@pytest.fixture
def synthetic_patient(db_session: Session, organization: Organization) -> SyntheticPatient:
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref="synthetic-patient-001",
        display_name="Synthetic Patient",
    )
    db_session.add(patient)
    db_session.flush()
    return patient


def test_need_lifecycle_is_independent_of_submission(
    db_session: Session,
    organization: Organization,
    synthetic_patient: SyntheticPatient,
    check_in_definition: CheckInDefinition,
) -> None:
    user = User(email="patient-author@example.test", display_name="Patient author")
    db_session.add(user)
    db_session.flush()
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=synthetic_patient.id,
        status="active",
    )
    db_session.add(episode)
    db_session.flush()
    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=synthetic_patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=check_in_definition.id,
        status="submitted",
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=user.id,
        submitted_at=datetime.now(UTC),
    )
    need = ReportedNeed(
        organization_id=organization.id,
        patient_id=synthetic_patient.id,
        care_episode_id=episode.id,
        source_submission=submission,
        kind="symptom_change",
        status="open",
    )
    db_session.add_all([submission, need])
    db_session.commit()
    assert need.status == "open"
    submission.status = "processed"
    with pytest.raises(DBAPIError, match="check_in_submission is append-only"):
        db_session.commit()
    db_session.rollback()
