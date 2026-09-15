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


@dataclass(frozen=True)
class ResolvedAuthority:
    actor: CurrentActor
    role_assignment_id: UUID
    patient_identity_link_id: UUID | None = None


@dataclass(frozen=True)
class VerifiedDemoSession:
    authority: ResolvedAuthority
