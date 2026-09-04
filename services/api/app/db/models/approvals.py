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
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.sql.naming import conv

from app.domain.enums import (
    ApprovalChangeType,
    ApprovalDecisionValue,
    SafetySeverity,
    UserRole,
)
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class PatientMessage(Base):
    __tablename__ = "patient_message"
    __table_args__ = (
        tenant_identity_constraint("patient_message"),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_patient_message_organization_patient",
        ),
        Index(
            "ix_patient_message_org_patient_created",
            "organization_id",
            "patient_id",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProposedValueSchema(Base):
    __tablename__ = "proposed_value_schema"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('dismiss_signal', 'override_signal_severity', "
            "'authorize_navigation_task', 'authorize_patient_message')",
            name=conv("ck_proposed_value_schema_ck_proposed_value_schema_chang_7b44"),
        ),
        CheckConstraint(
            "value_schema_version >= 1",
            name=conv("ck_proposed_value_schema_ck_proposed_value_schema_value_5948"),
        ),
    )
    change_type: Mapped[ApprovalChangeType] = mapped_column(
        state_enum(ApprovalChangeType, "approval_change_type"), primary_key=True
    )
    value_schema_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    value_schema_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalPolicy(Base):
    __tablename__ = "approval_policy"
    __table_args__ = (
        tenant_identity_constraint("approval_policy"),
        state_constraint("approval_policy", "change_type", ApprovalChangeType),
        CheckConstraint(
            "required_approver_role IN "
            "('administrator', 'navigator', 'supporting_actor')",
            name=conv("ck_approval_policy_ck_approval_policy_required_approver_5cb2"),
        ),
        CheckConstraint(
            "deterministic_severity_threshold IN ('routine', 'urgent', 'emergent')",
            name=conv("ck_approval_policy_ck_approval_policy_deterministic_sev_585d"),
        ),
        CheckConstraint(
            "(change_type = 'dismiss_signal' "
            "AND deterministic_severity_threshold IS NOT NULL) OR "
            "(change_type <> 'dismiss_signal' "
            "AND deterministic_severity_threshold IS NULL)",
            name="ck_approval_policy_dismissal_threshold_shape",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from < effective_to",
            name="ck_approval_policy_effective_interval",
        ),
        CheckConstraint(
            "required_approval_count >= 1",
            name="ck_approval_policy_required_approval_count",
        ),
        UniqueConstraint(
            "organization_id",
            "id",
            "version",
            name="uq_approval_policy_organization_id_version",
        ),
        ExcludeConstraint(
            ("organization_id", "="),
            ("change_type", "="),
            (text("tstzrange(effective_from, effective_to, '[)')"), "&&"),
            name="ex_approval_policy_no_overlap",
            using="gist",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        Index(
            "ix_approval_policy_org_change_version",
            "organization_id",
            "change_type",
            "version",
            unique=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    change_type: Mapped[ApprovalChangeType] = mapped_column(
        state_enum(ApprovalChangeType, "approval_change_type"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deterministic_severity_threshold: Mapped[SafetySeverity | None] = mapped_column(
        state_enum(SafetySeverity, "safety_severity")
    )
    allow_self_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    required_approval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    required_approver_role: Mapped[UserRole] = mapped_column(
        state_enum(UserRole, "user_role"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProposedChange(Base):
    __tablename__ = "proposed_change"
    __table_args__ = (
        tenant_identity_constraint("proposed_change"),
        state_constraint("proposed_change", "change_type", ApprovalChangeType),
        CheckConstraint(
            "required_approver_role_snapshot IN "
            "('administrator', 'navigator', 'supporting_actor')",
            name=conv("ck_proposed_change_ck_proposed_change_required_approver_1505"),
        ),
        CheckConstraint(
            "deterministic_severity_threshold_snapshot IN "
            "('routine', 'urgent', 'emergent')",
            name=conv("ck_proposed_change_ck_proposed_change_deterministic_sev_1b36"),
        ),
        CheckConstraint(
            "num_nonnulls(proposed_by_user_id, proposed_by_agent_run_id) = 1",
            name="ck_proposed_change_proposer",
        ),
        CheckConstraint(
            "num_nonnulls(safety_signal_id, navigation_task_id, patient_message_id) = 1",
            name="ck_proposed_change_target_count",
        ),
        CheckConstraint(
            "CASE change_type "
            "WHEN 'dismiss_signal' THEN safety_signal_id IS NOT NULL "
            "WHEN 'override_signal_severity' THEN safety_signal_id IS NOT NULL "
            "WHEN 'authorize_navigation_task' THEN navigation_task_id IS NOT NULL "
            "WHEN 'authorize_patient_message' THEN patient_message_id IS NOT NULL "
            "ELSE false END",
            name="ck_proposed_change_target_type",
        ),
        CheckConstraint(
            "required_approval_count_snapshot >= 1",
            name=conv("ck_proposed_change_ck_proposed_change_required_approval_54e5"),
        ),
        CheckConstraint(
            "value_schema_version >= 1",
            name=conv("ck_proposed_change_ck_proposed_change_value_schema_vers_e771"),
        ),
        CheckConstraint(
            "(change_type = 'dismiss_signal' "
            "AND deterministic_severity_threshold_snapshot IS NOT NULL) OR "
            "(change_type <> 'dismiss_signal' "
            "AND deterministic_severity_threshold_snapshot IS NULL)",
            name=conv("ck_proposed_change_ck_proposed_change_dismissal_thresho_b719"),
        ),
        UniqueConstraint(
            "id",
            "safety_signal_id",
            "organization_id",
            "change_type",
            name="uq_proposed_change_signal_authorization_unit",
        ),
        ForeignKeyConstraint(
            ["organization_id", "safety_signal_id"],
            ["safety_signal.organization_id", "safety_signal.id"],
            name="fk_proposed_change_safety_signal",
        ),
        ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_proposed_change_navigation_task",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_message_id"],
            ["patient_message.organization_id", "patient_message.id"],
            name="fk_proposed_change_patient_message",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_proposed_change_id"],
            ["proposed_change.organization_id", "proposed_change.id"],
            name="fk_proposed_change_predecessor",
        ),
        ForeignKeyConstraint(
            ["organization_id", "approval_policy_id", "approval_policy_version"],
            ["approval_policy.organization_id", "approval_policy.id", "approval_policy.version"],
            name="fk_proposed_change_policy_version",
        ),
        ForeignKeyConstraint(
            ["organization_id", "proposed_by_agent_run_id"],
            ["agent_run.organization_id", "agent_run.id"],
            name="fk_proposed_change_agent_proposer",
        ),
        ForeignKeyConstraint(
            ["change_type", "value_schema_id", "value_schema_version"],
            [
                "proposed_value_schema.change_type",
                "proposed_value_schema.value_schema_id",
                "proposed_value_schema.value_schema_version",
            ],
            name="fk_proposed_change_value_schema",
        ),
        Index("ix_proposed_change_org_created", "organization_id", "proposed_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    proposed_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("user_account.id"))
    proposed_by_agent_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    change_type: Mapped[ApprovalChangeType] = mapped_column(
        state_enum(ApprovalChangeType, "approval_change_type"), nullable=False
    )
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    value_schema_id: Mapped[str] = mapped_column(String(255), nullable=False)
    value_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_proposed_change_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    safety_signal_id: Mapped[UUID | None] = mapped_column(Uuid)
    navigation_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    patient_message_id: Mapped[UUID | None] = mapped_column(Uuid)
    approval_policy_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    approval_policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deterministic_severity_threshold_snapshot: Mapped[SafetySeverity | None] = mapped_column(
        state_enum(SafetySeverity, "safety_severity")
    )
    allow_self_approval_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False)
    required_approval_count_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    required_approver_role_snapshot: Mapped[UserRole] = mapped_column(
        state_enum(UserRole, "user_role"), nullable=False
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decision"
    __table_args__ = (
        tenant_identity_constraint("approval_decision"),
        state_constraint("approval_decision", "decision", ApprovalDecisionValue),
        CheckConstraint(
            "qualifying_role_snapshot IN "
            "('administrator', 'navigator', 'supporting_actor')",
            name=conv("ck_approval_decision_ck_approval_decision_qualifying_ro_7502"),
        ),
        CheckConstraint(
            "(decision = 'declined' AND NULLIF(trim(reason), '') IS NOT NULL) "
            "OR decision = 'approved'",
            name="ck_approval_decision_decline_reason",
        ),
        UniqueConstraint(
            "proposed_change_id",
            "authorized_by_user_id",
            name="uq_approval_decision_proposal_authorizer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "proposed_change_id"],
            ["proposed_change.organization_id", "proposed_change.id"],
            name="fk_approval_decision_proposed_change",
        ),
        ForeignKeyConstraint(
            ["organization_id", "authorized_by_user_id", "qualifying_role_assignment_id"],
            ["role_assignment.organization_id", "role_assignment.user_id", "role_assignment.id"],
            name="fk_approval_decision_qualifying_role",
        ),
        Index(
            "ix_approval_decision_org_proposal_authorized",
            "organization_id",
            "proposed_change_id",
            "authorized_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    proposed_change_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    authorized_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id"), nullable=False
    )
    qualifying_role_assignment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    qualifying_role_snapshot: Mapped[UserRole] = mapped_column(
        state_enum(UserRole, "user_role"), nullable=False
    )
    decision: Mapped[ApprovalDecisionValue] = mapped_column(
        state_enum(ApprovalDecisionValue, "approval_decision_value"), nullable=False
    )
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
