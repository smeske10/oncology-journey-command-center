from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.enums import KnowledgeDocumentStatus
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class Resource(Base):
    __tablename__ = "resource"
    __table_args__ = (
        tenant_identity_constraint("resource"),
        Index("ix_resource_org_category_active", "organization_id", "category", "is_active"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    url: Mapped[str | None] = mapped_column(String(2048))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_document"
    __table_args__ = (
        tenant_identity_constraint("knowledge_document"),
        state_constraint("knowledge_document", "status", KnowledgeDocumentStatus),
        ForeignKeyConstraint(
            ["organization_id", "resource_id"],
            ["resource.organization_id", "resource.id"],
            name="fk_knowledge_document_organization_resource",
        ),
        Index(
            "ix_knowledge_document_org_status_reviewed", "organization_id", "status", "reviewed_at"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        state_enum(KnowledgeDocumentStatus, "knowledge_document_status"),
        default=KnowledgeDocumentStatus.DRAFT,
    )
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
