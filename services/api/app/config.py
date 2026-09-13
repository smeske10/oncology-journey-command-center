import os
from dataclasses import dataclass, field
from uuid import UUID

from app.db.targets import DatabaseTargetConfigurationError
from app.domain.prioritization import OperationalPriorityWeights, policy_from_json


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise DatabaseTargetConfigurationError(f"{name} is required")
    return value


def _optional_uuid_from_environment(name: str) -> UUID | None:
    value = os.getenv(name)
    if value is None:
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        return None


def _optional_int_from_environment(name: str, default: int) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip(), 10)
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    api_title: str = "Oncology Journey Command Center API"
    database_url: str = field(default_factory=lambda: _required_environment("DATABASE_URL"))
    migration_database_url: str | None = field(
        default_factory=lambda: os.getenv("MIGRATION_DATABASE_URL")
    )
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "local"))
    demo_session_secret: str | None = field(
        default_factory=lambda: os.getenv("DEMO_SESSION_SECRET")
    )
    demo_session_ttl_minutes: int | None = field(
        default_factory=lambda: _optional_int_from_environment(
            "DEMO_SESSION_TTL_MINUTES", 30
        )
    )
    demo_organization_id: UUID | None = field(
        default_factory=lambda: _optional_uuid_from_environment("DEMO_ORGANIZATION_ID")
    )
    demo_actors_json: str | None = field(
        default_factory=lambda: os.getenv("DEMO_ACTORS_JSON")
    )
    navigator_priority_weights_json: str | None = field(
        default_factory=lambda: os.getenv("NAVIGATOR_PRIORITY_WEIGHTS_JSON")
    )

    @property
    def navigator_priority_policy(self) -> OperationalPriorityWeights:
        """Validated deployment policy; malformed environment JSON uses documented safe defaults."""
        return policy_from_json(self.navigator_priority_weights_json)

    def require_migration_database_url(self) -> str:
        value = self.migration_database_url
        if value is None or not value.strip():
            raise DatabaseTargetConfigurationError("MIGRATION_DATABASE_URL is required")
        return value

    @property
    def is_local_development(self) -> bool:
        return self.environment == "local"


settings = Settings()
