from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import (
    NavigationTaskStatus,
    NeedStatus,
    OutcomeDisposition,
    TaskCancellationReason,
)
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
            ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
            [
                "check_in_submission.organization_id",
                "check_in_submission.patient_id",
                "check_in_submission.care_episode_id",
                "check_in_submission.id",
            ],
            name="fk_reported_need_origin_submission",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "reopened_from_need_id"],
            [
                "reported_need.organization_id",
                "reported_need.patient_id",
                "reported_need.care_episode_id",
                "reported_need.id",
            ],
            name="fk_reported_need_reopened_predecessor",
        ),
        UniqueConstraint(
            "organization_id",
            "patient_id",
            "care_episode_id",
            "id",
            name="uq_reported_need_org_patient_episode_id",
        ),
        UniqueConstraint(
            "organization_id",
            "patient_id",
            "id",
            name="uq_reported_need_org_patient_id",
        ),
        UniqueConstraint(
            "reopened_from_need_id",
            name="uq_reported_need_reopened_from_need_id",
        ),
        CheckConstraint(
            "num_nonnulls(source_submission_id, reopened_from_need_id) = 1",
            name="ck_reported_need_origin",
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
    care_episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_submission_id: Mapped[UUID | None] = mapped_column(Uuid)
    reopened_from_need_id: Mapped[UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[NeedStatus] = mapped_column(
        state_enum(NeedStatus, "need_status"), default=NeedStatus.OPEN
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_submission: Mapped["CheckInSubmission | None"] = relationship(
        back_populates="reported_needs"
    )
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
            ["organization_id", "patient_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.patient_id", "reported_need.id"],
            name="fk_navigation_task_parent_need",
        ),
        CheckConstraint(
            "(status = 'open' AND assignee_user_id IS NULL) OR "
            "(status = 'assigned' AND assignee_user_id IS NOT NULL) OR "
            "status IN ('in_progress', 'completed', 'cancelled')",
            name="ck_navigation_task_assignment_shape",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_by_user_id IS NOT NULL "
            "AND cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL) OR "
            "(status <> 'cancelled' AND cancelled_by_user_id IS NULL "
            "AND cancelled_at IS NULL AND cancellation_reason IS NULL)",
            name="ck_navigation_task_cancellation_shape",
        ),
        Index("ix_navigation_task_org_status_due_at", "organization_id", "status", "due_at"),
        Index(
            "ix_navigation_task_org_need_status", "organization_id", "reported_need_id", "status"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reported_need_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_navigation_task_assignee_user")
    )
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[NavigationTaskStatus] = mapped_column(
        state_enum(NavigationTaskStatus, "navigation_task_status"),
        default=NavigationTaskStatus.OPEN,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_navigation_task_cancelled_by_user")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[TaskCancellationReason | None] = mapped_column(
        state_enum(TaskCancellationReason, "task_cancellation_reason")
    )
    reported_need: Mapped[ReportedNeed] = relationship(back_populates="navigation_tasks")


class Outcome(Base):
    __tablename__ = "outcome"
    __table_args__ = (
        tenant_identity_constraint("outcome"),
        state_constraint("outcome", "disposition", OutcomeDisposition),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_outcome_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.patient_id", "reported_need.id"],
            name="fk_outcome_parent_need",
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_outcome_organization_idempotency_key",
        ),
        Index("ix_outcome_org_patient_recorded", "organization_id", "patient_id", "recorded_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reported_need_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", name="fk_outcome_recorded_by_user"), nullable=False
    )
    disposition: Mapped[OutcomeDisposition] = mapped_column(
        state_enum(OutcomeDisposition, "outcome_disposition"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_need: Mapped[ReportedNeed] = relationship(back_populates="outcome")
