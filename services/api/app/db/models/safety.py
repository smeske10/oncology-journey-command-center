from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.domain.enums import (
    ApprovalChangeType,
    SafetySeverity,
    SafetySignalStatus,
    SignalRuleKind,
)
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint


class SignalRule(Base):
    __tablename__ = "signal_rule"
    __table_args__ = (
        tenant_identity_constraint("signal_rule"),
        state_constraint("signal_rule", "rule_kind", SignalRuleKind),
        UniqueConstraint(
            "organization_id",
            "id",
            "version",
            name="uq_signal_rule_organization_id_version",
        ),
        Index(
            "ix_signal_rule_org_code_version",
            "organization_id",
            "rule_code",
            "version",
            unique=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_kind: Mapped[SignalRuleKind] = mapped_column(
        state_enum(SignalRuleKind, "signal_rule_kind"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SafetySignal(Base):
    __tablename__ = "safety_signal"
    __table_args__ = (
        tenant_identity_constraint("safety_signal"),
        state_constraint("safety_signal", "status", SafetySignalStatus),
        state_constraint("safety_signal", "deterministic_level", SafetySeverity),
        state_constraint("safety_signal", "effective_level", SafetySeverity),
        CheckConstraint(
            "num_nonnulls(source_submission_id, escalated_from_signal_id) = 1",
            name="ck_safety_signal_origin",
        ),
        CheckConstraint(
            "escalated_from_signal_id IS NULL OR escalated_from_signal_id <> id",
            name="ck_safety_signal_escalation_not_self",
        ),
        CheckConstraint(
            "(status = 'open' AND acknowledged_by_user_id IS NULL AND acknowledged_at IS NULL) "
            "OR (status = 'acknowledged' AND acknowledged_by_user_id IS NOT NULL "
            "AND acknowledged_at IS NOT NULL)",
            name="ck_safety_signal_acknowledgement_shape",
        ),
        CheckConstraint(
            "dismissal_change_type = 'dismiss_signal'",
            name="ck_safety_signal_dismissal_change_type",
        ),
        CheckConstraint(
            "current_severity_override_change_type = 'override_signal_severity'",
            name="ck_safety_signal_override_change_type",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_safety_signal_organization_patient",
        ),
        UniqueConstraint(
            "organization_id",
            "patient_id",
            "care_episode_id",
            "id",
            name="uq_safety_signal_org_patient_episode_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
            [
                "check_in_submission.organization_id",
                "check_in_submission.patient_id",
                "check_in_submission.care_episode_id",
                "check_in_submission.id",
            ],
            name="fk_safety_signal_origin_submission",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "escalated_from_signal_id"],
            [
                "safety_signal.organization_id",
                "safety_signal.patient_id",
                "safety_signal.care_episode_id",
                "safety_signal.id",
            ],
            name="fk_safety_signal_escalated_predecessor",
        ),
        ForeignKeyConstraint(
            ["organization_id", "signal_rule_id", "signal_rule_version"],
            ["signal_rule.organization_id", "signal_rule.id", "signal_rule.version"],
            name="fk_safety_signal_versioned_rule",
        ),
        ForeignKeyConstraint(
            ["dismissal_proposed_change_id", "id", "organization_id", "dismissal_change_type"],
            [
                "proposed_change.id",
                "proposed_change.safety_signal_id",
                "proposed_change.organization_id",
                "proposed_change.change_type",
            ],
            name="fk_safety_signal_dismissal_proposal",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            [
                "current_severity_override_proposed_change_id",
                "id",
                "organization_id",
                "current_severity_override_change_type",
            ],
            [
                "proposed_change.id",
                "proposed_change.safety_signal_id",
                "proposed_change.organization_id",
                "proposed_change.change_type",
            ],
            name="fk_safety_signal_current_override_proposal",
            use_alter=True,
        ),
        Index(
            "ix_safety_signal_org_patient_status_effective",
            "organization_id",
            "patient_id",
            "status",
            "effective_level",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    care_episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_submission_id: Mapped[UUID | None] = mapped_column(Uuid)
    escalated_from_signal_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    signal_rule_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    signal_rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    deterministic_level: Mapped[SafetySeverity] = mapped_column(
        state_enum(SafetySeverity, "safety_severity"), nullable=False
    )
    effective_level: Mapped[SafetySeverity] = mapped_column(
        state_enum(SafetySeverity, "safety_severity"), nullable=False
    )
    status: Mapped[SafetySignalStatus] = mapped_column(
        state_enum(SafetySignalStatus, "safety_signal_status"),
        default=SafetySignalStatus.OPEN,
        nullable=False,
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, nullable=False)
    acknowledged_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("user_account.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissal_proposed_change_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    dismissal_change_type: Mapped[ApprovalChangeType] = mapped_column(
        state_enum(ApprovalChangeType, "approval_change_type"),
        default=ApprovalChangeType.DISMISS_SIGNAL,
        server_default=ApprovalChangeType.DISMISS_SIGNAL.value,
        nullable=False,
    )
    current_severity_override_proposed_change_id: Mapped[UUID | None] = mapped_column(
        Uuid, unique=True
    )
    current_severity_override_change_type: Mapped[ApprovalChangeType] = mapped_column(
        state_enum(ApprovalChangeType, "approval_change_type"),
        default=ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY,
        server_default=ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rule: Mapped[SignalRule] = relationship()

    @property
    def rule_code(self) -> str:
        return self.rule.rule_code

    @property
    def severity(self) -> SafetySeverity:
        return self.effective_level


class SafetySignalResolution(Base):
    __tablename__ = "safety_signal_resolution"
    __table_args__ = (
        tenant_identity_constraint("safety_signal_resolution"),
        ForeignKeyConstraint(
            ["organization_id", "safety_signal_id"],
            ["safety_signal.organization_id", "safety_signal.id"],
            name="fk_safety_signal_resolution_signal",
        ),
        Index(
            "ix_safety_signal_resolution_org_resolved",
            "organization_id",
            "resolved_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    safety_signal_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, unique=True)
    resolved_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolution_reason: Mapped[str] = mapped_column(Text, nullable=False)
