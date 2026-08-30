"""Add governed workflow, knowledge, resource, and audit lineage."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_workflow_knowledge_audit"
down_revision: str | None = "0004_safety_approval_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_ACTOR_TYPE = postgresql.ENUM(
    "user", "agent", "policy", "system", name="audit_actor_type", create_type=False
)
MANUAL_REVIEW_TASK_STATE = postgresql.ENUM(
    "open", "assigned", "resolved", name="manual_review_task_state", create_type=False
)

ACTOR_SHAPE_SQL = """
CASE actor_type
  WHEN 'user' THEN actor_user_id IS NOT NULL AND actor_agent_run_id IS NULL
    AND actor_policy_component IS NULL AND actor_policy_version IS NULL
    AND actor_system_component IS NULL AND actor_system_version IS NULL
  WHEN 'agent' THEN actor_user_id IS NULL AND actor_agent_run_id IS NOT NULL
    AND actor_policy_component IS NULL AND actor_policy_version IS NULL
    AND actor_system_component IS NULL AND actor_system_version IS NULL
  WHEN 'policy' THEN actor_user_id IS NULL AND actor_agent_run_id IS NULL
    AND NULLIF(trim(actor_policy_component), '') IS NOT NULL
    AND NULLIF(trim(actor_policy_version), '') IS NOT NULL
    AND actor_system_component IS NULL AND actor_system_version IS NULL
  WHEN 'system' THEN actor_user_id IS NULL AND actor_agent_run_id IS NULL
    AND actor_policy_component IS NULL AND actor_policy_version IS NULL
    AND NULLIF(trim(actor_system_component), '') IS NOT NULL
    AND NULLIF(trim(actor_system_version), '') IS NOT NULL
  ELSE false
END
"""


def upgrade() -> None:
    _guard_populated_upgrade()
    bind = op.get_bind()
    AUDIT_ACTOR_TYPE.create(bind, checkfirst=True)
    MANUAL_REVIEW_TASK_STATE.create(bind, checkfirst=True)
    _create_workflow_lineage()
    _create_knowledge_governance()
    _reconcile_audit_actors()
    _create_workflow_guards()
    _create_knowledge_guards()
    _replace_proposal_guards()
    _replace_closure_audit_function()
    _create_append_only_backstops()
    _create_application_role_surface()


def _guard_populated_upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE ambiguous_audit_ids text;
        DECLARE ambiguous_document_ids text;
        BEGIN
            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            INTO ambiguous_audit_ids
            FROM audit_event
            WHERE actor_user_id IS NULL;
            IF ambiguous_audit_ids IS NOT NULL THEN
                RAISE EXCEPTION
                    'Task 5 cannot infer audit actor provenance for AuditEvent id(s): %. '
                    'Do not invent user, agent, policy, or system attribution; '
                    'reset the synthetic demo database instead.',
                    ambiguous_audit_ids;
            END IF;

            SELECT string_agg(id::text, ', ' ORDER BY id::text)
            INTO ambiguous_document_ids
            FROM knowledge_document
            WHERE status <> 'draft';
            IF ambiguous_document_ids IS NOT NULL THEN
                RAISE EXCEPTION
                    'Task 5 cannot infer approval or withdrawal provenance for '
                    'KnowledgeDocument id(s): %. Reset the synthetic demo database instead.',
                    ambiguous_document_ids;
            END IF;
        END;
        $$
        """
    )


def _create_workflow_lineage() -> None:
    op.create_table(
        "workflow_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("care_episode_id", sa.Uuid(), nullable=False),
        sa.Column("source_submission_id", sa.Uuid()),
        sa.Column("reported_need_id", sa.Uuid()),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("initial_state", sa.String(length=64), nullable=False),
        sa.Column("current_state", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_run"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_workflow_run_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "patient_id",
            "care_episode_id",
            "id",
            name="uq_workflow_run_org_patient_episode_id",
        ),
        sa.CheckConstraint(
            "num_nonnulls(source_submission_id, reported_need_id) = 1",
            name="ck_workflow_run_source",
        ),
        sa.CheckConstraint(
            "NULLIF(trim(initial_state), '') IS NOT NULL "
            "AND NULLIF(trim(current_state), '') IS NOT NULL",
            name="ck_workflow_run_state_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_workflow_run_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_workflow_run_organization_patient",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id"],
            ["care_episode.organization_id", "care_episode.patient_id", "care_episode.id"],
            name="fk_workflow_run_organization_patient_episode",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
            [
                "check_in_submission.organization_id",
                "check_in_submission.patient_id",
                "check_in_submission.care_episode_id",
                "check_in_submission.id",
            ],
            name="fk_workflow_run_source_submission",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id", "care_episode_id", "reported_need_id"],
            [
                "reported_need.organization_id",
                "reported_need.patient_id",
                "reported_need.care_episode_id",
                "reported_need.id",
            ],
            name="fk_workflow_run_reported_need",
        ),
    )
    op.create_index(
        "ix_workflow_run_org_trace_id",
        "workflow_run",
        ["organization_id", "trace_id"],
        unique=True,
    )
    op.create_index(
        "ix_workflow_run_org_patient_started",
        "workflow_run",
        ["organization_id", "patient_id", "started_at"],
    )

    op.create_table(
        "workflow_transition_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=64), nullable=False),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.Column("actor_type", AUDIT_ACTOR_TYPE, nullable=False),
        sa.Column("actor_user_id", sa.Uuid()),
        sa.Column("actor_agent_run_id", sa.Uuid()),
        sa.Column("actor_policy_component", sa.String(length=128)),
        sa.Column("actor_policy_version", sa.String(length=64)),
        sa.Column("actor_system_component", sa.String(length=128)),
        sa.Column("actor_system_version", sa.String(length=64)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transitioned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_transition_event"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_workflow_transition_event_organization_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "workflow_run_id",
            name="uq_workflow_transition_event_org_id_workflow",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "workflow_run_id",
            "sequence_number",
            name="uq_workflow_transition_event_org_run_sequence",
        ),
        sa.CheckConstraint(
            "sequence_number > 0",
            name=op.f("ck_workflow_transition_event_ck_workflow_transition_eve_db7b"),
        ),
        sa.CheckConstraint(
            "NULLIF(trim(from_state), '') IS NOT NULL "
            "AND NULLIF(trim(to_state), '') IS NOT NULL AND from_state <> to_state",
            name=op.f("ck_workflow_transition_event_ck_workflow_transition_eve_cccc"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'agent', 'policy', 'system')",
            name=op.f("ck_workflow_transition_event_ck_workflow_transition_eve_1080"),
        ),
        sa.CheckConstraint(
            ACTOR_SHAPE_SQL,
            name=op.f("ck_workflow_transition_event_ck_workflow_transition_eve_7b0d"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_workflow_transition_event_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user_account.id"],
            name="fk_workflow_transition_event_actor_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_run.organization_id", "workflow_run.id"],
            name="fk_workflow_transition_event_workflow_run",
        ),
    )
    op.create_index(
        "ix_workflow_transition_event_org_run_sequence",
        "workflow_transition_event",
        ["organization_id", "workflow_run_id", "sequence_number"],
    )

    op.add_column("agent_run", sa.Column("workflow_run_id", sa.Uuid()))
    op.add_column("agent_run", sa.Column("workflow_transition_event_id", sa.Uuid()))
    op.create_unique_constraint(
        "uq_agent_run_org_id_workflow",
        "agent_run",
        ["organization_id", "id", "workflow_run_id"],
    )
    op.create_check_constraint(
        "ck_agent_run_workflow_lineage_shape",
        "agent_run",
        "(workflow_run_id IS NULL AND workflow_transition_event_id IS NULL) OR "
        "(workflow_run_id IS NOT NULL AND workflow_transition_event_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_agent_run_workflow_run",
        "agent_run",
        "workflow_run",
        ["organization_id", "workflow_run_id"],
        ["organization_id", "id"],
    )
    op.create_foreign_key(
        "fk_agent_run_workflow_transition_event",
        "agent_run",
        "workflow_transition_event",
        ["organization_id", "workflow_transition_event_id", "workflow_run_id"],
        ["organization_id", "id", "workflow_run_id"],
    )
    op.create_index(
        "ix_agent_run_org_transition",
        "agent_run",
        ["organization_id", "workflow_transition_event_id"],
    )
    op.create_foreign_key(
        "fk_workflow_transition_event_actor_agent_run",
        "workflow_transition_event",
        "agent_run",
        ["organization_id", "actor_agent_run_id"],
        ["organization_id", "id"],
    )

    op.create_table(
        "manual_review_task",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("retry_context", postgresql.JSONB(), nullable=False),
        sa.Column("state", MANUAL_REVIEW_TASK_STATE, nullable=False),
        sa.Column("assignee_user_id", sa.Uuid()),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manual_review_task"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_manual_review_task_organization_id_id"
        ),
        sa.UniqueConstraint("agent_run_id", name="uq_manual_review_task_agent_run_id"),
        sa.CheckConstraint(
            "state IN ('open', 'assigned', 'resolved')",
            name="ck_manual_review_task_state_state",
        ),
        sa.CheckConstraint(
            "NULLIF(trim(failure_reason), '') IS NOT NULL",
            name="ck_manual_review_task_failure_reason",
        ),
        sa.CheckConstraint(
            "(assignee_user_id IS NULL AND assigned_at IS NULL) OR "
            "(assignee_user_id IS NOT NULL AND assigned_at IS NOT NULL)",
            name="ck_manual_review_task_assignment_shape",
        ),
        sa.CheckConstraint(
            "(state = 'open' AND assignee_user_id IS NULL AND resolved_by_user_id IS NULL "
            "AND resolved_at IS NULL AND resolution IS NULL) OR "
            "(state = 'assigned' AND assignee_user_id IS NOT NULL "
            "AND resolved_by_user_id IS NULL AND resolved_at IS NULL AND resolution IS NULL) OR "
            "(state = 'resolved' AND resolved_by_user_id IS NOT NULL "
            "AND resolved_at IS NOT NULL AND NULLIF(trim(resolution), '') IS NOT NULL)",
            name="ck_manual_review_task_resolution_shape",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_manual_review_task_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"],
            ["user_account.id"],
            name="fk_manual_review_task_assignee_user",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["user_account.id"],
            name="fk_manual_review_task_resolved_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workflow_run_id"],
            ["workflow_run.organization_id", "workflow_run.id"],
            name="fk_manual_review_task_workflow_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_run_id", "workflow_run_id"],
            ["agent_run.organization_id", "agent_run.id", "agent_run.workflow_run_id"],
            name="fk_manual_review_task_agent_run",
        ),
    )
    op.create_index(
        "ix_manual_review_task_org_state_created",
        "manual_review_task",
        ["organization_id", "state", "created_at"],
    )


def _create_knowledge_governance() -> None:
    op.drop_index("ix_knowledge_document_org_status_reviewed", table_name="knowledge_document")
    op.drop_constraint(
        "ck_knowledge_document_status_state", "knowledge_document", type_="check"
    )
    op.create_unique_constraint(
        "uq_knowledge_document_org_id_version",
        "knowledge_document",
        ["organization_id", "id", "version"],
    )
    op.drop_column("knowledge_document", "status")
    op.drop_column("knowledge_document", "reviewed_at")
    op.create_index(
        "ix_knowledge_document_org_title_version",
        "knowledge_document",
        ["organization_id", "title", "version"],
    )

    op.create_table(
        "organization_knowledge_approval",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_version", sa.String(length=64), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawn_by_user_id", sa.Uuid()),
        sa.Column("withdrawal_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_knowledge_approval"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_organization_knowledge_approval_organization_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "knowledge_document_id",
            "knowledge_document_version",
            name="uq_organization_knowledge_approval_document_version",
        ),
        sa.CheckConstraint(
            "approved_at <= effective_from",
            name=op.f("ck_organization_knowledge_approval_ck_organization_know_6e48"),
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL AND withdrawn_by_user_id IS NULL "
            "AND withdrawal_reason IS NULL) OR "
            "(withdrawn_at IS NOT NULL AND withdrawn_by_user_id IS NOT NULL "
            "AND NULLIF(trim(withdrawal_reason), '') IS NOT NULL "
            "AND effective_from < withdrawn_at)",
            name=op.f("ck_organization_knowledge_approval_ck_organization_know_da80"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_knowledge_approval_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["user_account.id"],
            name="fk_organization_knowledge_approval_approved_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by_user_id"],
            ["user_account.id"],
            name="fk_organization_knowledge_approval_withdrawn_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "knowledge_document_id", "knowledge_document_version"],
            [
                "knowledge_document.organization_id",
                "knowledge_document.id",
                "knowledge_document.version",
            ],
            name="fk_organization_knowledge_approval_document_version",
        ),
    )
    op.create_index(
        "ix_organization_knowledge_approval_org_effective",
        "organization_knowledge_approval",
        ["organization_id", "effective_from", "withdrawn_at"],
    )

    op.create_table(
        "agent_run_citation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_version", sa.String(length=64), nullable=False),
        sa.Column("passage", sa.Text(), nullable=False),
        sa.Column("cited_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_citation"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_agent_run_citation_organization_id_id"
        ),
        sa.CheckConstraint(
            "NULLIF(trim(passage), '') IS NOT NULL",
            name="ck_agent_run_citation_passage_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_agent_run_citation_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_run_id"],
            ["agent_run.organization_id", "agent_run.id"],
            name="fk_agent_run_citation_agent_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "knowledge_document_id", "knowledge_document_version"],
            [
                "knowledge_document.organization_id",
                "knowledge_document.id",
                "knowledge_document.version",
            ],
            name="fk_agent_run_citation_document_version",
        ),
    )
    op.create_index(
        "ix_agent_run_citation_org_agent_cited",
        "agent_run_citation",
        ["organization_id", "agent_run_id", "cited_at"],
    )

    op.create_table(
        "navigation_task_resource",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("navigation_task_id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_change_id", sa.Uuid(), nullable=False),
        sa.Column("resource_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("resource_category_snapshot", sa.String(length=64), nullable=False),
        sa.Column("resource_url_snapshot", sa.String(length=2048)),
        sa.Column("resource_metadata_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("match_rationale_snapshot", sa.Text(), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_by_user_id", sa.Uuid()),
        sa.PrimaryKeyConstraint("id", name="pk_navigation_task_resource"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_navigation_task_resource_organization_id_id"
        ),
        sa.UniqueConstraint(
            "proposed_change_id",
            "resource_id",
            name="uq_navigation_task_resource_proposal_resource",
        ),
        sa.CheckConstraint(
            "NULLIF(trim(resource_name_snapshot), '') IS NOT NULL "
            "AND NULLIF(trim(resource_category_snapshot), '') IS NOT NULL "
            "AND NULLIF(trim(match_rationale_snapshot), '') IS NOT NULL",
            name=op.f("ck_navigation_task_resource_ck_navigation_task_resource_a508"),
        ),
        sa.CheckConstraint(
            "(delivered_at IS NULL AND delivered_by_user_id IS NULL) OR "
            "(approved_at IS NOT NULL AND delivered_at IS NOT NULL "
            "AND delivered_by_user_id IS NOT NULL AND approved_at <= delivered_at)",
            name=op.f("ck_navigation_task_resource_ck_navigation_task_resource_955e"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_navigation_task_resource_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["delivered_by_user_id"],
            ["user_account.id"],
            name="fk_navigation_task_resource_delivered_by_user",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_navigation_task_resource_navigation_task",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "resource_id"],
            ["resource.organization_id", "resource.id"],
            name="fk_navigation_task_resource_resource",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposed_change_id"],
            ["proposed_change.organization_id", "proposed_change.id"],
            name="fk_navigation_task_resource_proposed_change",
        ),
    )
    op.create_index(
        "ix_navigation_task_resource_org_task_proposed",
        "navigation_task_resource",
        ["organization_id", "navigation_task_id", "proposed_at"],
    )

    op.execute(
        r"""
        INSERT INTO proposed_value_schema
            (change_type, value_schema_id, value_schema_version, schema_document)
        VALUES (
            'authorize_navigation_task', 'ojcc.authorize-navigation-task', 2,
            '{"type":"object","additionalProperties"\:false,
              "required":["title","resources"],
              "properties":{"title":{"type":"string","minLength"\:1,"maxLength"\:255},
              "resources":{"type":"array","items":{"type":"object"}}}}'::jsonb
        )
        """
    )


def _reconcile_audit_actors() -> None:
    op.add_column("audit_event", sa.Column("actor_type", AUDIT_ACTOR_TYPE))
    op.add_column("audit_event", sa.Column("actor_agent_run_id", sa.Uuid()))
    op.add_column("audit_event", sa.Column("actor_policy_component", sa.String(length=128)))
    op.add_column("audit_event", sa.Column("actor_policy_version", sa.String(length=64)))
    op.add_column("audit_event", sa.Column("actor_system_component", sa.String(length=128)))
    op.add_column("audit_event", sa.Column("actor_system_version", sa.String(length=64)))
    op.execute("UPDATE audit_event SET actor_type = 'user' WHERE actor_user_id IS NOT NULL")
    op.alter_column("audit_event", "actor_type", nullable=False)
    op.create_check_constraint(
        "ck_audit_event_actor_type_state",
        "audit_event",
        "actor_type IN ('user', 'agent', 'policy', 'system')",
    )
    op.create_check_constraint("ck_audit_event_actor_shape", "audit_event", ACTOR_SHAPE_SQL)
    op.create_foreign_key(
        "fk_audit_event_actor_agent_run",
        "audit_event",
        "agent_run",
        ["organization_id", "actor_agent_run_id"],
        ["organization_id", "id"],
    )


def _create_workflow_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_workflow_run_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Workflow runs are durable lineage records';
            END IF;
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
               OR NEW.care_episode_id IS DISTINCT FROM OLD.care_episode_id
               OR NEW.source_submission_id IS DISTINCT FROM OLD.source_submission_id
               OR NEW.reported_need_id IS DISTINCT FROM OLD.reported_need_id
               OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
               OR NEW.initial_state IS DISTINCT FROM OLD.initial_state
               OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                RAISE EXCEPTION 'Workflow run identity and source are immutable';
            END IF;
            IF pg_trigger_depth() < 2
               AND (
                    NEW.current_state IS DISTINCT FROM OLD.current_state
                    OR NEW.updated_at IS DISTINCT FROM OLD.updated_at
               ) THEN
                RAISE EXCEPTION
                    'Materialized state may change only workflow transition events';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workflow_run_lineage_guard
        BEFORE UPDATE OR DELETE ON workflow_run
        FOR EACH ROW
        EXECUTE FUNCTION guard_workflow_run_lineage()
        """
    )
    op.execute(
        """
        CREATE FUNCTION append_workflow_transition_event()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE locked_run workflow_run%ROWTYPE;
        DECLARE prior_sequence integer;
        DECLARE prior_transitioned_at timestamptz;
        BEGIN
            SELECT * INTO locked_run
            FROM workflow_run
            WHERE organization_id = NEW.organization_id
              AND id = NEW.workflow_run_id
            FOR UPDATE;
            IF locked_run.id IS NULL THEN
                RAISE EXCEPTION 'Workflow transition is outside the organization';
            END IF;

            SELECT sequence_number, transitioned_at
            INTO prior_sequence, prior_transitioned_at
            FROM workflow_transition_event
            WHERE organization_id = NEW.organization_id
              AND workflow_run_id = NEW.workflow_run_id
            ORDER BY sequence_number DESC
            LIMIT 1;
            IF NEW.sequence_number IS DISTINCT FROM coalesce(prior_sequence, 0) + 1 THEN
                RAISE EXCEPTION 'Workflow transition requires the next contiguous sequence';
            END IF;
            IF NEW.from_state IS DISTINCT FROM locked_run.current_state THEN
                RAISE EXCEPTION 'Workflow transition from-state must match current state';
            END IF;
            IF NEW.transitioned_at < locked_run.started_at
               OR (
                    prior_transitioned_at IS NOT NULL
                    AND NEW.transitioned_at < prior_transitioned_at
               ) THEN
                RAISE EXCEPTION 'Workflow transition timestamp cannot move backward';
            END IF;

            UPDATE workflow_run
            SET current_state = NEW.to_state,
                updated_at = NEW.transitioned_at
            WHERE id = locked_run.id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workflow_transition_event_append
        BEFORE INSERT ON workflow_transition_event
        FOR EACH ROW
        EXECUTE FUNCTION append_workflow_transition_event()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_manual_review_task()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE run_status agent_run_status;
        BEGIN
            SELECT status INTO run_status
            FROM agent_run
            WHERE organization_id = NEW.organization_id
              AND id = NEW.agent_run_id
              AND workflow_run_id = NEW.workflow_run_id
            FOR UPDATE;
            IF run_status IS NULL OR run_status NOT IN ('failed', 'manual_review') THEN
                RAISE EXCEPTION 'Manual review requires a failed or manual-review AgentRun';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_manual_review_task_guard
        BEFORE INSERT ON manual_review_task
        FOR EACH ROW
        EXECUTE FUNCTION guard_manual_review_task()
        """
    )


def _create_knowledge_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_knowledge_document_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Knowledge documents are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_knowledge_document_immutable
        BEFORE UPDATE OR DELETE ON knowledge_document
        FOR EACH ROW
        EXECUTE FUNCTION guard_knowledge_document_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_knowledge_approval_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Knowledge approval history is immutable';
            END IF;
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.knowledge_document_id IS DISTINCT FROM OLD.knowledge_document_id
               OR NEW.knowledge_document_version IS DISTINCT FROM OLD.knowledge_document_version
               OR NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
               OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
               OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
               OR OLD.withdrawn_at IS NOT NULL
               OR (
                    NEW.withdrawn_at IS NULL
                    AND (
                        NEW.withdrawn_by_user_id IS DISTINCT FROM OLD.withdrawn_by_user_id
                        OR NEW.withdrawal_reason IS DISTINCT FROM OLD.withdrawal_reason
                    )
               ) THEN
                RAISE EXCEPTION 'Knowledge approval history is immutable except withdrawal';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_knowledge_approval_history
        BEFORE UPDATE OR DELETE ON organization_knowledge_approval
        FOR EACH ROW
        EXECUTE FUNCTION guard_knowledge_approval_history()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_agent_run_citation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE approved boolean;
        BEGIN
            SELECT true INTO approved
            FROM organization_knowledge_approval
            WHERE organization_id = NEW.organization_id
              AND knowledge_document_id = NEW.knowledge_document_id
              AND knowledge_document_version = NEW.knowledge_document_version
              AND effective_from <= NEW.cited_at
              AND (withdrawn_at IS NULL OR NEW.cited_at < withdrawn_at)
            FOR KEY SHARE;
            IF NOT coalesce(approved, false) THEN
                RAISE EXCEPTION
                    'Knowledge document version is not approved for citation at this time';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_run_citation_guard
        BEFORE INSERT ON agent_run_citation
        FOR EACH ROW
        EXECUTE FUNCTION guard_agent_run_citation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_agent_run_citation_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'AgentRun citations are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_run_citation_immutable
        BEFORE UPDATE OR DELETE ON agent_run_citation
        FOR EACH ROW
        EXECUTE FUNCTION guard_agent_run_citation_immutable()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_navigation_task_resource()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE proposal proposed_change%ROWTYPE;
        DECLARE proposal_state text;
        DECLARE proposed_resource jsonb;
        DECLARE final_approval_at timestamptz;
        BEGIN
            SELECT * INTO proposal
            FROM proposed_change
            WHERE organization_id = NEW.organization_id
              AND id = NEW.proposed_change_id
              AND navigation_task_id = NEW.navigation_task_id
              AND change_type = 'authorize_navigation_task'
            FOR UPDATE;
            IF proposal.id IS NULL THEN
                RAISE EXCEPTION 'Resource match requires its authorize_navigation_task proposal';
            END IF;

            IF TG_OP = 'INSERT' THEN
                SELECT value INTO proposed_resource
                FROM jsonb_array_elements(proposal.proposed_value->'resources') AS value
                WHERE value->>'resource_id' = NEW.resource_id::text;
                IF proposed_resource IS NULL THEN
                    RAISE EXCEPTION
                        'Resource match is not part of the authorize_navigation_task proposal';
                END IF;
                IF proposed_resource->>'name' IS DISTINCT FROM NEW.resource_name_snapshot
                   OR proposed_resource->>'category' IS DISTINCT FROM
                        NEW.resource_category_snapshot
                   OR proposed_resource->>'url' IS DISTINCT FROM NEW.resource_url_snapshot
                   OR proposed_resource->'metadata' IS DISTINCT FROM
                        NEW.resource_metadata_snapshot
                   OR proposed_resource->>'match_rationale' IS DISTINCT FROM
                        NEW.match_rationale_snapshot
                   OR NEW.proposed_at IS DISTINCT FROM proposal.proposed_at
                   OR NEW.approved_at IS NOT NULL
                   OR NEW.delivered_at IS NOT NULL
                   OR NEW.delivered_by_user_id IS NOT NULL THEN
                    RAISE EXCEPTION 'Resource match snapshots must equal the proposed value';
                END IF;
                SELECT effective_state INTO proposal_state
                FROM effective_proposed_change_state
                WHERE organization_id = proposal.organization_id AND id = proposal.id;
                IF proposal_state IS DISTINCT FROM 'pending' THEN
                    RAISE EXCEPTION
                        'Resource matches must be materialized while proposal is pending';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.navigation_task_id IS DISTINCT FROM OLD.navigation_task_id
               OR NEW.resource_id IS DISTINCT FROM OLD.resource_id
               OR NEW.proposed_change_id IS DISTINCT FROM OLD.proposed_change_id
               OR NEW.resource_name_snapshot IS DISTINCT FROM OLD.resource_name_snapshot
               OR NEW.resource_category_snapshot IS DISTINCT FROM OLD.resource_category_snapshot
               OR NEW.resource_url_snapshot IS DISTINCT FROM OLD.resource_url_snapshot
               OR NEW.resource_metadata_snapshot IS DISTINCT FROM OLD.resource_metadata_snapshot
               OR NEW.match_rationale_snapshot IS DISTINCT FROM OLD.match_rationale_snapshot
               OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at
               OR OLD.delivered_at IS NOT NULL
               OR OLD.delivered_by_user_id IS NOT NULL THEN
                RAISE EXCEPTION 'Proposed resource-match history is immutable';
            END IF;

            IF NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                IF OLD.approved_at IS NOT NULL THEN
                    RAISE EXCEPTION 'Resource-match approval is irreversible';
                END IF;
                SELECT effective_state INTO proposal_state
                FROM effective_proposed_change_state
                WHERE organization_id = proposal.organization_id AND id = proposal.id;
                SELECT max(authorized_at) INTO final_approval_at
                FROM approval_decision
                WHERE organization_id = proposal.organization_id
                  AND proposed_change_id = proposal.id
                  AND decision = 'approved';
                IF proposal_state IS DISTINCT FROM 'approved'
                   OR NEW.approved_at IS DISTINCT FROM final_approval_at THEN
                    RAISE EXCEPTION
                        'Resource-match approval must come from final proposal approval';
                END IF;
            END IF;
            IF NEW.delivered_at IS NOT NULL AND NEW.approved_at IS NULL THEN
                RAISE EXCEPTION 'Resource match cannot be delivered before approval';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_navigation_task_resource_guard
        BEFORE INSERT OR UPDATE ON navigation_task_resource
        FOR EACH ROW
        EXECUTE FUNCTION guard_navigation_task_resource()
        """
    )


def _replace_proposal_guards() -> None:
    op.execute("DROP TRIGGER trg_proposed_change_revision_guard ON proposed_change")
    op.execute(
        """
        CREATE TRIGGER trg_proposed_change_revision_guard
        BEFORE INSERT ON proposed_change
        FOR EACH ROW
        WHEN (NOT (
            NEW.change_type = 'authorize_navigation_task'
            AND NEW.value_schema_id = 'ojcc.authorize-navigation-task'
            AND NEW.value_schema_version = 2
        ))
        EXECUTE FUNCTION guard_proposed_change_revision()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_navigation_task_resource_proposal()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE predecessor proposed_change%ROWTYPE;
        DECLARE predecessor_state text;
        DECLARE selected_policy approval_policy%ROWTYPE;
        DECLARE registered_schema boolean;
        DECLARE invalid_resource boolean;
        BEGIN
            SELECT * INTO selected_policy
            FROM approval_policy
            WHERE organization_id = NEW.organization_id
              AND change_type = NEW.change_type
              AND effective_from <= NEW.proposed_at
              AND (effective_to IS NULL OR NEW.proposed_at < effective_to)
            ORDER BY version DESC
            LIMIT 1
            FOR UPDATE;
            IF selected_policy.id IS NULL
               OR selected_policy.id IS DISTINCT FROM NEW.approval_policy_id
               OR selected_policy.version IS DISTINCT FROM NEW.approval_policy_version
               OR NEW.deterministic_severity_threshold_snapshot IS DISTINCT FROM
                    selected_policy.deterministic_severity_threshold
               OR NEW.allow_self_approval_snapshot IS DISTINCT FROM
                    selected_policy.allow_self_approval
               OR NEW.required_approval_count_snapshot IS DISTINCT FROM
                    selected_policy.required_approval_count
               OR NEW.required_approver_role_snapshot IS DISTINCT FROM
                    selected_policy.required_approver_role THEN
                RAISE EXCEPTION
                    'Proposal must reference the canonical effective policy '
                    'with matching snapshots';
            END IF;

            SELECT true INTO registered_schema
            FROM proposed_value_schema
            WHERE change_type = NEW.change_type
              AND value_schema_id = NEW.value_schema_id
              AND value_schema_version = NEW.value_schema_version
            FOR KEY SHARE;
            IF NOT coalesce(registered_schema, false) THEN
                RAISE EXCEPTION 'Unknown or mismatched proposed-value schema identity';
            END IF;
            IF jsonb_typeof(NEW.proposed_value) IS DISTINCT FROM 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(NEW.proposed_value)) <> 2
               OR jsonb_typeof(NEW.proposed_value->'title') IS DISTINCT FROM 'string'
               OR NULLIF(trim(NEW.proposed_value->>'title'), '') IS NULL
               OR length(NEW.proposed_value->>'title') > 255
               OR jsonb_typeof(NEW.proposed_value->'resources') IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'Proposed value does not match its registered task schema';
            END IF;
            SELECT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(NEW.proposed_value->'resources') AS value
                WHERE jsonb_typeof(value) IS DISTINCT FROM 'object'
                   OR (SELECT count(*) FROM jsonb_object_keys(value)) <> 6
                   OR NULLIF(trim(value->>'resource_id'), '') IS NULL
                   OR NULLIF(trim(value->>'name'), '') IS NULL
                   OR NULLIF(trim(value->>'category'), '') IS NULL
                   OR NOT (value ? 'url')
                   OR jsonb_typeof(value->'metadata') IS DISTINCT FROM 'object'
                   OR NULLIF(trim(value->>'match_rationale'), '') IS NULL
            ) INTO invalid_resource;
            IF invalid_resource THEN
                RAISE EXCEPTION 'Proposed value does not match its registered resource schema';
            END IF;
            BEGIN
                PERFORM (value->>'resource_id')::uuid
                FROM jsonb_array_elements(NEW.proposed_value->'resources') AS value;
            EXCEPTION WHEN invalid_text_representation THEN
                RAISE EXCEPTION 'Proposed value does not match its registered resource schema';
            END;

            IF NEW.supersedes_proposed_change_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT * INTO predecessor
            FROM proposed_change
            WHERE organization_id = NEW.organization_id
              AND id = NEW.supersedes_proposed_change_id
            FOR UPDATE;
            IF predecessor.id IS NULL THEN
                RAISE EXCEPTION 'Proposal predecessor is outside the organization';
            END IF;
            SELECT effective_state INTO predecessor_state
            FROM effective_proposed_change_state
            WHERE id = predecessor.id;
            IF predecessor_state NOT IN ('pending', 'declined') THEN
                RAISE EXCEPTION 'Only a pending or declined current proposal can be revised';
            END IF;
            IF NEW.change_type IS DISTINCT FROM predecessor.change_type
               OR NEW.navigation_task_id IS DISTINCT FROM predecessor.navigation_task_id THEN
                RAISE EXCEPTION
                    'Proposal revision must preserve organization, target, and change type';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_proposed_change_revision_guard_v2
        BEFORE INSERT ON proposed_change
        FOR EACH ROW
        WHEN (
            NEW.change_type = 'authorize_navigation_task'
            AND NEW.value_schema_id = 'ojcc.authorize-navigation-task'
            AND NEW.value_schema_version = 2
        )
        EXECUTE FUNCTION guard_navigation_task_resource_proposal()
        """
    )
    op.execute(
        """
        CREATE FUNCTION apply_navigation_resource_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE proposal proposed_change%ROWTYPE;
        DECLARE current_state text;
        BEGIN
            IF NEW.decision <> 'approved' THEN
                RETURN NEW;
            END IF;
            SELECT * INTO proposal
            FROM proposed_change
            WHERE organization_id = NEW.organization_id
              AND id = NEW.proposed_change_id
            FOR UPDATE;
            IF proposal.change_type <> 'authorize_navigation_task' THEN
                RETURN NEW;
            END IF;
            SELECT effective_state INTO current_state
            FROM effective_proposed_change_state
            WHERE organization_id = proposal.organization_id AND id = proposal.id;
            IF current_state = 'approved' THEN
                IF proposal.value_schema_version = 2
                   AND jsonb_array_length(proposal.proposed_value->'resources') <>
                       (
                           SELECT count(*)
                           FROM navigation_task_resource
                           WHERE organization_id = proposal.organization_id
                             AND proposed_change_id = proposal.id
                       ) THEN
                    RAISE EXCEPTION
                        'Every proposed resource match must be materialized before approval';
                END IF;
                UPDATE navigation_task_resource
                SET approved_at = NEW.authorized_at
                WHERE organization_id = proposal.organization_id
                  AND proposed_change_id = proposal.id
                  AND approved_at IS NULL;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_decision_apply_navigation_resource
        AFTER INSERT ON approval_decision
        FOR EACH ROW
        EXECUTE FUNCTION apply_navigation_resource_approval()
        """
    )


def _replace_closure_audit_function() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION close_reported_need_from_outcome()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE task_record record;
        DECLARE locked_need_id uuid;
        BEGIN
            SELECT need.id INTO locked_need_id
            FROM reported_need AS need
            WHERE need.organization_id = NEW.organization_id
              AND need.patient_id = NEW.patient_id
              AND need.id = NEW.reported_need_id
            FOR UPDATE;

            IF locked_need_id IS NULL THEN
                RAISE EXCEPTION
                    'Outcome reported need % is outside the tenant or patient',
                    NEW.reported_need_id;
            END IF;

            FOR task_record IN
                SELECT task.id
                FROM navigation_task AS task
                WHERE task.organization_id = NEW.organization_id
                  AND task.patient_id = NEW.patient_id
                  AND task.reported_need_id = NEW.reported_need_id
                  AND task.status IN ('open', 'assigned', 'in_progress')
                ORDER BY task.id
                FOR UPDATE
            LOOP
                UPDATE navigation_task
                SET status = 'cancelled',
                    cancelled_by_user_id = NEW.recorded_by_user_id,
                    cancelled_at = NEW.recorded_at,
                    cancellation_reason = 'need_closed'
                WHERE id = task_record.id;

                INSERT INTO audit_event
                    (id, organization_id, actor_type, actor_user_id, entity_type, entity_id,
                     event_type, payload, created_at)
                VALUES
                    (md5(NEW.id::text || task_record.id::text ||
                         'task_cancelled_by_closure')::uuid,
                     NEW.organization_id,
                     'user',
                     NEW.recorded_by_user_id,
                     'navigation_task',
                     task_record.id,
                     'task_cancelled_by_closure',
                     jsonb_build_object(
                         'outcome_id', NEW.id::text,
                         'cancellation_reason', 'need_closed'
                     ),
                     NEW.recorded_at);
            END LOOP;
            RETURN NEW;
        END;
        $$
        """
    )


def _create_append_only_backstops() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_append_only_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only; use a successor record', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table_name in (
        "check_in_submission",
        "proposed_change",
        "approval_decision",
        "outcome",
        "safety_signal_resolution",
        "audit_event",
        "workflow_transition_event",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION reject_append_only_mutation()
            """
        )


def _create_application_role_surface() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ojcc_app') THEN
                CREATE ROLE ojcc_app NOLOGIN;
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "ALTER ROLE ojcc_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOREPLICATION NOBYPASSRLS"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO ojcc_app")
    for table_name in (
        "check_in_submission",
        "proposed_change",
        "approval_decision",
        "outcome",
        "safety_signal_resolution",
        "audit_event",
        "workflow_transition_event",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {table_name} FROM ojcc_app")
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table_name} TO ojcc_app")


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading 0005 would discard workflow, knowledge, resource, and audit lineage; "
        "reset the synthetic demo database instead."
    )
