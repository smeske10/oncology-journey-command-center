from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import NavigationTaskStatus, NeedStatus, OutcomeStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint

if TYPE_CHECKING:
    from .submissions import CheckInSubmission


class ReportedNeed(Base):
    __tablename__ = "reported_need"
    __table_args__ = (
        tenant_identity_constraint("reported_need"),
        state_constraint("reported_need", "status", NeedStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_reported_need_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_submission_id"],
            ["check_in_submission.organization_id", "check_in_submission.id"],
            name="fk_reported_need_organization_submission",
        ),
        Index(
            "ix_reported_need_org_patient_status_created",
            "organization_id",
            "patient_id",
            "status",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[NeedStatus] = mapped_column(
        state_enum(NeedStatus, "need_status"), default=NeedStatus.OPEN
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_submission: Mapped["CheckInSubmission"] = relationship(back_populates="reported_needs")
    navigation_tasks: Mapped[list["NavigationTask"]] = relationship(back_populates="reported_need")
    outcome: Mapped["Outcome | None"] = relationship(back_populates="reported_need", uselist=False)


class NavigationTask(Base):
    __tablename__ = "navigation_task"
    __table_args__ = (
        tenant_identity_constraint("navigation_task"),
        state_constraint("navigation_task", "status", NavigationTaskStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_navigation_task_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.id"],
            name="fk_navigation_task_organization_reported_need",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assignee_user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_navigation_task_organization_assignee",
        ),
        Index("ix_navigation_task_org_status_due_at", "organization_id", "status", "due_at"),
        Index(
            "ix_navigation_task_org_need_status", "organization_id", "reported_need_id", "status"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reported_need_id: Mapped[UUID | None] = mapped_column(Uuid)
    assignee_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[NavigationTaskStatus] = mapped_column(
        state_enum(NavigationTaskStatus, "navigation_task_status"),
        default=NavigationTaskStatus.OPEN,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_need: Mapped[ReportedNeed | None] = relationship(back_populates="navigation_tasks")


class Outcome(Base):
    __tablename__ = "outcome"
    __table_args__ = (
        tenant_identity_constraint("outcome"),
        state_constraint("outcome", "status", OutcomeStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_outcome_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.id"],
            name="fk_outcome_organization_reported_need",
        ),
        Index("ix_outcome_org_patient_created", "organization_id", "patient_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reported_need_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    status: Mapped[OutcomeStatus] = mapped_column(state_enum(OutcomeStatus, "outcome_status"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reported_need: Mapped[ReportedNeed] = relationship(back_populates="outcome")
