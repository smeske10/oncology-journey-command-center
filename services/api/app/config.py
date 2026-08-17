import os
from dataclasses import dataclass, field
from uuid import UUID


def _optional_uuid_from_environment(name: str) -> UUID | None:
    value = os.getenv(name)
    return UUID(value) if value else None


@dataclass(frozen=True)
class Settings:
    api_title: str = "Oncology Journey Command Center API"
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://ojcc:local-synthetic-only@localhost:5432/ojcc"
        )
    )
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "local"))
    demo_session_secret: str | None = field(
        default_factory=lambda: os.getenv("DEMO_SESSION_SECRET")
    )
    demo_session_ttl_minutes: int = field(
        default_factory=lambda: int(os.getenv("DEMO_SESSION_TTL_MINUTES", "30"))
    )
    demo_organization_id: UUID | None = field(
        default_factory=lambda: _optional_uuid_from_environment("DEMO_ORGANIZATION_ID")
    )

    @property
    def is_local_development(self) -> bool:
        return self.environment == "local"


settings = Settings()
