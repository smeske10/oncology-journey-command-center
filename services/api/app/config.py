from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_title: str = "Oncology Journey Command Center API"


settings = Settings()
