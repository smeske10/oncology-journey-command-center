"""Reconcile safety signals and risk-based approval authorization."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision = "0004_safety_approval_lifecycle"
down_revision = "0003_need_task_outcome_lifecycle"
branch_labels = None
depends_on = None


SIGNAL_RULE_KIND = postgresql.ENUM(
    "deterministic",
    "human_escalation",
    name="signal_rule_kind",
    create_type=False,
)
APPROVAL_CHANGE_TYPE = postgresql.ENUM(
    "dismiss_signal",
    "override_signal_severity",
    "authorize_navigation_task",
    "authorize_patient_message",
    name="approval_change_type",
    create_type=False,
)
APPROVAL_DECISION_VALUE = postgresql.ENUM(
    "approved",
    "declined",
    name="approval_decision_value",
    create_type=False,
)
SAFETY_SEVERITY = postgresql.ENUM(
    "routine",
    "urgent",
    "emergent",
    name="safety_severity",
    create_type=False,
)
USER_ROLE = postgresql.ENUM(
    "administrator",
    "navigator",
    "supporting_actor",
    name="user_role",
    create_type=False,
)


def upgrade() -> None:
    _reject_ambiguous_legacy_rows()

    bind = op.get_bind()
    SIGNAL_RULE_KIND.create(bind, checkfirst=False)
    APPROVAL_CHANGE_TYPE.create(bind, checkfirst=False)
    APPROVAL_DECISION_VALUE.create(bind, checkfirst=False)

    _extend_role_assignment_keys()
    _create_signal_rule()
    _reconcile_safety_signal()
    _replace_legacy_approval_decision()
    _create_approval_tables()
    _add_signal_proposal_links()
    _create_views_and_triggers()


def _reject_ambiguous_legacy_rows() -> None:
    op.execute(
        """
        DO $$
        DECLARE ambiguous_id uuid;
        BEGIN
            SELECT id INTO ambiguous_id
            FROM approval_decision
            LIMIT 1;
            IF ambiguous_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'Cannot migrate legacy approval decision % without '
                    'proposal, policy, or qualifying-role provenance; reset the synthetic '
                    'demo database and rerun '
                    'migrations.',
                    ambiguous_id;
            END IF;

            SELECT id INTO ambiguous_id
            FROM safety_signal
            WHERE status <> 'active' OR resolved_at IS NOT NULL
            LIMIT 1;
            IF ambiguous_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'Cannot migrate legacy terminal safety signal % without '
                    'resolver or acknowledgement provenance; reset the synthetic demo '
                    'database and rerun '
                    'migrations.',
                    ambiguous_id;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE ambiguous_id uuid;
        BEGIN
            SELECT signal.id INTO ambiguous_id
            FROM safety_signal AS signal
            LEFT JOIN check_in_submission AS submission
              ON submission.organization_id = signal.organization_id
             AND submission.patient_id = signal.patient_id
             AND submission.id = signal.source_submission_id
            WHERE submission.id IS NULL
               OR submission.care_episode_id IS NULL
               OR NULLIF(trim(signal.rule_code), '') IS NULL
               OR signal.severity IS NULL
            LIMIT 1;
            IF ambiguous_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'Cannot migrate safety signal % '
                    'without derivable tenant-, patient-, episode-, severity-, and rule provenance'
                    '; reset the synthetic demo '
                    'database and rerun migrations.',
                    ambiguous_id;
            END IF;
        END $$;
        """
    )


def _extend_role_assignment_keys() -> None:
    op.create_unique_constraint(
        "uq_role_assignment_organization_id_id",
        "role_assignment",
        ["organization_id", "id"],
    )
    op.create_unique_constraint(
        "uq_role_assignment_organization_user_id",
        "role_assignment",
        ["organization_id", "user_id", "id"],
    )


def _create_signal_rule() -> None:
    op.create_table(
        "signal_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("rule_code", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rule_kind", SIGNAL_RULE_KIND, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_rule"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_signal_rule_organization_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "version",
            name="uq_signal_rule_organization_id_version",
        ),
        sa.CheckConstraint(
            "rule_kind IN ('deterministic', 'human_escalation')",
            name="ck_signal_rule_rule_kind_state",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_signal_rule_organization_id_organization",
        ),
    )
    op.create_index(
        "ix_signal_rule_org_code_version",
        "signal_rule",
        ["organization_id", "rule_code", "version"],
        unique=True,
    )
    op.execute(
        """
        INSERT INTO signal_rule
            (id, organization_id, rule_code, version, rule_kind, name)
        SELECT
            md5(organization_id::text || ':' || rule_code || ':1:deterministic')::uuid,
            organization_id,
            rule_code,
            1,
            'deterministic',
            rule_code
        FROM safety_signal
        GROUP BY organization_id, rule_code
        """
    )


def _reconcile_safety_signal() -> None:
    op.drop_constraint(
        "fk_safety_signal_organization_submission",
        "safety_signal",
        type_="foreignkey",
    )
    op.drop_constraint("ck_safety_signal_status_state", "safety_signal", type_="check")
    op.drop_constraint("ck_safety_signal_severity_state", "safety_signal", type_="check")
    op.drop_index("ix_safety_signal_org_patient_status_severity", table_name="safety_signal")

    op.add_column("safety_signal", sa.Column("care_episode_id", sa.Uuid()))
    op.add_column("safety_signal", sa.Column("escalated_from_signal_id", sa.Uuid()))
    op.add_column("safety_signal", sa.Column("signal_rule_id", sa.Uuid()))
    op.add_column("safety_signal", sa.Column("signal_rule_version", sa.Integer()))
    op.add_column("safety_signal", sa.Column("deterministic_level", SAFETY_SEVERITY))
    op.add_column("safety_signal", sa.Column("effective_level", SAFETY_SEVERITY))
    op.add_column("safety_signal", sa.Column("acknowledged_by_user_id", sa.Uuid()))
    op.add_column(
        "safety_signal",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        """
        UPDATE safety_signal AS signal
        SET care_episode_id = submission.care_episode_id,
            signal_rule_id = md5(
                signal.organization_id::text || ':' || signal.rule_code || ':1:deterministic'
            )::uuid,
            signal_rule_version = 1,
            deterministic_level = signal.severity,
            effective_level = signal.severity
        FROM check_in_submission AS submission
        WHERE submission.organization_id = signal.organization_id
          AND submission.patient_id = signal.patient_id
          AND submission.id = signal.source_submission_id
        """
    )
    if not context.is_offline_mode():
        op.execute(
            """
            DO $$
            DECLARE ambiguous_id uuid;
            BEGIN
                SELECT id INTO ambiguous_id
                FROM safety_signal
                WHERE care_episode_id IS NULL
                   OR signal_rule_id IS NULL
                   OR deterministic_level IS NULL
                   OR effective_level IS NULL
                LIMIT 1;
                IF ambiguous_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot migrate safety signal % without derivable tenant-, patient-, '
                        'episode-, severity-, and rule provenance; reset the synthetic demo '
                        'database and rerun migrations.',
                        ambiguous_id;
                END IF;
            END $$;
            """
        )

    op.execute("ALTER TABLE safety_signal ALTER COLUMN status TYPE text USING status::text")
    op.execute("UPDATE safety_signal SET status = 'open' WHERE status = 'active'")
    op.execute("DROP TYPE safety_signal_status")
    op.execute("CREATE TYPE safety_signal_status AS ENUM ('open', 'acknowledged')")
    op.execute(
        "ALTER TABLE safety_signal ALTER COLUMN status TYPE safety_signal_status "
        "USING status::safety_signal_status"
    )
    op.alter_column("safety_signal", "source_submission_id", nullable=True)
    for column in (
        "care_episode_id",
        "signal_rule_id",
        "signal_rule_version",
        "deterministic_level",
        "effective_level",
    ):
        op.alter_column("safety_signal", column, nullable=False)
    op.drop_column("safety_signal", "rule_code")
    op.drop_column("safety_signal", "severity")
    op.drop_column("safety_signal", "resolved_at")

    op.create_check_constraint(
        "ck_safety_signal_status_state",
        "safety_signal",
        "status IN ('open', 'acknowledged')",
    )
    op.create_check_constraint(
        "ck_safety_signal_deterministic_level_state",
        "safety_signal",
        "deterministic_level IN ('routine', 'urgent', 'emergent')",
    )
    op.create_check_constraint(
        "ck_safety_signal_effective_level_state",
        "safety_signal",
        "effective_level IN ('routine', 'urgent', 'emergent')",
    )
    op.create_check_constraint(
        "ck_safety_signal_origin",
        "safety_signal",
        "num_nonnulls(source_submission_id, escalated_from_signal_id) = 1",
    )
    op.create_check_constraint(
        "ck_safety_signal_escalation_not_self",
        "safety_signal",
        "escalated_from_signal_id IS NULL OR escalated_from_signal_id <> id",
    )
    op.create_check_constraint(
        "ck_safety_signal_acknowledgement_shape",
        "safety_signal",
        "(status = 'open' AND acknowledged_by_user_id IS NULL "
        "AND acknowledged_at IS NULL) OR "
        "(status = 'acknowledged' AND acknowledged_by_user_id IS NOT NULL "
        "AND acknowledged_at IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_safety_signal_org_patient_episode_id",
        "safety_signal",
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )
    op.create_unique_constraint(
        "uq_safety_signal_escalated_from_signal_id",
        "safety_signal",
        ["escalated_from_signal_id"],
    )
    op.create_foreign_key(
        "fk_safety_signal_origin_submission",
        "safety_signal",
        "check_in_submission",
        ["organization_id", "patient_id", "care_episode_id", "source_submission_id"],
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )
    op.create_foreign_key(
        "fk_safety_signal_escalated_predecessor",
        "safety_signal",
        "safety_signal",
        ["organization_id", "patient_id", "care_episode_id", "escalated_from_signal_id"],
        ["organization_id", "patient_id", "care_episode_id", "id"],
    )
    op.create_foreign_key(
        "fk_safety_signal_versioned_rule",
        "safety_signal",
        "signal_rule",
        ["organization_id", "signal_rule_id", "signal_rule_version"],
        ["organization_id", "id", "version"],
    )
    op.create_foreign_key(
        "fk_safety_signal_acknowledged_by_user_id_user_account",
        "safety_signal",
        "user_account",
        ["acknowledged_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_safety_signal_org_patient_status_effective",
        "safety_signal",
        ["organization_id", "patient_id", "status", "effective_level"],
    )


def _replace_legacy_approval_decision() -> None:
    op.drop_index("ix_approval_decision_org_task_created", table_name="approval_decision")
    op.drop_table("approval_decision")
    op.execute("DROP TYPE approval_status")


def _create_approval_tables() -> None:
    op.create_table(
        "patient_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_patient_message"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_patient_message_organization_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_patient_message_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_id"],
            ["synthetic_patient.organization_id", "synthetic_patient.id"],
            name="fk_patient_message_organization_patient",
        ),
    )
    op.create_index(
        "ix_patient_message_org_patient_created",
        "patient_message",
        ["organization_id", "patient_id", "created_at"],
    )
    op.create_table(
        "approval_policy",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("change_type", APPROVAL_CHANGE_TYPE, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("deterministic_severity_threshold", SAFETY_SEVERITY),
        sa.Column("allow_self_approval", sa.Boolean(), nullable=False),
        sa.Column("required_approval_count", sa.Integer(), nullable=False),
        sa.Column("required_approver_role", USER_ROLE, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_policy"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_approval_policy_organization_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "version",
            name="uq_approval_policy_organization_id_version",
        ),
        sa.CheckConstraint(
            "change_type IN ('dismiss_signal', 'override_signal_severity', "
            "'authorize_navigation_task', 'authorize_patient_message')",
            name="ck_approval_policy_change_type_state",
        ),
        sa.CheckConstraint(
            "required_approver_role IN "
            "('administrator', 'navigator', 'supporting_actor')",
            name="ck_approval_policy_required_approver_role_state",
        ),
        sa.CheckConstraint(
            "deterministic_severity_threshold IN ('routine', 'urgent', 'emergent')",
            name="ck_approval_policy_deterministic_severity_threshold_state",
        ),
        sa.CheckConstraint(
            "(change_type = 'dismiss_signal' "
            "AND deterministic_severity_threshold IS NOT NULL) OR "
            "(change_type <> 'dismiss_signal' "
            "AND deterministic_severity_threshold IS NULL)",
            name="ck_approval_policy_dismissal_threshold_shape",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from < effective_to",
            name="ck_approval_policy_effective_interval",
        ),
        sa.CheckConstraint(
            "required_approval_count >= 1",
            name="ck_approval_policy_required_approval_count",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_approval_policy_organization_id_organization",
        ),
        postgresql.ExcludeConstraint(
            ("organization_id", "="),
            ("change_type", "="),
            (sa.text("tstzrange(effective_from, effective_to, '[)')"), "&&"),
            name="ex_approval_policy_no_overlap",
            using="gist",
            deferrable=True,
            initially="IMMEDIATE",
        ),
    )
    op.create_index(
        "ix_approval_policy_org_change_version",
        "approval_policy",
        ["organization_id", "change_type", "version"],
        unique=True,
    )
    _create_value_schema_table()
    _create_proposed_change_table()
    _create_approval_decision_table()
    _create_resolution_table()


def _create_value_schema_table() -> None:
    op.create_table(
        "proposed_value_schema",
        sa.Column("change_type", APPROVAL_CHANGE_TYPE, nullable=False),
        sa.Column("value_schema_id", sa.String(length=255), nullable=False),
        sa.Column("value_schema_version", sa.Integer(), nullable=False),
        sa.Column("schema_document", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "change_type",
            "value_schema_id",
            "value_schema_version",
            name="pk_proposed_value_schema",
        ),
        sa.CheckConstraint(
            "change_type IN ('dismiss_signal', 'override_signal_severity', "
            "'authorize_navigation_task', 'authorize_patient_message')",
            name=op.f("ck_proposed_value_schema_ck_proposed_value_schema_chang_7b44"),
        ),
        sa.CheckConstraint(
            "value_schema_version >= 1",
            name=op.f("ck_proposed_value_schema_ck_proposed_value_schema_value_5948"),
        ),
    )
    op.execute(
        r"""
        INSERT INTO proposed_value_schema
            (change_type, value_schema_id, value_schema_version, schema_document)
        VALUES
            (
                'dismiss_signal', 'ojcc.dismiss-signal', 1,
                '{"type":"object","additionalProperties"\:false,'
                '"required":["category"],"properties":{"category":{"type":"string",'
                '"enum":["false_positive","duplicate","not_applicable"]}}}'::jsonb
            ),
            (
                'override_signal_severity', 'ojcc.override-signal-severity', 1,
                '{"type":"object","additionalProperties"\:false,'
                '"required":["level"],"properties":{"level":{"type":"string",'
                '"enum":["routine","urgent","emergent"]}}}'::jsonb
            ),
            (
                'authorize_navigation_task', 'ojcc.authorize-navigation-task', 1,
                '{"type":"object","additionalProperties"\:false,'
                '"required":["title"],"properties":{"title":{"type":"string",'
                '"minLength"\:1,"maxLength"\:255}}}'::jsonb
            ),
            (
                'authorize_patient_message', 'ojcc.authorize-patient-message', 1,
                '{"type":"object","additionalProperties"\:false,'
                '"required":["body"],"properties":{"body":{"type":"string",'
                '"minLength"\:1}}}'::jsonb
            )
        """
    )


def _create_proposed_change_table() -> None:
    op.create_table(
        "proposed_change",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid()),
        sa.Column("proposed_by_agent_run_id", sa.Uuid()),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change_type", APPROVAL_CHANGE_TYPE, nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("value_schema_id", sa.String(length=255), nullable=False),
        sa.Column("value_schema_version", sa.Integer(), nullable=False),
        sa.Column("supersedes_proposed_change_id", sa.Uuid()),
        sa.Column("safety_signal_id", sa.Uuid()),
        sa.Column("navigation_task_id", sa.Uuid()),
        sa.Column("patient_message_id", sa.Uuid()),
        sa.Column("approval_policy_id", sa.Uuid(), nullable=False),
        sa.Column("approval_policy_version", sa.Integer(), nullable=False),
        sa.Column("deterministic_severity_threshold_snapshot", SAFETY_SEVERITY),
        sa.Column("allow_self_approval_snapshot", sa.Boolean(), nullable=False),
        sa.Column("required_approval_count_snapshot", sa.Integer(), nullable=False),
        sa.Column("required_approver_role_snapshot", USER_ROLE, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_proposed_change"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_proposed_change_organization_id_id",
        ),
        sa.UniqueConstraint(
            "supersedes_proposed_change_id",
            name="uq_proposed_change_supersedes_proposed_change_id",
        ),
        sa.UniqueConstraint(
            "id",
            "safety_signal_id",
            "organization_id",
            "change_type",
            name="uq_proposed_change_signal_authorization_unit",
        ),
        sa.CheckConstraint(
            "num_nonnulls(proposed_by_user_id, proposed_by_agent_run_id) = 1",
            name="ck_proposed_change_proposer",
        ),
        sa.CheckConstraint(
            "num_nonnulls(safety_signal_id, navigation_task_id, patient_message_id) = 1",
            name="ck_proposed_change_target_count",
        ),
        sa.CheckConstraint(
            "CASE change_type "
            "WHEN 'dismiss_signal' THEN safety_signal_id IS NOT NULL "
            "WHEN 'override_signal_severity' THEN safety_signal_id IS NOT NULL "
            "WHEN 'authorize_navigation_task' THEN navigation_task_id IS NOT NULL "
            "WHEN 'authorize_patient_message' THEN patient_message_id IS NOT NULL "
            "ELSE false END",
            name="ck_proposed_change_target_type",
        ),
        sa.CheckConstraint(
            "change_type IN ('dismiss_signal', 'override_signal_severity', "
            "'authorize_navigation_task', 'authorize_patient_message')",
            name="ck_proposed_change_change_type_state",
        ),
        sa.CheckConstraint(
            "required_approver_role_snapshot IN "
            "('administrator', 'navigator', 'supporting_actor')",
            name="ck_proposed_change_required_approver_role_snapshot_state",
        ),
        sa.CheckConstraint(
            "deterministic_severity_threshold_snapshot IN "
            "('routine', 'urgent', 'emergent')",
            name="ck_proposed_change_deterministic_severity_threshold_snapshot_state",
        ),
        sa.CheckConstraint(
            "required_approval_count_snapshot >= 1",
            name="ck_proposed_change_required_approval_count_snapshot",
        ),
        sa.CheckConstraint(
            "value_schema_version >= 1",
            name=op.f("ck_proposed_change_ck_proposed_change_value_schema_vers_e771"),
        ),
        sa.CheckConstraint(
            "(change_type = 'dismiss_signal' "
            "AND deterministic_severity_threshold_snapshot IS NOT NULL) OR "
            "(change_type <> 'dismiss_signal' "
            "AND deterministic_severity_threshold_snapshot IS NULL)",
            name=op.f("ck_proposed_change_ck_proposed_change_dismissal_thresho_b719"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_proposed_change_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_by_user_id"],
            ["user_account.id"],
            name="fk_proposed_change_proposed_by_user_id_user_account",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposed_by_agent_run_id"],
            ["agent_run.organization_id", "agent_run.id"],
            name="fk_proposed_change_agent_proposer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "safety_signal_id"],
            ["safety_signal.organization_id", "safety_signal.id"],
            name="fk_proposed_change_safety_signal",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "navigation_task_id"],
            ["navigation_task.organization_id", "navigation_task.id"],
            name="fk_proposed_change_navigation_task",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "patient_message_id"],
            ["patient_message.organization_id", "patient_message.id"],
            name="fk_proposed_change_patient_message",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "supersedes_proposed_change_id"],
            ["proposed_change.organization_id", "proposed_change.id"],
            name="fk_proposed_change_predecessor",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "approval_policy_id", "approval_policy_version"],
            ["approval_policy.organization_id", "approval_policy.id", "approval_policy.version"],
            name="fk_proposed_change_policy_version",
        ),
        sa.ForeignKeyConstraint(
            ["change_type", "value_schema_id", "value_schema_version"],
            [
                "proposed_value_schema.change_type",
                "proposed_value_schema.value_schema_id",
                "proposed_value_schema.value_schema_version",
            ],
            name="fk_proposed_change_value_schema",
        ),
    )
    op.create_index(
        "ix_proposed_change_org_created",
        "proposed_change",
        ["organization_id", "proposed_at"],
    )


def _create_approval_decision_table() -> None:
    op.create_table(
        "approval_decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_change_id", sa.Uuid(), nullable=False),
        sa.Column("authorized_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("qualifying_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("qualifying_role_snapshot", USER_ROLE, nullable=False),
        sa.Column("decision", APPROVAL_DECISION_VALUE, nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decision"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_approval_decision_organization_id_id",
        ),
        sa.UniqueConstraint(
            "proposed_change_id",
            "authorized_by_user_id",
            name="uq_approval_decision_proposal_authorizer",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'declined')",
            name="ck_approval_decision_decision_state",
        ),
        sa.CheckConstraint(
            "qualifying_role_snapshot IN "
            "('administrator', 'navigator', 'supporting_actor')",
            name="ck_approval_decision_qualifying_role_snapshot_state",
        ),
        sa.CheckConstraint(
            "(decision = 'declined' AND NULLIF(trim(reason), '') IS NOT NULL) "
            "OR decision = 'approved'",
            name="ck_approval_decision_decline_reason",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_approval_decision_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["authorized_by_user_id"],
            ["user_account.id"],
            name="fk_approval_decision_authorized_by_user_id_user_account",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposed_change_id"],
            ["proposed_change.organization_id", "proposed_change.id"],
            name="fk_approval_decision_proposed_change",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "authorized_by_user_id", "qualifying_role_assignment_id"],
            ["role_assignment.organization_id", "role_assignment.user_id", "role_assignment.id"],
            name="fk_approval_decision_qualifying_role",
        ),
    )
    op.create_index(
        "ix_approval_decision_org_proposal_authorized",
        "approval_decision",
        ["organization_id", "proposed_change_id", "authorized_at"],
    )


def _create_resolution_table() -> None:
    op.create_table(
        "safety_signal_resolution",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("safety_signal_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_safety_signal_resolution"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name="uq_safety_signal_resolution_organization_id_id",
        ),
        sa.UniqueConstraint(
            "safety_signal_id",
            name="uq_safety_signal_resolution_safety_signal_id",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_safety_signal_resolution_organization_id_organization",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "safety_signal_id"],
            ["safety_signal.organization_id", "safety_signal.id"],
            name="fk_safety_signal_resolution_signal",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["user_account.id"],
            name="fk_safety_signal_resolution_resolved_by_user_id_user_account",
        ),
    )
    op.create_index(
        "ix_safety_signal_resolution_org_resolved",
        "safety_signal_resolution",
        ["organization_id", "resolved_at"],
    )


def _add_signal_proposal_links() -> None:
    op.add_column(
        "safety_signal",
        sa.Column("dismissal_proposed_change_id", sa.Uuid()),
    )
    op.add_column(
        "safety_signal",
        sa.Column(
            "dismissal_change_type",
            APPROVAL_CHANGE_TYPE,
            server_default="dismiss_signal",
            nullable=False,
        ),
    )
    op.add_column(
        "safety_signal",
        sa.Column("current_severity_override_proposed_change_id", sa.Uuid()),
    )
    op.add_column(
        "safety_signal",
        sa.Column(
            "current_severity_override_change_type",
            APPROVAL_CHANGE_TYPE,
            server_default="override_signal_severity",
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_safety_signal_dismissal_proposed_change_id",
        "safety_signal",
        ["dismissal_proposed_change_id"],
    )
    op.create_unique_constraint(
        "uq_safety_signal_current_severity_override_proposed_change_id",
        "safety_signal",
        ["current_severity_override_proposed_change_id"],
    )
    op.create_check_constraint(
        "ck_safety_signal_dismissal_change_type",
        "safety_signal",
        "dismissal_change_type = 'dismiss_signal'",
    )
    op.create_check_constraint(
        "ck_safety_signal_override_change_type",
        "safety_signal",
        "current_severity_override_change_type = 'override_signal_severity'",
    )
    op.create_foreign_key(
        "fk_safety_signal_dismissal_proposal",
        "safety_signal",
        "proposed_change",
        ["dismissal_proposed_change_id", "id", "organization_id", "dismissal_change_type"],
        ["id", "safety_signal_id", "organization_id", "change_type"],
    )
    op.create_foreign_key(
        "fk_safety_signal_current_override_proposal",
        "safety_signal",
        "proposed_change",
        [
            "current_severity_override_proposed_change_id",
            "id",
            "organization_id",
            "current_severity_override_change_type",
        ],
        ["id", "safety_signal_id", "organization_id", "change_type"],
    )


def _create_views_and_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION safety_severity_rank(value safety_severity)
        RETURNS integer
        LANGUAGE sql
        IMMUTABLE
        STRICT
        AS $$
            SELECT CASE value
                WHEN 'routine' THEN 0
                WHEN 'urgent' THEN 1
                WHEN 'emergent' THEN 2
            END
        $$
        """
    )
    _create_effective_proposal_view()
    _create_effective_signal_view()
    _create_immutable_authorization_guards()
    _create_signal_guard()
    _create_resolution_guard()
    _create_revision_guard()
    _create_decision_guards()


def _create_immutable_authorization_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_signal_rule_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Signal rule versions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_signal_rule_immutable
        BEFORE UPDATE OR DELETE ON signal_rule
        FOR EACH ROW
        EXECUTE FUNCTION reject_signal_rule_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_approval_policy_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Approval policy versions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_policy_immutable
        BEFORE UPDATE OR DELETE ON approval_policy
        FOR EACH ROW
        EXECUTE FUNCTION reject_approval_policy_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_proposed_value_schema_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Proposed value schema versions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_proposed_value_schema_immutable
        BEFORE UPDATE OR DELETE ON proposed_value_schema
        FOR EACH ROW
        EXECUTE FUNCTION reject_proposed_value_schema_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_role_assignment_approval_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM approval_decision AS decision
                JOIN proposed_change AS proposal
                  ON proposal.organization_id = decision.organization_id
                 AND proposal.id = decision.proposed_change_id
                LEFT JOIN safety_signal AS signal
                  ON signal.organization_id = proposal.organization_id
                 AND signal.id = proposal.safety_signal_id
                 AND (
                    signal.dismissal_proposed_change_id = proposal.id
                    OR signal.current_severity_override_proposed_change_id = proposal.id
                 )
                JOIN effective_proposed_change_state AS proposal_state
                  ON proposal_state.organization_id = proposal.organization_id
                 AND proposal_state.id = proposal.id
                WHERE decision.organization_id = OLD.organization_id
                  AND decision.authorized_by_user_id = OLD.user_id
                  AND decision.qualifying_role_assignment_id = OLD.id
                  AND (
                    proposal_state.effective_state = 'approved'
                    OR signal.id IS NOT NULL
                  )
                  AND (
                    TG_OP = 'DELETE'
                    OR NEW.organization_id IS DISTINCT FROM decision.organization_id
                    OR NEW.user_id IS DISTINCT FROM decision.authorized_by_user_id
                    OR NEW.id IS DISTINCT FROM decision.qualifying_role_assignment_id
                    OR NEW.role IS DISTINCT FROM decision.qualifying_role_snapshot
                    OR NEW.role IS DISTINCT FROM proposal.required_approver_role_snapshot
                    OR decision.authorized_at < NEW.granted_at
                    OR (
                        NEW.revoked_at IS NOT NULL
                        AND decision.authorized_at >= NEW.revoked_at
                    )
                  )
            ) THEN
                RAISE EXCEPTION
                    'Role assignment mutation would invalidate approval history';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_role_assignment_approval_history_guard
        BEFORE UPDATE OR DELETE ON role_assignment
        FOR EACH ROW
        EXECUTE FUNCTION guard_role_assignment_approval_history()
        """
    )


def _create_effective_proposal_view() -> None:
    op.execute(
        """
        CREATE VIEW effective_proposed_change_state AS
        WITH facts AS (
            SELECT
                proposal.id,
                bool_or(decision.decision = 'declined') FILTER (
                    WHERE assignment.id IS NOT NULL
                ) AS has_decline,
                count(DISTINCT decision.authorized_by_user_id) FILTER (
                    WHERE decision.decision = 'approved'
                      AND assignment.id IS NOT NULL
                      AND (
                        proposal.proposed_by_user_id IS NULL
                        OR decision.authorized_by_user_id <> proposal.proposed_by_user_id
                        OR (
                            proposal.allow_self_approval_snapshot
                            AND NOT (
                                proposal.change_type = 'dismiss_signal'
                                AND (
                                    proposal.deterministic_severity_threshold_snapshot IS NULL
                                    OR safety_severity_rank(signal.deterministic_level) >=
                                        safety_severity_rank(
                                            proposal.deterministic_severity_threshold_snapshot
                                        )
                                )
                            )
                        )
                      )
                ) AS approval_count,
                CASE
                    WHEN proposal.change_type = 'dismiss_signal'
                     AND (
                        proposal.deterministic_severity_threshold_snapshot IS NULL
                        OR safety_severity_rank(signal.deterministic_level) >=
                            safety_severity_rank(
                                proposal.deterministic_severity_threshold_snapshot
                            )
                     )
                    THEN greatest(proposal.required_approval_count_snapshot, 2)
                    ELSE proposal.required_approval_count_snapshot
                END AS required_count
            FROM proposed_change AS proposal
            LEFT JOIN approval_decision AS decision
              ON decision.organization_id = proposal.organization_id
             AND decision.proposed_change_id = proposal.id
            LEFT JOIN role_assignment AS assignment
              ON assignment.organization_id = decision.organization_id
             AND assignment.user_id = decision.authorized_by_user_id
             AND assignment.id = decision.qualifying_role_assignment_id
             AND assignment.role = proposal.required_approver_role_snapshot
             AND decision.qualifying_role_snapshot = proposal.required_approver_role_snapshot
             AND decision.authorized_at >= assignment.granted_at
             AND (
                assignment.revoked_at IS NULL
                OR decision.authorized_at < assignment.revoked_at
             )
            LEFT JOIN safety_signal AS signal
              ON signal.organization_id = proposal.organization_id
             AND signal.id = proposal.safety_signal_id
            GROUP BY proposal.id, signal.deterministic_level
        )
        SELECT
            proposal.*,
            CASE
                WHEN successor.id IS NOT NULL THEN 'superseded'
                WHEN coalesce(facts.has_decline, false) THEN 'declined'
                WHEN facts.approval_count >= facts.required_count THEN 'approved'
                ELSE 'pending'
            END AS effective_state
        FROM proposed_change AS proposal
        JOIN facts ON facts.id = proposal.id
        LEFT JOIN proposed_change AS successor
          ON successor.organization_id = proposal.organization_id
         AND successor.supersedes_proposed_change_id = proposal.id
        """
    )


def _create_effective_signal_view() -> None:
    op.execute(
        """
        CREATE VIEW effective_safety_signal_state AS
        SELECT
            signal.*,
            CASE
                WHEN dismissal.id IS NOT NULL THEN 'dismissed'
                WHEN resolution.id IS NOT NULL THEN 'resolved'
                WHEN signal.acknowledged_at IS NOT NULL THEN 'acknowledged'
                ELSE 'open'
            END AS effective_state
        FROM safety_signal AS signal
        LEFT JOIN safety_signal_resolution AS resolution
          ON resolution.organization_id = signal.organization_id
         AND resolution.safety_signal_id = signal.id
        LEFT JOIN effective_proposed_change_state AS dismissal
          ON dismissal.organization_id = signal.organization_id
         AND dismissal.id = signal.dismissal_proposed_change_id
         AND dismissal.effective_state = 'approved'
        """
    )


def _create_signal_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_safety_signal_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE registered_kind signal_rule_kind;
        DECLARE proposal_state text;
        DECLARE proposal_value jsonb;
        BEGIN
            SELECT rule_kind INTO registered_kind
            FROM signal_rule
            WHERE organization_id = NEW.organization_id
              AND id = NEW.signal_rule_id
              AND version = NEW.signal_rule_version;
            IF registered_kind IS NULL THEN
                RAISE EXCEPTION 'Safety signal requires versioned rule provenance';
            END IF;
            IF NEW.source_submission_id IS NOT NULL
               AND registered_kind <> 'deterministic' THEN
                RAISE EXCEPTION 'Submission signal requires a deterministic rule';
            END IF;
            IF NEW.escalated_from_signal_id IS NOT NULL
               AND registered_kind <> 'human_escalation' THEN
                RAISE EXCEPTION 'Escalated signal requires a human-escalation rule';
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
                   OR NEW.patient_id IS DISTINCT FROM OLD.patient_id
                   OR NEW.care_episode_id IS DISTINCT FROM OLD.care_episode_id
                   OR NEW.source_submission_id IS DISTINCT FROM OLD.source_submission_id
                   OR NEW.escalated_from_signal_id IS DISTINCT FROM OLD.escalated_from_signal_id
                   OR NEW.signal_rule_id IS DISTINCT FROM OLD.signal_rule_id
                   OR NEW.signal_rule_version IS DISTINCT FROM OLD.signal_rule_version
                   OR NEW.deterministic_level IS DISTINCT FROM OLD.deterministic_level THEN
                    RAISE EXCEPTION
                        'Safety signal identity, origin, rule, and deterministic level '
                        'are immutable';
                END IF;
                IF OLD.acknowledged_at IS NOT NULL AND (
                    NEW.acknowledged_at IS DISTINCT FROM OLD.acknowledged_at
                    OR NEW.acknowledged_by_user_id IS DISTINCT FROM OLD.acknowledged_by_user_id
                    OR NEW.status IS DISTINCT FROM OLD.status
                ) THEN
                    RAISE EXCEPTION 'Safety signal acknowledgement is irreversible';
                END IF;
                IF OLD.dismissal_proposed_change_id IS NOT NULL
                   AND NEW.dismissal_proposed_change_id IS DISTINCT FROM
                       OLD.dismissal_proposed_change_id THEN
                    RAISE EXCEPTION 'Safety signal dismissal is irreversible';
                END IF;
            END IF;

            IF safety_severity_rank(NEW.effective_level) <
               safety_severity_rank(NEW.deterministic_level) THEN
                IF NEW.current_severity_override_proposed_change_id IS NULL THEN
                    RAISE EXCEPTION
                        'Effective severity cannot be below deterministic severity without '
                        'an approved override';
                END IF;
                SELECT effective_state, proposed_value
                INTO proposal_state, proposal_value
                FROM effective_proposed_change_state
                WHERE organization_id = NEW.organization_id
                  AND id = NEW.current_severity_override_proposed_change_id
                  AND safety_signal_id = NEW.id
                  AND change_type = 'override_signal_severity';
                IF proposal_state IS DISTINCT FROM 'approved'
                   OR proposal_value->>'level' IS DISTINCT FROM NEW.effective_level::text THEN
                    RAISE EXCEPTION
                        'Effective severity requires its approved override proposal';
                END IF;
            END IF;

            IF TG_OP = 'UPDATE'
               AND NEW.dismissal_proposed_change_id IS DISTINCT FROM
                   OLD.dismissal_proposed_change_id THEN
                IF NEW.acknowledged_at IS NULL THEN
                    RAISE EXCEPTION 'Safety signal must be acknowledged before dismissal';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM safety_signal_resolution
                    WHERE organization_id = NEW.organization_id
                      AND safety_signal_id = NEW.id
                ) THEN
                    RAISE EXCEPTION 'Resolved safety signal cannot be dismissed';
                END IF;
                SELECT effective_state INTO proposal_state
                FROM effective_proposed_change_state
                WHERE organization_id = NEW.organization_id
                  AND id = NEW.dismissal_proposed_change_id
                  AND safety_signal_id = NEW.id
                  AND change_type = 'dismiss_signal';
                IF proposal_state IS DISTINCT FROM 'approved' THEN
                    RAISE EXCEPTION 'Dismissal requires an approved proposal';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_safety_signal_lifecycle_guard
        BEFORE INSERT OR UPDATE ON safety_signal
        FOR EACH ROW
        EXECUTE FUNCTION guard_safety_signal_lifecycle()
        """
    )


def _create_resolution_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_safety_signal_resolution()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE locked_signal safety_signal%ROWTYPE;
        BEGIN
            SELECT * INTO locked_signal
            FROM safety_signal
            WHERE organization_id = NEW.organization_id
              AND id = NEW.safety_signal_id
            FOR UPDATE;
            IF locked_signal.id IS NULL THEN
                RAISE EXCEPTION 'Safety signal is outside the resolution organization';
            END IF;
            IF locked_signal.acknowledged_at IS NULL THEN
                RAISE EXCEPTION 'Safety signal must be acknowledged before resolution';
            END IF;
            IF locked_signal.dismissal_proposed_change_id IS NOT NULL THEN
                RAISE EXCEPTION 'Dismissed safety signal cannot be resolved';
            END IF;
            IF NULLIF(trim(NEW.resolution_reason), '') IS NULL THEN
                RAISE EXCEPTION 'Resolution reason is required';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_safety_signal_resolution_guard
        BEFORE INSERT ON safety_signal_resolution
        FOR EACH ROW
        EXECUTE FUNCTION guard_safety_signal_resolution()
        """
    )


def _create_revision_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_proposed_change_revision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE predecessor proposed_change%ROWTYPE;
        DECLARE predecessor_state text;
        DECLARE selected_policy approval_policy%ROWTYPE;
        DECLARE registered_schema boolean;
        BEGIN
            IF NEW.change_type = 'dismiss_signal'
               AND NEW.proposed_by_agent_run_id IS NOT NULL THEN
                RAISE EXCEPTION 'Agents cannot propose dismissal';
            END IF;

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
            IF NEW.change_type = 'dismiss_signal'
               AND NEW.deterministic_severity_threshold_snapshot IS NULL THEN
                RAISE EXCEPTION 'Dismissal proposal requires a deterministic threshold';
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
               OR (SELECT count(*) FROM jsonb_object_keys(NEW.proposed_value)) <> 1 THEN
                RAISE EXCEPTION 'Proposed value does not match its registered schema';
            END IF;
            CASE NEW.change_type
                WHEN 'dismiss_signal' THEN
                    IF jsonb_typeof(NEW.proposed_value->'category') IS DISTINCT FROM 'string'
                       OR NEW.proposed_value->>'category' NOT IN (
                            'false_positive', 'duplicate', 'not_applicable'
                       ) THEN
                        RAISE EXCEPTION
                            'Proposed value does not match its registered dismissal schema';
                    END IF;
                WHEN 'override_signal_severity' THEN
                    IF jsonb_typeof(NEW.proposed_value->'level') IS DISTINCT FROM 'string'
                       OR NEW.proposed_value->>'level' NOT IN (
                            'routine', 'urgent', 'emergent'
                       ) THEN
                        RAISE EXCEPTION
                            'Proposed value does not match its registered severity schema';
                    END IF;
                WHEN 'authorize_navigation_task' THEN
                    IF jsonb_typeof(NEW.proposed_value->'title') IS DISTINCT FROM 'string'
                       OR NULLIF(trim(NEW.proposed_value->>'title'), '') IS NULL
                       OR length(NEW.proposed_value->>'title') > 255 THEN
                        RAISE EXCEPTION
                            'Proposed value does not match its registered task schema';
                    END IF;
                WHEN 'authorize_patient_message' THEN
                    IF jsonb_typeof(NEW.proposed_value->'body') IS DISTINCT FROM 'string'
                       OR NULLIF(trim(NEW.proposed_value->>'body'), '') IS NULL THEN
                        RAISE EXCEPTION
                            'Proposed value does not match its registered message schema';
                    END IF;
            END CASE;

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
                RAISE EXCEPTION
                    'Only a pending or declined current proposal can be revised';
            END IF;
            IF EXISTS (
                SELECT 1 FROM safety_signal
                WHERE dismissal_proposed_change_id = predecessor.id
                   OR current_severity_override_proposed_change_id = predecessor.id
            ) THEN
                RAISE EXCEPTION 'An applied proposal cannot be revised';
            END IF;
            IF NEW.change_type IS DISTINCT FROM predecessor.change_type
               OR NEW.safety_signal_id IS DISTINCT FROM predecessor.safety_signal_id
               OR NEW.navigation_task_id IS DISTINCT FROM predecessor.navigation_task_id
               OR NEW.patient_message_id IS DISTINCT FROM predecessor.patient_message_id THEN
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
        CREATE TRIGGER trg_proposed_change_revision_guard
        BEFORE INSERT ON proposed_change
        FOR EACH ROW
        EXECUTE FUNCTION guard_proposed_change_revision()
        """
    )


def _create_decision_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_approval_decision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE proposal proposed_change%ROWTYPE;
        DECLARE assignment role_assignment%ROWTYPE;
        DECLARE locked_signal safety_signal%ROWTYPE;
        DECLARE current_state text;
        DECLARE high_risk boolean := false;
        BEGIN
            SELECT * INTO proposal
            FROM proposed_change
            WHERE organization_id = NEW.organization_id
              AND id = NEW.proposed_change_id
            FOR UPDATE;
            IF proposal.id IS NULL THEN
                RAISE EXCEPTION 'Proposed change is outside the decision organization';
            END IF;
            IF proposal.safety_signal_id IS NOT NULL THEN
                SELECT * INTO locked_signal
                FROM safety_signal
                WHERE organization_id = proposal.organization_id
                  AND id = proposal.safety_signal_id
                FOR UPDATE;
            END IF;
            SELECT * INTO assignment
            FROM role_assignment
            WHERE organization_id = NEW.organization_id
              AND user_id = NEW.authorized_by_user_id
              AND id = NEW.qualifying_role_assignment_id
            FOR UPDATE;
            IF assignment.id IS NULL
               OR assignment.role IS DISTINCT FROM proposal.required_approver_role_snapshot
               OR NEW.qualifying_role_snapshot IS DISTINCT FROM assignment.role
               OR NEW.authorized_at < assignment.granted_at
               OR (
                   assignment.revoked_at IS NOT NULL
                   AND NEW.authorized_at >= assignment.revoked_at
               ) THEN
                RAISE EXCEPTION 'Role assignment does not qualify for this proposal';
            END IF;
            SELECT effective_state INTO current_state
            FROM effective_proposed_change_state
            WHERE id = proposal.id;
            IF current_state IS DISTINCT FROM 'pending' THEN
                RAISE EXCEPTION 'Approval decisions require a current pending proposal';
            END IF;
            IF proposal.change_type = 'dismiss_signal'
               AND (
                    proposal.deterministic_severity_threshold_snapshot IS NULL
                    OR safety_severity_rank(locked_signal.deterministic_level) >=
                       safety_severity_rank(
                           proposal.deterministic_severity_threshold_snapshot
                       )
               ) THEN
                high_risk := true;
            END IF;
            IF NEW.decision = 'approved'
               AND proposal.proposed_by_user_id = NEW.authorized_by_user_id
               AND (high_risk OR NOT proposal.allow_self_approval_snapshot) THEN
                RAISE EXCEPTION 'The proposer cannot approve this change';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_decision_guard
        BEFORE INSERT ON approval_decision
        FOR EACH ROW
        EXECUTE FUNCTION guard_approval_decision()
        """
    )
    op.execute(
        """
        CREATE FUNCTION apply_final_approval_decision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE proposal proposed_change%ROWTYPE;
        DECLARE current_state text;
        DECLARE locked_signal safety_signal%ROWTYPE;
        DECLARE requested_level safety_severity;
        BEGIN
            IF NEW.decision <> 'approved' THEN
                RETURN NEW;
            END IF;
            SELECT * INTO proposal
            FROM proposed_change
            WHERE organization_id = NEW.organization_id
              AND id = NEW.proposed_change_id
            FOR UPDATE;
            IF proposal.safety_signal_id IS NOT NULL THEN
                SELECT * INTO locked_signal
                FROM safety_signal
                WHERE organization_id = proposal.organization_id
                  AND id = proposal.safety_signal_id
                FOR UPDATE;
                IF locked_signal.id IS NULL THEN
                    RAISE EXCEPTION 'Proposal target is outside the organization';
                END IF;
            END IF;

            -- The final-state lock order is proposal, safety target, then each exact
            -- referenced role assignment in stable identifier order. Re-read the
            -- canonical view only after all authorizing history is locked.
            PERFORM 1
            FROM approval_decision AS decision
            JOIN role_assignment AS assignment
              ON assignment.organization_id = decision.organization_id
             AND assignment.user_id = decision.authorized_by_user_id
             AND assignment.id = decision.qualifying_role_assignment_id
            WHERE decision.organization_id = proposal.organization_id
              AND decision.proposed_change_id = proposal.id
            ORDER BY assignment.id
            FOR UPDATE OF assignment;

            SELECT effective_state INTO current_state
            FROM effective_proposed_change_state
            WHERE organization_id = proposal.organization_id
              AND id = proposal.id;
            IF current_state <> 'approved' THEN
                RETURN NEW;
            END IF;
            IF proposal.change_type NOT IN (
                'dismiss_signal', 'override_signal_severity'
            ) THEN
                RETURN NEW;
            END IF;
            IF proposal.change_type = 'dismiss_signal' THEN
                IF locked_signal.acknowledged_at IS NULL THEN
                    RAISE EXCEPTION 'Safety signal must be acknowledged before dismissal';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM safety_signal_resolution
                    WHERE organization_id = proposal.organization_id
                      AND safety_signal_id = proposal.safety_signal_id
                ) THEN
                    RAISE EXCEPTION 'Resolved safety signal cannot be dismissed';
                END IF;
                IF locked_signal.dismissal_proposed_change_id IS NOT NULL THEN
                    RAISE EXCEPTION 'Safety signal is already dismissed';
                END IF;
                UPDATE safety_signal
                SET dismissal_proposed_change_id = proposal.id
                WHERE id = locked_signal.id;
            ELSE
                BEGIN
                    requested_level := (proposal.proposed_value->>'level')::safety_severity;
                EXCEPTION WHEN invalid_text_representation THEN
                    RAISE EXCEPTION 'Severity override value is invalid';
                END;
                UPDATE safety_signal
                SET effective_level = requested_level,
                    current_severity_override_proposed_change_id = proposal.id
                WHERE id = locked_signal.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_decision_apply
        AFTER INSERT ON approval_decision
        FOR EACH ROW
        EXECUTE FUNCTION apply_final_approval_decision()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading 0004 would discard proposal and terminal authorization provenance; "
        "reset the synthetic demo database instead."
    )
