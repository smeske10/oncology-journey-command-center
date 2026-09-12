"""Frozen database privilege contract introduced by migration 0007."""

REVISION = "0007_database_least_privilege"
PREVIOUS_REVISION = "0006_navigator_closed_loop"
APPLICATION_GROUP = "ojcc_app"
APPLICATION_RELKINDS = ("r", "p", "v", "m", "S", "f")

SELECT_ONLY_RELATIONS = (
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
)
INSERT_RELATIONS = (
    "approval_decision",
    "check_in_submission",
    "follow_up_response",
    "outcome",
    "proposed_change",
    "safety_signal_resolution",
)
UPDATE_RELATIONS = ("navigation_task", "safety_signal")
APPLICATION_VIEWS = (
    "active_check_in_submission",
    "effective_need_state",
    "effective_proposed_change_state",
    "effective_safety_signal_state",
)
APPLICATION_RELATIONS = (
    *SELECT_ONLY_RELATIONS,
    *INSERT_RELATIONS,
    *UPDATE_RELATIONS,
    *APPLICATION_VIEWS,
)
CATALOG_RELATIONS = (*APPLICATION_RELATIONS, "alembic_version")
RUNTIME_SELECT_RELATIONS = (
    *APPLICATION_RELATIONS,
    "alembic_version",
)
EXPECTED_RELATION_KINDS = tuple(
    (name, "v" if name in APPLICATION_VIEWS else "r") for name in CATALOG_RELATIONS
)

SECURITY_DEFINER_FUNCTIONS = (
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
)
SECURITY_INVOKER_FUNCTIONS = (
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
)
APPLICATION_FUNCTIONS = (*SECURITY_DEFINER_FUNCTIONS, *SECURITY_INVOKER_FUNCTIONS)
FUNCTION_IDENTITIES = tuple(
    (name, "value safety_severity" if name == "safety_severity_rank" else "")
    for name in APPLICATION_FUNCTIONS
)
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
