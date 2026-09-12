from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.privilege_attestation import (
    RuntimePrivilegeBoundaryError,
    attest_runtime_database,
)
from app.db.targets import parse_database_target

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def attest_runtime_engine() -> None:
    """Open one startup connection and attest the configured runtime boundary."""
    target = parse_database_target(settings.database_url, label="DATABASE_URL")
    try:
        with engine.connect() as connection:
            attest_runtime_database(connection, target=target)
    except RuntimePrivilegeBoundaryError:
        raise
    except SQLAlchemyError:
        raise RuntimePrivilegeBoundaryError(
            "runtime_database_boundary.unavailable",
            "database attestation could not be completed",
        ) from None


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
