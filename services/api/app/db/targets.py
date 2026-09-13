from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

POSTGRESQL_DEFAULT_PORT = 5432


class DatabaseTargetError(ValueError):
    """Base error for a database target that cannot be used safely."""


class DatabaseTargetConfigurationError(DatabaseTargetError):
    """A database URL is absent, malformed, or incomplete."""


class DatabaseTargetMismatch(DatabaseTargetError):
    """The runtime and migration credentials identify different targets."""


class DatabaseCredentialConflict(DatabaseTargetError):
    """The runtime and migration credentials are not distinct."""


@dataclass(frozen=True)
class DatabaseTarget:
    backend: str
    host: str
    port: int
    database: str
    username: str


def parse_database_target(url_text: str | None, *, label: str) -> DatabaseTarget:
    """Parse a PostgreSQL URL into the fields that identify its target."""
    if not isinstance(url_text, str) or not url_text.strip():
        raise DatabaseTargetConfigurationError(f"{label} is required")

    try:
        url = make_url(url_text.strip())
    except (ArgumentError, TypeError, ValueError) as error:
        raise DatabaseTargetConfigurationError(f"{label} is not a valid database URL") from error

    backend = url.get_backend_name()
    if backend != "postgresql":
        raise DatabaseTargetConfigurationError(f"{label} must use a PostgreSQL backend")
    if not url.host or not url.host.strip():
        raise DatabaseTargetConfigurationError(f"{label} must include a host")
    if not url.database or not url.database.strip():
        raise DatabaseTargetConfigurationError(f"{label} must include a database name")
    if not url.username or not url.username.strip():
        raise DatabaseTargetConfigurationError(f"{label} must include a username")

    return DatabaseTarget(
        backend=backend,
        host=url.host.casefold(),
        port=url.port or POSTGRESQL_DEFAULT_PORT,
        database=url.database,
        username=url.username,
    )


def validate_database_target_pair(
    *, application_url: str, migration_url: str
) -> tuple[DatabaseTarget, DatabaseTarget]:
    """Require two distinct credentials for one exact PostgreSQL target."""
    application = parse_database_target(application_url, label="DATABASE_URL")
    migration = parse_database_target(migration_url, label="MIGRATION_DATABASE_URL")

    compared_fields = (
        ("backend", application.backend, migration.backend),
        ("host", application.host, migration.host),
        ("port", application.port, migration.port),
        ("database name", application.database, migration.database),
    )
    for field_name, application_value, migration_value in compared_fields:
        if application_value != migration_value:
            raise DatabaseTargetMismatch(
                f"DATABASE_URL and MIGRATION_DATABASE_URL must use the same {field_name}"
            )

    if application.username == migration.username:
        raise DatabaseCredentialConflict(
            "DATABASE_URL and MIGRATION_DATABASE_URL must use distinct usernames"
        )

    return application, migration
