from dataclasses import dataclass
from uuid import UUID

from app.domain.enums import UserRole

Role = UserRole


@dataclass(frozen=True)
class CurrentActor:
    user_id: UUID
    organization_id: UUID
    role: Role
    patient_id: UUID | None = None
