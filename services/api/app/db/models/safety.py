from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import SafetySeverity, SafetySignalStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class SafetySignal(Base):
    __tablename__ = "safety_signal"
    __table_args__ = (
        tenant_identity_constraint("safety_signal"),
        state_constraint("status", SafetySignalStatus),
        state_constraint("severity", SafetySeverity),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_safety_signal_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_submission_id"],
            ["check_in_submission.organization_id", "check_in_submission.id"],
            name="fk_safety_signal_organization_submission",
        ),
        Index(
            "ix_safety_signal_org_patient_status_severity",
            "organization_id",
            "patient_id",
            "status",
            "severity",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    rule_code: Mapped[str] = mapped_column(String(128))
    severity: Mapped[SafetySeverity] = mapped_column(state_enum(SafetySeverity, "safety_severity"))
    status: Mapped[SafetySignalStatus] = mapped_column(
        state_enum(SafetySignalStatus, "safety_signal_status"), default=SafetySignalStatus.ACTIVE
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
