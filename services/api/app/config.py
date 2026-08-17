import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    api_title: str = "Oncology Journey Command Center API"
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://ojcc:local-synthetic-only@localhost:5432/ojcc"
        )
    )


settings = Settings()
