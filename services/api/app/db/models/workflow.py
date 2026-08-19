from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import AgentRunStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        tenant_identity_constraint("agent_run"),
        state_constraint("status", AgentRunStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_agent_run_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_submission_id"],
            ["check_in_submission.organization_id", "check_in_submission.id"],
            name="fk_agent_run_organization_submission",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.id"],
            name="fk_agent_run_organization_reported_need",
        ),
        Index("ix_agent_run_org_patient_created", "organization_id", "patient_id", "created_at"),
        Index("ix_agent_run_org_trace_id", "organization_id", "trace_id", unique=True),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID | None] = mapped_column(Uuid)
    source_submission_id: Mapped[UUID | None] = mapped_column(Uuid)
    reported_need_id: Mapped[UUID | None] = mapped_column(Uuid)
    trace_id: Mapped[str] = mapped_column(String(128))
    agent_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[AgentRunStatus] = mapped_column(
        state_enum(AgentRunStatus, "agent_run_status"), default=AgentRunStatus.PENDING
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
