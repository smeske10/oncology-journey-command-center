from __future__ import annotations

from datetime import date, datetime
from enum import Enum as PythonEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.domain.enums import (
    AgentRunStatus,
    ApprovalStatus,
    CareEpisodeStatus,
    CheckInStatus,
    KnowledgeDocumentStatus,
    NavigationTaskStatus,
    NeedStatus,
    OutcomeStatus,
    SafetySeverity,
    SafetySignalStatus,
    UserRole,
)
from app.domain.types import uuid7


def _state_enum(enum_class: type[PythonEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda values: [member.value for member in values],
        native_enum=True,
        create_constraint=False,
    )


def _state_constraint(column: str, enum_class: type[PythonEnum]) -> CheckConstraint:
    allowed_values = ", ".join(f"'{member.value}'" for member in enum_class)
    return CheckConstraint(f"{column} IN ({allowed_values})", name=f"{column}_state")


def _tenant_identity_constraint(table_name: str) -> UniqueConstraint:
    return UniqueConstraint("organization_id", "id", name=f"uq_{table_name}_organization_id_id")


class Organization(Base):
    __tablename__ = "organization"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "user_account"
    __table_args__ = (
        _tenant_identity_constraint("user_account"),
        Index("ix_user_account_org_email", "organization_id", "email", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleAssignment(Base):
    __tablename__ = "role_assignment"
    __table_args__ = (
        _tenant_identity_constraint("role_assignment"),
        _state_constraint("role", UserRole),
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_role_assignment_organization_user_account",
        ),
        Index("ix_role_assignment_org_user", "organization_id", "user_id", "role", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[UserRole] = mapped_column(_state_enum(UserRole, "user_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyntheticPatient(Base):
    __tablename__ = "synthetic_patient"
    __table_args__ = (
        _tenant_identity_constraint("synthetic_patient"),
        Index(
            "ix_synthetic_patient_org_external_ref", "organization_id", "external_ref", unique=True
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(255))
    birth_date: Mapped[date | None] = mapped_column(Date)
    demographics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PathwayDefinition(Base):
    __tablename__ = "pathway_definition"
    __table_args__ = (
        _tenant_identity_constraint("pathway_definition"),
        Index(
            "ix_pathway_definition_org_slug_version",
            "organization_id",
            "slug",
            "version",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    slug: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CareEpisode(Base):
    __tablename__ = "care_episode"
    __table_args__ = (
        _tenant_identity_constraint("care_episode"),
        _state_constraint("status", CareEpisodeStatus),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_care_episode_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_care_episode_organization_pathway_definition",
        ),
        Index("ix_care_episode_org_patient_status", "organization_id", "patient_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    pathway_definition_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[CareEpisodeStatus] = mapped_column(
        _state_enum(CareEpisodeStatus, "care_episode_status"), default=CareEpisodeStatus.ACTIVE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckInDefinition(Base):
    __tablename__ = "check_in_definition"
    __table_args__ = (
        _tenant_identity_constraint("check_in_definition"),
        ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_check_in_definition_organization_pathway_definition",
        ),
        Index(
            "ix_check_in_definition_org_pathway_slug_version",
            "organization_id",
            "pathway_definition_id",
            "slug",
            "version",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    pathway_definition_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    slug: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    questionnaire: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    pathway_definition: Mapped[PathwayDefinition] = relationship()


class CheckInSubmission(Base):
    __tablename__ = "check_in_submission"
    __table_args__ = (
        _tenant_identity_constraint("check_in_submission"),
        _state_constraint("status", CheckInStatus),
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
        _state_enum(CheckInStatus, "check_in_status"), default=CheckInStatus.DRAFT
    )
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reported_needs: Mapped[list["ReportedNeed"]] = relationship(back_populates="source_submission")


class ReportedNeed(Base):
    __tablename__ = "reported_need"
    __table_args__ = (
        _tenant_identity_constraint("reported_need"),
        _state_constraint("status", NeedStatus),
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
        _state_enum(NeedStatus, "need_status"), default=NeedStatus.OPEN
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_submission: Mapped[CheckInSubmission] = relationship(back_populates="reported_needs")
    navigation_tasks: Mapped[list["NavigationTask"]] = relationship(back_populates="reported_need")
    outcome: Mapped["Outcome | None"] = relationship(back_populates="reported_need", uselist=False)


class SafetySignal(Base):
    __tablename__ = "safety_signal"
    __table_args__ = (
        _tenant_identity_constraint("safety_signal"),
        _state_constraint("status", SafetySignalStatus),
        _state_constraint("severity", SafetySeverity),
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
    severity: Mapped[SafetySeverity] = mapped_column(_state_enum(SafetySeverity, "safety_severity"))
    status: Mapped[SafetySignalStatus] = mapped_column(
        _state_enum(SafetySignalStatus, "safety_signal_status"), default=SafetySignalStatus.ACTIVE
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NavigationTask(Base):
    __tablename__ = "navigation_task"
    __table_args__ = (
        _tenant_identity_constraint("navigation_task"),
        _state_constraint("status", NavigationTaskStatus),
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
        _state_enum(NavigationTaskStatus, "navigation_task_status"),
        default=NavigationTaskStatus.OPEN,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reported_need: Mapped[ReportedNeed | None] = relationship(back_populates="navigation_tasks")


class ApprovalDecision(Base):
    __tablename__ = "approval_decision"
    __table_args__ = (
        _tenant_identity_constraint("approval_decision"),
        _state_constraint("status", ApprovalStatus),
        ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_approval_decision_organization_navigation_task",
        ),
        ForeignKeyConstraint(
            ["organization_id", "authorized_user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_approval_decision_organization_authorized_user",
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
    authorized_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(_state_enum(ApprovalStatus, "approval_status"))
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    final_value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Resource(Base):
    __tablename__ = "resource"
    __table_args__ = (
        _tenant_identity_constraint("resource"),
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
        _tenant_identity_constraint("knowledge_document"),
        _state_constraint("status", KnowledgeDocumentStatus),
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
        _state_enum(KnowledgeDocumentStatus, "knowledge_document_status"),
        default=KnowledgeDocumentStatus.DRAFT,
    )
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        _tenant_identity_constraint("agent_run"),
        _state_constraint("status", AgentRunStatus),
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
        _state_enum(AgentRunStatus, "agent_run_status"), default=AgentRunStatus.PENDING
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outcome(Base):
    __tablename__ = "outcome"
    __table_args__ = (
        _tenant_identity_constraint("outcome"),
        _state_constraint("status", OutcomeStatus),
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
    status: Mapped[OutcomeStatus] = mapped_column(_state_enum(OutcomeStatus, "outcome_status"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reported_need: Mapped[ReportedNeed] = relationship(back_populates="outcome")


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        _tenant_identity_constraint("audit_event"),
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
