from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.types import uuid7

from .shared import Base, tenant_identity_constraint


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        tenant_identity_constraint("audit_event"),
        ForeignKeyConstraint(
            ["organization_id", "actor_user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_audit_event_organization_actor",
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
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    entity_type: Mapped[str] = mapped_column(String(128))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    event_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
