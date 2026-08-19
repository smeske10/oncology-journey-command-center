from __future__ import annotations

from app.db import models

EXPECTED_TASK_FIVE_TABLES = {
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


def test_model_package_preserves_task_five_table_set() -> None:
    assert hasattr(models, "__path__")
    assert set(models.Base.metadata.tables) == EXPECTED_TASK_FIVE_TABLES
    assert models.Organization.__tablename__ == "organization"
    assert models.SafetySignal.__tablename__ == "safety_signal"
