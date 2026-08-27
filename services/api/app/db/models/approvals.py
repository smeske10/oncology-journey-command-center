from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import ApprovalStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class ApprovalDecision(Base):
    __tablename__ = "approval_decision"
    __table_args__ = (
        tenant_identity_constraint("approval_decision"),
        state_constraint("approval_decision", "status", ApprovalStatus),
        ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_approval_decision_organization_navigation_task",
        ),
        Index(
            "ix_approval_decision_org_task_created",
            "organization_id",
            "navigation_task_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    navigation_task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    authorized_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", name="fk_approval_decision_authorized_user"), nullable=False
    )
    status: Mapped[ApprovalStatus] = mapped_column(state_enum(ApprovalStatus, "approval_status"))
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    final_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
