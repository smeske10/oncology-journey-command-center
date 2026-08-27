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
        "approval_decision": {"navigation_task"},
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
            "ck_safety_signal_ck_safety_signal_severity_state",
            "ck_safety_signal_ck_safety_signal_status_state",
        },
        "navigation_task": {
            "ck_navigation_task_ck_navigation_task_assignment_shape",
            "ck_navigation_task_ck_navigation_task_cancellation_shape",
            "ck_navigation_task_ck_navigation_task_status_state",
        },
        "approval_decision": {"ck_approval_decision_ck_approval_decision_status_state"},
        "knowledge_document": {"ck_knowledge_document_ck_knowledge_document_status_state"},
        "agent_run": {"ck_agent_run_ck_agent_run_status_state"},
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
