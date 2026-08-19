from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import CheckInStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint

if TYPE_CHECKING:
    from .needs import ReportedNeed


class CheckInSubmission(Base):
    __tablename__ = "check_in_submission"
    __table_args__ = (
        tenant_identity_constraint("check_in_submission"),
        state_constraint("status", CheckInStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_check_in_submission_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "check_in_definition_id"],
            ["check_in_definition.organization_id", "check_in_definition.id"],
            name="fk_check_in_submission_organization_check_in_definition",
        ),
        Index(
            "ix_check_in_submission_org_patient_created",
            "organization_id",
            "patient_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    check_in_definition_id: Mapped[UUID | None] = mapped_column(Uuid)
    status: Mapped[CheckInStatus] = mapped_column(
        state_enum(CheckInStatus, "check_in_status"), default=CheckInStatus.DRAFT
    )
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reported_needs: Mapped[list["ReportedNeed"]] = relationship(back_populates="source_submission")
