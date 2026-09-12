from app.db.privilege_contracts import v0007
from app.db.privilege_validation import application_relation_catalog_statement


def test_v0007_contract_keeps_the_independently_reviewed_object_surface() -> None:
    assert v0007.REVISION == "0007_database_least_privilege"
    assert v0007.APPLICATION_GROUP == "ojcc_app"
    assert v0007.APPLICATION_RELKINDS == ("r", "p", "v", "m", "S", "f")
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
