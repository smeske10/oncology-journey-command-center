from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import AuditActorType
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint
from .workflow import ACTOR_SHAPE_SQL


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        tenant_identity_constraint("audit_event"),
        state_constraint("audit_event", "actor_type", AuditActorType),
        CheckConstraint(ACTOR_SHAPE_SQL, name="ck_audit_event_actor_shape"),
        ForeignKeyConstraint(
            ["organization_id", "actor_agent_run_id"],
            ["agent_run.organization_id", "agent_run.id"],
            name="fk_audit_event_actor_agent_run",
        ),
        Index(
            "ix_audit_event_org_entity_created",
            "organization_id",
            "entity_type",
            "entity_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    actor_type: Mapped[AuditActorType] = mapped_column(
        state_enum(AuditActorType, "audit_actor_type"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_audit_event_actor_user")
    )
    actor_agent_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    actor_policy_component: Mapped[str | None] = mapped_column(String(128))
    actor_policy_version: Mapped[str | None] = mapped_column(String(64))
    actor_system_component: Mapped[str | None] = mapped_column(String(128))
    actor_system_version: Mapped[str | None] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
