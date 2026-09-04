from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.sql.naming import conv

from app.domain.types import uuid7

from .shared import Base, tenant_identity_constraint


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
        UniqueConstraint(
            "organization_id",
            "id",
            "version",
            name="uq_knowledge_document_org_id_version",
        ),
        ForeignKeyConstraint(
            ["organization_id", "resource_id"],
            ["resource.organization_id", "resource.id"],
            name="fk_knowledge_document_organization_resource",
        ),
        Index(
            "ix_knowledge_document_org_title_version",
            "organization_id",
            "title",
            "version",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrganizationKnowledgeApproval(Base):
    __tablename__ = "organization_knowledge_approval"
    __table_args__ = (
        tenant_identity_constraint("organization_knowledge_approval"),
        UniqueConstraint(
            "organization_id",
            "knowledge_document_id",
            "knowledge_document_version",
            name="uq_organization_knowledge_approval_document_version",
        ),
        CheckConstraint(
            "approved_at <= effective_from",
            name=conv("ck_organization_knowledge_approval_ck_organization_know_6e48"),
        ),
        CheckConstraint(
            "(withdrawn_at IS NULL AND withdrawn_by_user_id IS NULL "
            "AND withdrawn_by_role_assignment_id IS NULL "
            "AND withdrawal_reason IS NULL) OR "
            "(withdrawn_at IS NOT NULL AND withdrawn_by_user_id IS NOT NULL "
            "AND withdrawn_by_role_assignment_id IS NOT NULL "
            "AND NULLIF(trim(withdrawal_reason), '') IS NOT NULL "
            "AND effective_from < withdrawn_at)",
            name=conv("ck_organization_knowledge_approval_ck_organization_know_da80"),
        ),
        ForeignKeyConstraint(
            ["organization_id", "knowledge_document_id", "knowledge_document_version"],
            [
                "knowledge_document.organization_id",
                "knowledge_document.id",
                "knowledge_document.version",
            ],
            name="fk_organization_knowledge_approval_document_version",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "approved_by_user_id",
                "approved_by_role_assignment_id",
            ],
            [
                "role_assignment.organization_id",
                "role_assignment.user_id",
                "role_assignment.id",
            ],
            name="fk_knowledge_approval_approved_role",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "withdrawn_by_user_id",
                "withdrawn_by_role_assignment_id",
            ],
            [
                "role_assignment.organization_id",
                "role_assignment.user_id",
                "role_assignment.id",
            ],
            name="fk_knowledge_approval_withdrawn_role",
        ),
        Index(
            "ix_organization_knowledge_approval_org_effective",
            "organization_id",
            "effective_from",
            "withdrawn_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    knowledge_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    approved_by_role_assignment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_by_user_id: Mapped[UUID | None] = mapped_column(Uuid)
    withdrawn_by_role_assignment_id: Mapped[UUID | None] = mapped_column(Uuid)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRunCitation(Base):
    __tablename__ = "agent_run_citation"
    __table_args__ = (
        tenant_identity_constraint("agent_run_citation"),
        CheckConstraint(
            "NULLIF(trim(passage), '') IS NOT NULL",
            name="ck_agent_run_citation_passage_nonblank",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_run_id"],
            ["agent_run.organization_id", "agent_run.id"],
            name="fk_agent_run_citation_agent_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "knowledge_document_id", "knowledge_document_version"],
            [
                "knowledge_document.organization_id",
                "knowledge_document.id",
                "knowledge_document.version",
            ],
            name="fk_agent_run_citation_document_version",
        ),
        Index(
            "ix_agent_run_citation_org_agent_cited",
            "organization_id",
            "agent_run_id",
            "cited_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    passage: Mapped[str] = mapped_column(Text, nullable=False)
    cited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NavigationTaskResource(Base):
    __tablename__ = "navigation_task_resource"
    __table_args__ = (
        tenant_identity_constraint("navigation_task_resource"),
        UniqueConstraint(
            "proposed_change_id",
            "resource_id",
            name="uq_navigation_task_resource_proposal_resource",
        ),
        CheckConstraint(
            "NULLIF(trim(resource_name_snapshot), '') IS NOT NULL "
            "AND NULLIF(trim(resource_category_snapshot), '') IS NOT NULL "
            "AND NULLIF(trim(match_rationale_snapshot), '') IS NOT NULL",
            name=conv("ck_navigation_task_resource_ck_navigation_task_resource_a508"),
        ),
        CheckConstraint(
            "(delivered_at IS NULL AND delivered_by_user_id IS NULL) OR "
            "(approved_at IS NOT NULL AND delivered_at IS NOT NULL "
            "AND delivered_by_user_id IS NOT NULL AND approved_at <= delivered_at)",
            name=conv("ck_navigation_task_resource_ck_navigation_task_resource_955e"),
        ),
        ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_navigation_task_resource_navigation_task",
        ),
        ForeignKeyConstraint(
            ["organization_id", "resource_id"],
            ["resource.organization_id", "resource.id"],
            name="fk_navigation_task_resource_resource",
        ),
        ForeignKeyConstraint(
            ["organization_id", "proposed_change_id"],
            ["proposed_change.organization_id", "proposed_change.id"],
            name="fk_navigation_task_resource_proposed_change",
        ),
        Index(
            "ix_navigation_task_resource_org_task_proposed",
            "organization_id",
            "navigation_task_id",
            "proposed_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    navigation_task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    proposed_change_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    resource_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_category_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_url_snapshot: Mapped[str | None] = mapped_column(String(2048))
    resource_metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    match_rationale_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_navigation_task_resource_delivered_by_user")
    )
