from app.db.privilege_contracts import v0007
from app.db.privilege_validation import application_relation_catalog_statement


def test_v0007_contract_keeps_the_independently_reviewed_object_surface() -> None:
    assert v0007.REVISION == "0007_database_least_privilege"
    assert v0007.APPLICATION_GROUP == "ojcc_app"
    assert v0007.APPLICATION_RELKINDS == ("r", "p", "v", "m", "S", "f")
    assert v0007.APPROVED_PUBLIC_EXECUTE_EXTENSIONS == (("btree_gist", "1.7"),)
    assert set(v0007.SELECT_ONLY_RELATIONS) == {
        "agent_run",
        "agent_run_citation",
        "approval_policy",
        "audit_event",
        "care_episode",
        "check_in_definition",
        "episode_pathway_assignment",
        "follow_up_request",
        "knowledge_document",
        "manual_review_task",
        "navigation_task_resource",
        "organization",
        "organization_knowledge_approval",
        "pathway_definition",
        "patient_identity_link",
        "patient_message",
        "proposed_value_schema",
        "reported_need",
        "resource",
        "role_assignment",
        "signal_rule",
        "synthetic_patient",
        "user_account",
        "workflow_run",
        "workflow_transition_event",
    }
    assert set(v0007.INSERT_RELATIONS) == {
        "approval_decision",
        "check_in_submission",
        "follow_up_response",
        "outcome",
        "proposed_change",
        "safety_signal_resolution",
    }
    assert set(v0007.UPDATE_RELATIONS) == {"navigation_task", "safety_signal"}
    assert set(v0007.APPLICATION_VIEWS) == {
        "active_check_in_submission",
        "effective_need_state",
        "effective_proposed_change_state",
        "effective_safety_signal_state",
    }
    assert set(v0007.SECURITY_DEFINER_FUNCTIONS) == {
        "append_workflow_transition_event",
        "apply_final_approval_decision",
        "apply_navigation_resource_approval",
        "close_reported_need_from_outcome",
        "guard_approval_decision",
        "guard_bound_navigation_task_delete",
        "guard_follow_up_request_insert",
        "guard_follow_up_response_insert",
        "guard_navigation_task_lifecycle",
        "guard_navigation_task_resource_proposal",
        "guard_patient_identity_link_response_history",
        "guard_proposed_change_revision",
        "guard_safety_signal_resolution",
        "record_navigation_task_transition",
    }
    assert set(v0007.SECURITY_INVOKER_FUNCTIONS) == {
        "guard_agent_run_citation",
        "guard_agent_run_citation_immutable",
        "guard_agent_run_created_at",
        "guard_knowledge_approval_history",
        "guard_knowledge_document_immutable",
        "guard_manual_review_task",
        "guard_navigation_task_resource",
        "guard_reported_need_identity_update",
        "guard_reported_need_reopening",
        "guard_role_assignment_approval_history",
        "guard_role_assignment_knowledge_history",
        "guard_safety_signal_lifecycle",
        "guard_workflow_run_lineage",
        "reject_append_only_mutation",
        "reject_approval_policy_mutation",
        "reject_proposed_value_schema_mutation",
        "reject_signal_rule_mutation",
        "safety_severity_rank",
    }
    assert {
        identity for identity in v0007.FUNCTION_IDENTITIES if identity[1]
    } == {("safety_severity_rank", "value safety_severity")}
    assert all(
        arguments == ""
        for name, arguments in v0007.FUNCTION_IDENTITIES
        if name != "safety_severity_rank"
    )
    assert v0007.TABLE_PRIVILEGES == (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    )
    assert v0007.COLUMN_PRIVILEGES == ("SELECT", "INSERT", "UPDATE", "REFERENCES")


def test_application_relation_scan_binds_every_supported_relkind() -> None:
    statement = application_relation_catalog_statement()

    expected_predicate = (
        "class.relkind IN "
        "(:relkind_0, :relkind_1, :relkind_2, :relkind_3, :relkind_4, :relkind_5)"
    )
    assert expected_predicate in statement.text
    assert statement.compile().params == {
        "relkind_0": "r",
        "relkind_1": "p",
        "relkind_2": "v",
        "relkind_3": "m",
        "relkind_4": "S",
        "relkind_5": "f",
    }
