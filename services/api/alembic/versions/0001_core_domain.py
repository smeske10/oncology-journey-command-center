"""Create the immutable oncology journey core domain schema."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_core_domain"
down_revision = None
branch_labels = None
depends_on = None


USER_ROLE = postgresql.ENUM(
    "administrator", "navigator", "supporting_actor", name="user_role", create_type=False
)
CARE_EPISODE_STATUS = postgresql.ENUM(
    "active", "closed", name="care_episode_status", create_type=False
)
CHECK_IN_STATUS = postgresql.ENUM(
    "draft", "submitted", "processed", name="check_in_status", create_type=False
)
NEED_STATUS = postgresql.ENUM(
    "open", "in_progress", "resolved", "closed", name="need_status", create_type=False
)
SAFETY_SEVERITY = postgresql.ENUM(
    "routine", "urgent", "emergent", name="safety_severity", create_type=False
)
SAFETY_SIGNAL_STATUS = postgresql.ENUM(
    "active", "acknowledged", "resolved", name="safety_signal_status", create_type=False
)
NAVIGATION_TASK_STATUS = postgresql.ENUM(
    "open",
    "in_progress",
    "completed",
    "cancelled",
    name="navigation_task_status",
    create_type=False,
)
APPROVAL_STATUS = postgresql.ENUM(
    "approved", "edited", "declined", name="approval_status", create_type=False
)
KNOWLEDGE_DOCUMENT_STATUS = postgresql.ENUM(
    "draft", "approved", "retired", name="knowledge_document_status", create_type=False
)
AGENT_RUN_STATUS = postgresql.ENUM(
    "pending", "succeeded", "failed", "manual_review", name="agent_run_status", create_type=False
)
OUTCOME_STATUS = postgresql.ENUM(
    "resolved", "closed_unresolved", name="outcome_status", create_type=False
)
ENUM_TYPES = (
    USER_ROLE,
    CARE_EPISODE_STATUS,
    CHECK_IN_STATUS,
    NEED_STATUS,
    SAFETY_SEVERITY,
    SAFETY_SIGNAL_STATUS,
    NAVIGATION_TASK_STATUS,
    APPROVAL_STATUS,
    KNOWLEDGE_DOCUMENT_STATUS,
    AGENT_RUN_STATUS,
    OUTCOME_STATUS,
)


def _id() -> sa.Column[sa.Uuid]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _organization_id() -> sa.Column[sa.Uuid]:
    return sa.Column("organization_id", sa.Uuid(), nullable=False)


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _organization_fk(table_name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name=f"fk_{table_name}_organization_id_organization",
    )


def _tenant_identity(table_name: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("organization_id", "id", name=f"uq_{table_name}_organization_id_id")


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ENUM_TYPES:
        enum_type.create(bind, checkfirst=False)

    op.create_table(
        "organization",
        _id(),
        sa.Column("name", sa.String(length=255), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("name", name="uq_organization_name"),
    )
    op.create_table(
        "user_account",
        _id(),
        _organization_id(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_user_account"),
        _tenant_identity("user_account"),
        _organization_fk("user_account"),
    )
    op.create_index(
        "ix_user_account_org_email", "user_account", ["organization_id", "email"], unique=True
    )
    op.create_table(
        "synthetic_patient",
        _id(),
        _organization_id(),
        sa.Column("external_ref", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("demographics", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_synthetic_patient"),
        _tenant_identity("synthetic_patient"),
        _organization_fk("synthetic_patient"),
    )
    op.create_index(
        "ix_synthetic_patient_org_external_ref",
        "synthetic_patient",
        ["organization_id", "external_ref"],
        unique=True,
    )
    op.create_table(
        "pathway_definition",
        _id(),
        _organization_id(),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_pathway_definition"),
        _tenant_identity("pathway_definition"),
        _organization_fk("pathway_definition"),
    )
    op.create_index(
        "ix_pathway_definition_org_slug_version",
        "pathway_definition",
        ["organization_id", "slug", "version"],
        unique=True,
    )
    op.create_table(
        "resource",
        _id(),
        _organization_id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("url", sa.String(2048)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_resource"),
        _tenant_identity("resource"),
        _organization_fk("resource"),
    )
    op.create_index(
        "ix_resource_org_category_active", "resource", ["organization_id", "category", "is_active"]
    )
    op.create_table(
        "role_assignment",
        _id(),
        _organization_id(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", USER_ROLE, nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_role_assignment"),
        _tenant_identity("role_assignment"),
        sa.CheckConstraint(
            "role IN ('administrator', 'navigator', 'supporting_actor')",
            name="ck_role_assignment_role_state",
        ),
        _organization_fk("role_assignment"),
        sa.ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_role_assignment_organization_user_account",
        ),
    )
    op.create_index(
        "ix_role_assignment_org_user",
        "role_assignment",
        ["organization_id", "user_id", "role"],
        unique=True,
    )
    op.create_table(
        "care_episode",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("pathway_definition_id", sa.Uuid(), nullable=False),
        sa.Column("status", CARE_EPISODE_STATUS, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_care_episode"),
        _tenant_identity("care_episode"),
        sa.CheckConstraint("status IN ('active', 'closed')", name="ck_care_episode_status_state"),
        _organization_fk("care_episode"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_care_episode_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_care_episode_organization_pathway_definition",
        ),
    )
    op.create_index(
        "ix_care_episode_org_patient_status",
        "care_episode",
        ["organization_id", "patient_id", "status"],
    )
    op.create_table(
        "check_in_definition",
        _id(),
        _organization_id(),
        sa.Column("pathway_definition_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("questionnaire", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_check_in_definition"),
        _tenant_identity("check_in_definition"),
        _organization_fk("check_in_definition"),
        sa.ForeignKeyConstraint(
            ["organization_id", "pathway_definition_id"],
            ["pathway_definition.organization_id", "pathway_definition.id"],
            name="fk_check_in_definition_organization_pathway_definition",
        ),
    )
    op.create_index(
        "ix_check_in_definition_org_pathway_slug_version",
        "check_in_definition",
        ["organization_id", "pathway_definition_id", "slug", "version"],
        unique=True,
    )
    op.create_table(
        "knowledge_document",
        _id(),
        _organization_id(),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", KNOWLEDGE_DOCUMENT_STATUS, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_document"),
        _tenant_identity("knowledge_document"),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'retired')", name="ck_knowledge_document_status_state"
        ),
        _organization_fk("knowledge_document"),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_id"],
            ["resource.organization_id", "resource.id"],
            name="fk_knowledge_document_organization_resource",
        ),
    )
    op.create_index(
        "ix_knowledge_document_org_status_reviewed",
        "knowledge_document",
        ["organization_id", "status", "reviewed_at"],
    )
    op.create_table(
        "audit_event",
        _id(),
        _organization_id(),
        sa.Column("actor_user_id", sa.Uuid()),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
        _tenant_identity("audit_event"),
        _organization_fk("audit_event"),
        sa.ForeignKeyConstraint(
            ["organization_id", "actor_user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_audit_event_organization_actor",
        ),
    )
    op.create_index(
        "ix_audit_event_org_entity_created",
        "audit_event",
        ["organization_id", "entity_type", "entity_id", "created_at"],
    )
    op.create_table(
        "check_in_submission",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("check_in_definition_id", sa.Uuid()),
        sa.Column("status", CHECK_IN_STATUS, nullable=False),
        sa.Column("answers", postgresql.JSONB(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_check_in_submission"),
        _tenant_identity("check_in_submission"),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'processed')",
            name="ck_check_in_submission_status_state",
        ),
        _organization_fk("check_in_submission"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_check_in_submission_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "check_in_definition_id"],
            ["check_in_definition.organization_id", "check_in_definition.id"],
            name="fk_check_in_submission_organization_check_in_definition",
        ),
    )
    op.create_index(
        "ix_check_in_submission_org_patient_created",
        "check_in_submission",
        ["organization_id", "patient_id", "created_at"],
    )
    op.create_table(
        "reported_need",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("source_submission_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", NEED_STATUS, nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_reported_need"),
        _tenant_identity("reported_need"),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'closed')",
            name="ck_reported_need_status_state",
        ),
        _organization_fk("reported_need"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_reported_need_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_submission_id"],
            ["check_in_submission.organization_id", "check_in_submission.id"],
            name="fk_reported_need_organization_submission",
        ),
    )
    op.create_index(
        "ix_reported_need_org_patient_status_created",
        "reported_need",
        ["organization_id", "patient_id", "status", "created_at"],
    )
    op.create_table(
        "safety_signal",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("source_submission_id", sa.Uuid(), nullable=False),
        sa.Column("rule_code", sa.String(128), nullable=False),
        sa.Column("severity", SAFETY_SEVERITY, nullable=False),
        sa.Column("status", SAFETY_SIGNAL_STATUS, nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_safety_signal"),
        _tenant_identity("safety_signal"),
        sa.CheckConstraint(
            "status IN ('active', 'acknowledged', 'resolved')", name="ck_safety_signal_status_state"
        ),
        sa.CheckConstraint(
            "severity IN ('routine', 'urgent', 'emergent')", name="ck_safety_signal_severity_state"
        ),
        _organization_fk("safety_signal"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_safety_signal_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_submission_id"],
            ["check_in_submission.organization_id", "check_in_submission.id"],
            name="fk_safety_signal_organization_submission",
        ),
    )
    op.create_index(
        "ix_safety_signal_org_patient_status_severity",
        "safety_signal",
        ["organization_id", "patient_id", "status", "severity"],
    )
    op.create_table(
        "agent_run",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid()),
        sa.Column("source_submission_id", sa.Uuid()),
        sa.Column("reported_need_id", sa.Uuid()),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("status", AGENT_RUN_STATUS, nullable=False),
        sa.Column("input_payload", postgresql.JSONB(), nullable=False),
        sa.Column("output_payload", postgresql.JSONB(), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run"),
        _tenant_identity("agent_run"),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'manual_review')",
            name="ck_agent_run_status_state",
        ),
        _organization_fk("agent_run"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_agent_run_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_submission_id"],
            ["check_in_submission.organization_id", "check_in_submission.id"],
            name="fk_agent_run_organization_submission",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.id"],
            name="fk_agent_run_organization_reported_need",
        ),
    )
    op.create_index(
        "ix_agent_run_org_patient_created",
        "agent_run",
        ["organization_id", "patient_id", "created_at"],
    )
    op.create_index(
        "ix_agent_run_org_trace_id", "agent_run", ["organization_id", "trace_id"], unique=True
    )
    op.create_table(
        "navigation_task",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("reported_need_id", sa.Uuid()),
        sa.Column("assignee_user_id", sa.Uuid()),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", NAVIGATION_TASK_STATUS, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_navigation_task"),
        _tenant_identity("navigation_task"),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'completed', 'cancelled')",
            name="ck_navigation_task_status_state",
        ),
        _organization_fk("navigation_task"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_navigation_task_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.id"],
            name="fk_navigation_task_organization_reported_need",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assignee_user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_navigation_task_organization_assignee",
        ),
    )
    op.create_index(
        "ix_navigation_task_org_need_status",
        "navigation_task",
        ["organization_id", "reported_need_id", "status"],
    )
    op.create_index(
        "ix_navigation_task_org_status_due_at",
        "navigation_task",
        ["organization_id", "status", "due_at"],
    )
    op.create_table(
        "outcome",
        _id(),
        _organization_id(),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("reported_need_id", sa.Uuid(), nullable=False),
        sa.Column("status", OUTCOME_STATUS, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_outcome"),
        _tenant_identity("outcome"),
        sa.UniqueConstraint("reported_need_id", name="uq_outcome_reported_need_id"),
        sa.CheckConstraint(
            "status IN ('resolved', 'closed_unresolved')", name="ck_outcome_status_state"
        ),
        _organization_fk("outcome"),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_outcome_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reported_need_id"],
            ["reported_need.organization_id", "reported_need.id"],
            name="fk_outcome_organization_reported_need",
        ),
    )
    op.create_index(
        "ix_outcome_org_patient_created", "outcome", ["organization_id", "patient_id", "created_at"]
    )
    op.create_table(
        "approval_decision",
        _id(),
        _organization_id(),
        sa.Column("navigation_task_id", sa.Uuid(), nullable=False),
        sa.Column("authorized_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", APPROVAL_STATUS, nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=False),
        sa.Column("final_value", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text()),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decision"),
        _tenant_identity("approval_decision"),
        sa.CheckConstraint(
            "status IN ('approved', 'edited', 'declined')", name="ck_approval_decision_status_state"
        ),
        _organization_fk("approval_decision"),
        sa.ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_approval_decision_organization_navigation_task",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "authorized_user_id"],
            ["user_account.organization_id", "user_account.id"],
            name="fk_approval_decision_organization_authorized_user",
        ),
    )
    op.create_index(
        "ix_approval_decision_org_task_created",
        "approval_decision",
        ["organization_id", "navigation_task_id", "created_at"],
    )


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_approval_decision_org_task_created", "approval_decision"),
        ("ix_outcome_org_patient_created", "outcome"),
        ("ix_navigation_task_org_status_due_at", "navigation_task"),
        ("ix_navigation_task_org_need_status", "navigation_task"),
        ("ix_agent_run_org_trace_id", "agent_run"),
        ("ix_agent_run_org_patient_created", "agent_run"),
        ("ix_safety_signal_org_patient_status_severity", "safety_signal"),
        ("ix_reported_need_org_patient_status_created", "reported_need"),
        ("ix_check_in_submission_org_patient_created", "check_in_submission"),
        ("ix_audit_event_org_entity_created", "audit_event"),
        ("ix_knowledge_document_org_status_reviewed", "knowledge_document"),
        ("ix_check_in_definition_org_pathway_slug_version", "check_in_definition"),
        ("ix_care_episode_org_patient_status", "care_episode"),
        ("ix_role_assignment_org_user", "role_assignment"),
        ("ix_resource_org_category_active", "resource"),
        ("ix_pathway_definition_org_slug_version", "pathway_definition"),
        ("ix_synthetic_patient_org_external_ref", "synthetic_patient"),
        ("ix_user_account_org_email", "user_account"),
    ):
        op.drop_index(index_name, table_name=table_name)
    for table_name in (
        "approval_decision",
        "outcome",
        "navigation_task",
        "agent_run",
        "safety_signal",
        "reported_need",
        "check_in_submission",
        "audit_event",
        "knowledge_document",
        "check_in_definition",
        "care_episode",
        "role_assignment",
        "resource",
        "pathway_definition",
        "synthetic_patient",
        "user_account",
        "organization",
    ):
        op.drop_table(table_name)
    bind = op.get_bind()
    for enum_type in reversed(ENUM_TYPES):
        enum_type.drop(bind, checkfirst=False)
