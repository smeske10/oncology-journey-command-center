from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.db.models import ReportedNeed


def test_core_domain_metadata_uses_postgresql_jsonb_and_tenant_first_indexes() -> None:
    expected_tables = {
        "organization",
        "user_account",
        "role_assignment",
        "synthetic_patient",
        "care_episode",
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
        "role_assignment": {"user_account"},
        "care_episode": {"synthetic_patient", "pathway_definition"},
        "check_in_definition": {"pathway_definition"},
        "check_in_submission": {"synthetic_patient", "check_in_definition"},
        "reported_need": {"synthetic_patient", "check_in_submission"},
        "safety_signal": {"synthetic_patient", "check_in_submission"},
        "navigation_task": {"synthetic_patient", "reported_need", "user_account"},
        "approval_decision": {"navigation_task", "user_account"},
        "knowledge_document": {"resource"},
        "agent_run": {"synthetic_patient", "check_in_submission", "reported_need"},
        "outcome": {"synthetic_patient", "reported_need"},
        "audit_event": {"user_account"},
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
        "role_assignment": {"ck_role_assignment_ck_role_assignment_role_state"},
        "care_episode": {"ck_care_episode_ck_care_episode_status_state"},
        "check_in_submission": {"ck_check_in_submission_ck_check_in_submission_status_state"},
        "reported_need": {"ck_reported_need_ck_reported_need_status_state"},
        "safety_signal": {
            "ck_safety_signal_ck_safety_signal_severity_state",
            "ck_safety_signal_ck_safety_signal_status_state",
        },
        "navigation_task": {"ck_navigation_task_ck_navigation_task_status_state"},
        "approval_decision": {"ck_approval_decision_ck_approval_decision_status_state"},
        "knowledge_document": {"ck_knowledge_document_ck_knowledge_document_status_state"},
        "agent_run": {"ck_agent_run_ck_agent_run_status_state"},
        "outcome": {"ck_outcome_ck_outcome_status_state"},
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
