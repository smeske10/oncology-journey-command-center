from __future__ import annotations

from app.db import models
from app.db.models import (
    approvals,
    audit,
    identity,
    knowledge,
    needs,
    pathways,
    safety,
    submissions,
    workflow,
)

EXPECTED_RECONCILED_TABLES_THROUGH_TASK_FOUR = {
    "organization",
    "user_account",
    "role_assignment",
    "synthetic_patient",
    "care_episode",
    "episode_pathway_assignment",
    "pathway_definition",
    "patient_identity_link",
    "check_in_definition",
    "check_in_submission",
    "reported_need",
    "safety_signal",
    "navigation_task",
    "approval_decision",
    "approval_policy",
    "proposed_value_schema",
    "patient_message",
    "proposed_change",
    "resource",
    "knowledge_document",
    "agent_run",
    "outcome",
    "audit_event",
    "safety_signal_resolution",
    "signal_rule",
}


def test_model_package_includes_reconciled_tables_through_task_four() -> None:
    assert hasattr(models, "__path__")
    assert set(models.Base.metadata.tables) == EXPECTED_RECONCILED_TABLES_THROUGH_TASK_FOUR
    assert models.Organization.__tablename__ == "organization"
    assert models.SafetySignal.__tablename__ == "safety_signal"


def test_domain_modules_own_their_mapped_models() -> None:
    assert identity.Organization.__module__ == identity.__name__
    assert pathways.PathwayDefinition.__module__ == pathways.__name__
    assert submissions.CheckInSubmission.__module__ == submissions.__name__
    assert needs.ReportedNeed.__module__ == needs.__name__
    assert safety.SafetySignal.__module__ == safety.__name__
    assert approvals.ApprovalDecision.__module__ == approvals.__name__
    assert workflow.AgentRun.__module__ == workflow.__name__
    assert knowledge.KnowledgeDocument.__module__ == knowledge.__name__
    assert audit.AuditEvent.__module__ == audit.__name__
