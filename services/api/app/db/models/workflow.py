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
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.sql.naming import conv

from app.domain.enums import AgentRunStatus, AuditActorType, ManualReviewTaskState
from app.domain.types import uuid7

from .shared import Base, state_constraint, state_enum, tenant_identity_constraint

ACTOR_SHAPE_SQL = (
    "CASE actor_type "
    "WHEN 'user' THEN actor_user_id IS NOT NULL AND actor_agent_run_id IS NULL "
    "AND actor_policy_component IS NULL AND actor_policy_version IS NULL "
    "AND actor_system_component IS NULL AND actor_system_version IS NULL "
    "WHEN 'agent' THEN actor_user_id IS NULL AND actor_agent_run_id IS NOT NULL "
    "AND actor_policy_component IS NULL AND actor_policy_version IS NULL "
    "AND actor_system_component IS NULL AND actor_system_version IS NULL "
    "WHEN 'policy' THEN actor_user_id IS NULL AND actor_agent_run_id IS NULL "
    "AND NULLIF(trim(actor_policy_component), '') IS NOT NULL "
    "AND NULLIF(trim(actor_policy_version), '') IS NOT NULL "
    "AND actor_system_component IS NULL AND actor_system_version IS NULL "
    "WHEN 'system' THEN actor_user_id IS NULL AND actor_agent_run_id IS NULL "
    "AND actor_policy_component IS NULL AND actor_policy_version IS NULL "
    "AND NULLIF(trim(actor_system_component), '') IS NOT NULL "
    "AND NULLIF(trim(actor_system_version), '') IS NOT NULL "
    "ELSE false END"
)


class WorkflowRun(Base):
    __tablename__ = "workflow_run"
    __table_args__ = (
        tenant_identity_constraint("workflow_run"),
        UniqueConstraint(
            "organization_id",
            "patient_id",
            "care_episode_id",
            "id",
            name="uq_workflow_run_org_patient_episode_id",
        ),
        CheckConstraint(
            "num_nonnulls(source_submission_id, reported_need_id) = 1",
            name="ck_workflow_run_source",
        ),
        CheckConstraint(
            "NULLIF(trim(initial_state), '') IS NOT NULL "
            "AND NULLIF(trim(current_state), '') IS NOT NULL",
            name="ck_workflow_run_state_nonblank",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_workflow_run_organization_patient",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id"],
            ["care_episode.organization_id", "care_episode.patient_id", "care_episode.id"],
            name="fk_workflow_run_organization_patient_episode",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
            [
                "check_in_submission.organization_id",
                "check_in_submission.patient_id",
                "check_in_submission.care_episode_id",
                "check_in_submission.id",
            ],
            name="fk_workflow_run_source_submission",
        ),
        ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "reported_need_id"],
            [
                "reported_need.organization_id",
                "reported_need.patient_id",
                "reported_need.care_episode_id",
                "reported_need.id",
            ],
            name="fk_workflow_run_reported_need",
        ),
        Index("ix_workflow_run_org_trace_id", "organization_id", "trace_id", unique=True),
        Index(
            "ix_workflow_run_org_patient_started",
            "organization_id",
            "patient_id",
            "started_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    care_episode_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_submission_id: Mapped[UUID | None] = mapped_column(Uuid)
    reported_need_id: Mapped[UUID | None] = mapped_column(Uuid)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    initial_state: Mapped[str] = mapped_column(String(64), nullable=False)
    current_state: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowTransitionEvent(Base):
    __tablename__ = "workflow_transition_event"
    __table_args__ = (
        tenant_identity_constraint("workflow_transition_event"),
        UniqueConstraint(
            "organization_id",
            "id",
            "workflow_run_id",
            name="uq_workflow_transition_event_org_id_workflow",
        ),
        UniqueConstraint(
            "organization_id",
            "workflow_run_id",
            "sequence_number",
            name="uq_workflow_transition_event_org_run_sequence",
        ),
        CheckConstraint(
            "sequence_number > 0",
            name=conv("ck_workflow_transition_event_ck_workflow_transition_eve_db7b"),
        ),
        CheckConstraint(
            "NULLIF(trim(from_state), '') IS NOT NULL "
            "AND NULLIF(trim(to_state), '') IS NOT NULL "
            "AND from_state <> to_state",
            name=conv("ck_workflow_transition_event_ck_workflow_transition_eve_cccc"),
        ),
        CheckConstraint(
            "actor_type IN ('user', 'agent', 'policy', 'system')",
            name=conv("ck_workflow_transition_event_ck_workflow_transition_eve_1080"),
        ),
        CheckConstraint(
            ACTOR_SHAPE_SQL,
            name=conv("ck_workflow_transition_event_ck_workflow_transition_eve_7b0d"),
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_run.organization_id", "workflow_run.id"],
            name="fk_workflow_transition_event_workflow_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "actor_agent_run_id"],
            ["agent_run.organization_id", "agent_run.id"],
            name="fk_workflow_transition_event_actor_agent_run",
            use_alter=True,
        ),
        Index(
            "ix_workflow_transition_event_org_run_sequence",
            "organization_id",
            "workflow_run_id",
            "sequence_number",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    workflow_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(64), nullable=False)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[AuditActorType] = mapped_column(
        state_enum(AuditActorType, "audit_actor_type"), nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_workflow_transition_event_actor_user")
    )
    actor_agent_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    actor_policy_component: Mapped[str | None] = mapped_column(String(128))
    actor_policy_version: Mapped[str | None] = mapped_column(String(64))
    actor_system_component: Mapped[str | None] = mapped_column(String(128))
    actor_system_version: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    transitioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_run"
    __table_args__ = (
        tenant_identity_constraint("agent_run"),
        UniqueConstraint(
            "organization_id",
            "id",
            "workflow_run_id",
            name="uq_agent_run_org_id_workflow",
        ),
        state_constraint("agent_run", "status", AgentRunStatus),
        CheckConstraint(
            "(workflow_run_id IS NULL AND workflow_transition_event_id IS NULL) OR "
            "(workflow_run_id IS NOT NULL AND workflow_transition_event_id IS NOT NULL)",
            name="ck_agent_run_workflow_lineage_shape",
        ),
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
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_run.organization_id", "workflow_run.id"],
            name="fk_agent_run_workflow_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_transition_event_id", "workflow_run_id"],
            [
                "workflow_transition_event.organization_id",
                "workflow_transition_event.id",
                "workflow_transition_event.workflow_run_id",
            ],
            name="fk_agent_run_workflow_transition_event",
        ),
        Index("ix_agent_run_org_patient_created", "organization_id", "patient_id", "created_at"),
        Index("ix_agent_run_org_trace_id", "organization_id", "trace_id", unique=True),
        Index(
            "ix_agent_run_org_transition",
            "organization_id",
            "workflow_transition_event_id",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    patient_id: Mapped[UUID | None] = mapped_column(Uuid)
    source_submission_id: Mapped[UUID | None] = mapped_column(Uuid)
    reported_need_id: Mapped[UUID | None] = mapped_column(Uuid)
    workflow_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    workflow_transition_event_id: Mapped[UUID | None] = mapped_column(Uuid)
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


class ManualReviewTask(Base):
    __tablename__ = "manual_review_task"
    __table_args__ = (
        tenant_identity_constraint("manual_review_task"),
        state_constraint("manual_review_task", "state", ManualReviewTaskState),
        CheckConstraint(
            "NULLIF(trim(failure_reason), '') IS NOT NULL",
            name="ck_manual_review_task_failure_reason",
        ),
        CheckConstraint(
            "(assignee_user_id IS NULL AND assigned_at IS NULL) OR "
            "(assignee_user_id IS NOT NULL AND assigned_at IS NOT NULL)",
            name="ck_manual_review_task_assignment_shape",
        ),
        CheckConstraint(
            "(state = 'open' AND assignee_user_id IS NULL AND resolved_by_user_id IS NULL "
            "AND resolved_at IS NULL AND resolution IS NULL) OR "
            "(state = 'assigned' AND assignee_user_id IS NOT NULL "
            "AND resolved_by_user_id IS NULL AND resolved_at IS NULL AND resolution IS NULL) OR "
            "(state = 'resolved' AND resolved_by_user_id IS NOT NULL "
            "AND resolved_at IS NOT NULL AND NULLIF(trim(resolution), '') IS NOT NULL)",
            name="ck_manual_review_task_resolution_shape",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_run.organization_id", "workflow_run.id"],
            name="fk_manual_review_task_workflow_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_run_id", "workflow_run_id"],
            ["agent_run.organization_id", "agent_run.id", "agent_run.workflow_run_id"],
            name="fk_manual_review_task_agent_run",
        ),
        Index(
            "ix_manual_review_task_org_state_created",
            "organization_id",
            "state",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    workflow_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, unique=True)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False)
    retry_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[ManualReviewTaskState] = mapped_column(
        state_enum(ManualReviewTaskState, "manual_review_task_state"),
        nullable=False,
        default=ManualReviewTaskState.OPEN,
    )
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_manual_review_task_assignee_user")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", name="fk_manual_review_task_resolved_by_user")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
