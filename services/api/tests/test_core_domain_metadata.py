from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.db.models import ReportedNeed
from app.domain.enums import NavigationTaskStatus, NeedStatus


def test_core_domain_metadata_uses_postgresql_jsonb_and_tenant_first_indexes() -> None:
    expected_tables = {
        "organization",
        "user_account",
        "role_assignment",
        "patient_identity_link",
        "synthetic_patient",
        "care_episode",
        "episode_pathway_assignment",
        "pathway_definition",
        "check_in_definition",
        "check_in_submission",
        "reported_need",
        "safety_signal",
        "navigation_task",
        "approval_decision",
        "resource",
        "knowledge_document",
        "agent_run",
        "outcome",
        "audit_event",
    }

    assert expected_tables <= set(Base.metadata.tables)
    assert "JSONB" in str(CreateTable(ReportedNeed.__table__).compile(dialect=postgresql.dialect()))

    tenant_tables = [
        table for table in Base.metadata.tables.values() if "organization_id" in table.c
    ]
    for table in tenant_tables:
        for index in table.indexes:
            assert index.expressions[0].name == "organization_id"


def test_tenant_owned_relationships_use_organization_aware_foreign_keys() -> None:
    expected_relationships = {
        "care_episode": {"synthetic_patient"},
        "episode_pathway_assignment": {"care_episode", "pathway_definition"},
        "check_in_definition": {"pathway_definition"},
        "check_in_submission": {"synthetic_patient", "check_in_definition"},
        "reported_need": {"synthetic_patient", "check_in_submission"},
        "safety_signal": {"synthetic_patient", "check_in_submission"},
        "navigation_task": {"synthetic_patient", "reported_need"},
        "approval_decision": {"proposed_change", "role_assignment"},
        "knowledge_document": {"resource"},
        "agent_run": {"synthetic_patient", "check_in_submission", "reported_need"},
        "outcome": {"synthetic_patient", "reported_need"},
    }

    for table_name, referenced_tables in expected_relationships.items():
        foreign_keys = [
            constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, ForeignKeyConstraint)
            and "organization_id" in constraint.column_keys
        ]
        assert {constraint.referred_table.name for constraint in foreign_keys} >= referenced_tables
        for constraint in foreign_keys:
            if constraint.referred_table.name in referenced_tables:
                assert constraint.column_keys[0] == "organization_id"
                assert constraint.elements[0].column.name == "organization_id"


def test_check_constraint_names_match_the_immutable_task_five_contract() -> None:
    expected_names = {
        "role_assignment": {
            "ck_role_assignment_ck_role_assignment_grant_interval",
            "ck_role_assignment_ck_role_assignment_role_state",
        },
        "care_episode": {"ck_care_episode_ck_care_episode_status_state"},
        "episode_pathway_assignment": {
            "ck_episode_pathway_assignment_ck_episode_pathway_assign_155e"
        },
        "check_in_submission": {
            "ck_check_in_submission_ck_check_in_submission_provenance",
            "ck_check_in_submission_ck_check_in_submission_status_state",
        },
        "reported_need": {
            "ck_reported_need_ck_reported_need_origin",
            "ck_reported_need_ck_reported_need_status_state",
        },
        "safety_signal": {
            "ck_safety_signal_ck_safety_signal_acknowledgement_shape",
            "ck_safety_signal_ck_safety_signal_deterministic_level_state",
            "ck_safety_signal_ck_safety_signal_dismissal_change_type",
            "ck_safety_signal_ck_safety_signal_effective_level_state",
            "ck_safety_signal_ck_safety_signal_escalation_not_self",
            "ck_safety_signal_ck_safety_signal_origin",
            "ck_safety_signal_ck_safety_signal_override_change_type",
            "ck_safety_signal_ck_safety_signal_status_state",
        },
        "navigation_task": {
            "ck_navigation_task_ck_navigation_task_assignment_shape",
            "ck_navigation_task_ck_navigation_task_cancellation_shape",
            "ck_navigation_task_ck_navigation_task_status_state",
        },
        "approval_decision": {
            "ck_approval_decision_ck_approval_decision_decision_state",
            "ck_approval_decision_ck_approval_decision_decline_reason",
            "ck_approval_decision_ck_approval_decision_qualifying_ro_7502",
        },
        "knowledge_document": set(),
        "agent_run": {
            "ck_agent_run_ck_agent_run_status_state",
            "ck_agent_run_ck_agent_run_workflow_lineage_shape",
        },
        "outcome": {"ck_outcome_ck_outcome_disposition_state"},
    }

    actual_names = {
        table_name: {
            constraint.name
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint)
        }
        for table_name in expected_names
    }

    assert actual_names == expected_names


def test_need_task_outcome_metadata_matches_the_authorizing_record_contract() -> None:
    submission = Base.metadata.tables["check_in_submission"]
    need = Base.metadata.tables["reported_need"]
    task = Base.metadata.tables["navigation_task"]
    outcome = Base.metadata.tables["outcome"]

    assert [member.value for member in NeedStatus] == ["open", "in_progress"]
    assert [member.value for member in NavigationTaskStatus] == [
        "open",
        "assigned",
        "in_progress",
        "completed",
        "cancelled",
    ]
    assert "care_episode_id" in need.c
    assert need.c.care_episode_id.nullable is False
    assert need.c.source_submission_id.nullable is True
    assert need.c.reopened_from_need_id.nullable is True
    assert "resolved_at" not in need.c
    assert "outcome_id" not in need.c
    assert task.c.reported_need_id.nullable is False
    assert {
        "cancelled_by_user_id",
        "cancelled_at",
        "cancellation_reason",
    } <= set(task.c.keys())
    assert {
        "recorded_by_user_id",
        "recorded_at",
        "disposition",
        "note",
        "idempotency_key",
    } <= set(outcome.c.keys())
    assert "status" not in outcome.c
    assert "reason" not in outcome.c
    assert "created_at" not in outcome.c

    submission_unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in submission.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    need_unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in need.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    outcome_unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in outcome.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("organization_id", "patient_id", "care_episode_id", "id") in submission_unique_keys
    assert ("organization_id", "patient_id", "care_episode_id", "id") in need_unique_keys
    assert ("organization_id", "patient_id", "id") in need_unique_keys
    assert ("reopened_from_need_id",) in need_unique_keys
    assert ("reported_need_id",) in outcome_unique_keys
    assert ("organization_id", "idempotency_key") in outcome_unique_keys

    need_foreign_keys = {
        tuple(constraint.column_keys): tuple(element.column.name for element in constraint.elements)
        for constraint in need.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    task_foreign_keys = {
        tuple(constraint.column_keys): tuple(element.column.name for element in constraint.elements)
        for constraint in task.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    outcome_foreign_keys = {
        tuple(constraint.column_keys): tuple(element.column.name for element in constraint.elements)
        for constraint in outcome.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert need_foreign_keys[
        ("organization_id", "patient_id", "care_episode_id", "source_submission_id")
    ] == ("organization_id", "patient_id", "care_episode_id", "id")
    assert need_foreign_keys[
        ("organization_id", "patient_id", "care_episode_id", "reopened_from_need_id")
    ] == ("organization_id", "patient_id", "care_episode_id", "id")
    assert task_foreign_keys[("organization_id", "patient_id", "reported_need_id")] == (
        "organization_id",
        "patient_id",
        "id",
    )
    assert outcome_foreign_keys[("organization_id", "patient_id", "reported_need_id")] == (
        "organization_id",
        "patient_id",
        "id",
    )


def test_safety_and_approval_metadata_matches_the_authorizing_record_contract() -> None:
    expected_tables = {
        "signal_rule",
        "safety_signal_resolution",
        "approval_policy",
        "proposed_value_schema",
        "proposed_change",
        "patient_message",
    }
    assert expected_tables <= set(Base.metadata.tables)

    signal = Base.metadata.tables["safety_signal"]
    signal_rule = Base.metadata.tables["signal_rule"]
    policy = Base.metadata.tables["approval_policy"]
    value_schema = Base.metadata.tables["proposed_value_schema"]
    proposal = Base.metadata.tables["proposed_change"]
    decision = Base.metadata.tables["approval_decision"]
    resolution = Base.metadata.tables["safety_signal_resolution"]

    assert {
        "care_episode_id",
        "escalated_from_signal_id",
        "signal_rule_id",
        "signal_rule_version",
        "deterministic_level",
        "effective_level",
        "acknowledged_by_user_id",
        "acknowledged_at",
        "dismissal_proposed_change_id",
        "current_severity_override_proposed_change_id",
    } <= set(signal.c.keys())
    assert "severity" not in signal.c
    assert "rule_code" not in signal.c
    assert "resolved_at" not in signal.c
    assert resolution.c.safety_signal_id.nullable is False
    signal_checks = {
        str(constraint.sqltext)
        for constraint in signal.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert any("escalated_from_signal_id <> id" in expression for expression in signal_checks)
    assert {"rule_code", "version", "rule_kind", "name"} <= set(signal_rule.c.keys())

    policy_checks = {
        str(constraint.sqltext)
        for constraint in policy.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert any(
        "change_type" in expression
        and "dismiss_signal" in expression
        and "deterministic_severity_threshold IS NOT NULL" in expression
        for expression in policy_checks
    )
    assert tuple(value_schema.primary_key.columns.keys()) == (
        "change_type",
        "value_schema_id",
        "value_schema_version",
    )

    assert {
        "safety_signal_id",
        "navigation_task_id",
        "patient_message_id",
        "proposed_by_user_id",
        "proposed_by_agent_run_id",
        "approval_policy_id",
        "approval_policy_version",
        "deterministic_severity_threshold_snapshot",
        "allow_self_approval_snapshot",
        "required_approval_count_snapshot",
        "required_approver_role_snapshot",
    } <= set(proposal.c.keys())
    assert {
        "proposed_change_id",
        "authorized_by_user_id",
        "qualifying_role_assignment_id",
        "qualifying_role_snapshot",
        "decision",
        "authorized_at",
    } <= set(decision.c.keys())
    assert "proposed_value" not in decision.c
    assert "final_value" not in decision.c
    assert "navigation_task_id" not in decision.c

    proposal_unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in proposal.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    decision_unique_keys = {
        tuple(constraint.columns.keys())
        for constraint in decision.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert (
        "id",
        "safety_signal_id",
        "organization_id",
        "change_type",
    ) in proposal_unique_keys
    assert ("supersedes_proposed_change_id",) in proposal_unique_keys
    assert ("proposed_change_id", "authorized_by_user_id") in decision_unique_keys
    proposal_foreign_keys = {
        tuple(constraint.columns.keys())
        for constraint in proposal.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        "change_type",
        "value_schema_id",
        "value_schema_version",
    ) in proposal_foreign_keys
