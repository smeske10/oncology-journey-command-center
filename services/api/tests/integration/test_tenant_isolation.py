from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import Organization, SyntheticPatient
from app.db.repositories import SqlAlchemyPatientRepository


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
        pytest.skip(
            "PostgreSQL DATABASE_URL is not reachable for tenant-isolation integration test"
        )
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


def test_actor_cannot_read_another_tenant(db_session: Session) -> None:
    """This fails if patient reads stop filtering by the actor's organization."""
    actor_organization = Organization(name="Actor organization")
    other_organization = Organization(name="Other organization")
    db_session.add_all([actor_organization, other_organization])
    db_session.flush()
    other_tenant_patient = SyntheticPatient(
        organization_id=other_organization.id,
        external_ref="other-tenant-patient",
        display_name="Other tenant patient",
    )
    db_session.add(other_tenant_patient)
    db_session.flush()

    patient = SqlAlchemyPatientRepository(db_session).get_for_actor(
        patient_id=other_tenant_patient.id,
        organization_id=actor_organization.id,
    )

    assert patient is None
