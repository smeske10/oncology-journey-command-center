from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.models import CurrentActor, Role
from app.db.models import (
    CheckInDefinition,
    CheckInSubmission,
    NavigationTask,
    NavigationTaskResource,
    Outcome,
    ProposedChange,
    ReportedNeed,
    Resource,
)
from app.domain.enums import (
    CheckInStatus,
    NeedStatus,
    OutcomeDisposition,
    SubmissionSource,
)


def _navigator(case: Any) -> CurrentActor:
    return CurrentActor(
        user_id=case.navigator_user_id,
        organization_id=case.organization_id,
        role=Role.NAVIGATOR,
    )


def test_workspace_exposes_exact_source_and_correction_is_not_independent_history(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    source = closed_loop_session.get(
        CheckInSubmission, closed_loop_case.source_submission_id
    )
    assert source is not None
    correction = CheckInSubmission(
        organization_id=closed_loop_case.organization_id,
        patient_id=closed_loop_case.patient_id,
        care_episode_id=closed_loop_case.care_episode_id,
        check_in_definition_id=source.check_in_definition_id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": False},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=closed_loop_case.patient_user_id,
        supersedes_submission_id=closed_loop_case.source_submission_id,
        submitted_at=datetime.now(UTC),
    )
    closed_loop_session.add(correction)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )

    assert response.status_code == 200, response.text
    workspace = response.json()
    assert workspace["need"]["id"] == str(closed_loop_case.reported_need_id)
    assert workspace["need"]["patient_id"] == str(closed_loop_case.patient_id)
    assert workspace["need"]["care_episode_id"] == str(
        closed_loop_case.care_episode_id
    )
    assert workspace["need"]["effective_state"] == "open"
    assert workspace["evidence"][0]["source_submission_id"] == str(correction.id)
    assert workspace["evidence"][0]["provenance_kind"] == "source_submission"
    assert workspace["comparisons"]["correction"] == {
        "status": "available",
        "label": "Correction to the same check-in.",
        "previous_submission_id": str(closed_loop_case.source_submission_id),
        "current_submission_id": str(correction.id),
        "deltas": [
            {
                "field_identifier": "transportation",
                "previous_present": True,
                "current_present": True,
                "previous_value": True,
                "current_value": False,
            }
        ],
    }
    assert workspace["comparisons"]["between_check_ins"]["status"] == (
        "insufficient_history"
    )


def test_workspace_hides_foreign_tenant_need(
    closed_loop_client: Any,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(approved_closed_loop_case),
    )

    assert response.status_code == 404


def test_workspace_does_not_leak_foreign_case_source_records(
    closed_loop_client: Any,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )

    assert response.status_code == 200, response.text
    assert str(approved_closed_loop_case.source_submission_id) not in response.text


def test_between_check_ins_uses_root_time_and_active_leaves_with_absent_vs_null(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    source = closed_loop_session.get(
        CheckInSubmission, closed_loop_case.source_submission_id
    )
    assert source is not None
    independent = CheckInSubmission(
        organization_id=closed_loop_case.organization_id,
        patient_id=closed_loop_case.patient_id,
        care_episode_id=closed_loop_case.care_episode_id,
        check_in_definition_id=source.check_in_definition_id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": True, "optional_detail": None},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=closed_loop_case.patient_user_id,
        submitted_at=source.submitted_at + timedelta(minutes=1),
    )
    late_correction = CheckInSubmission(
        organization_id=closed_loop_case.organization_id,
        patient_id=closed_loop_case.patient_id,
        care_episode_id=closed_loop_case.care_episode_id,
        check_in_definition_id=source.check_in_definition_id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": False},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=closed_loop_case.patient_user_id,
        supersedes_submission_id=source.id,
        submitted_at=source.submitted_at + timedelta(days=1),
    )
    closed_loop_session.add_all([independent, late_correction])
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )
    assert response.status_code == 200, response.text
    between = response.json()["comparisons"]["between_check_ins"]
    assert between["status"] == "available"
    assert between["previous_submission_id"] == str(late_correction.id)
    assert between["current_submission_id"] == str(independent.id)
    assert between["deltas"] == [
        {
            "field_identifier": "optional_detail",
            "previous_present": False,
            "current_present": True,
            "previous_value": None,
            "current_value": None,
        },
        {
            "field_identifier": "transportation",
            "previous_present": True,
            "current_present": True,
            "previous_value": False,
            "current_value": True,
        },
    ]


def test_between_check_ins_uses_root_id_as_equal_timestamp_tiebreaker(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    source = closed_loop_session.get(
        CheckInSubmission, closed_loop_case.source_submission_id
    )
    assert source is not None
    tied_at = source.submitted_at + timedelta(minutes=5)
    earlier_id = UUID(int=1)
    later_id = UUID(int=2)
    for submission_id, answer in ((later_id, False), (earlier_id, True)):
        closed_loop_session.add(
            CheckInSubmission(
                id=submission_id,
                organization_id=closed_loop_case.organization_id,
                patient_id=closed_loop_case.patient_id,
                care_episode_id=closed_loop_case.care_episode_id,
                check_in_definition_id=source.check_in_definition_id,
                status=CheckInStatus.SUBMITTED,
                answers={"transportation": answer},
                submission_source=SubmissionSource.PATIENT,
                submitted_by_user_id=closed_loop_case.patient_user_id,
                submitted_at=tied_at,
            )
        )
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )
    between = response.json()["comparisons"]["between_check_ins"]
    assert between["previous_submission_id"] == str(earlier_id)
    assert between["current_submission_id"] == str(later_id)


def test_between_check_ins_reports_incompatible_definition_version(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    source = closed_loop_session.get(
        CheckInSubmission, closed_loop_case.source_submission_id
    )
    assert source is not None
    definition = closed_loop_session.get(
        CheckInDefinition, source.check_in_definition_id
    )
    assert definition is not None
    incompatible = CheckInDefinition(
        organization_id=closed_loop_case.organization_id,
        pathway_definition_id=definition.pathway_definition_id,
        slug=definition.slug,
        version=definition.version + 1,
        title="Incompatible synthetic version",
        questionnaire=definition.questionnaire,
    )
    closed_loop_session.add(incompatible)
    closed_loop_session.flush()
    later = CheckInSubmission(
        organization_id=closed_loop_case.organization_id,
        patient_id=closed_loop_case.patient_id,
        care_episode_id=closed_loop_case.care_episode_id,
        check_in_definition_id=incompatible.id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": False},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=closed_loop_case.patient_user_id,
        submitted_at=source.submitted_at + timedelta(minutes=1),
    )
    closed_loop_session.add(later)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )
    between = response.json()["comparisons"]["between_check_ins"]
    assert between["status"] == "not_comparable"
    assert between["previous_submission_id"] == str(source.id)
    assert between["current_submission_id"] == str(later.id)
    assert between["deltas"] == []


def test_workspace_projects_separate_v1_and_v2_proposal_roots_with_exact_resources(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    task = closed_loop_session.get(NavigationTask, closed_loop_case.navigation_task_id)
    original = closed_loop_session.get(ProposedChange, closed_loop_case.proposed_change_id)
    assert task is not None
    assert original is not None
    resource = Resource(
        organization_id=closed_loop_case.organization_id,
        name="Synthetic transport directory",
        category="transportation",
        url="https://example.test/transport",
        is_active=True,
        metadata_json={"audience": "synthetic"},
    )
    closed_loop_session.add(resource)
    closed_loop_session.flush()
    snapshot = {
        "resource_id": str(resource.id),
        "name": resource.name,
        "category": resource.category,
        "url": resource.url,
        "metadata": resource.metadata_json,
        "match_rationale": "Matches the synthetic transportation need.",
    }
    v2 = ProposedChange(
        organization_id=closed_loop_case.organization_id,
        proposed_by_user_id=closed_loop_case.proposer_user_id,
        proposed_at=datetime.now(UTC),
        change_type=original.change_type,
        proposed_value={"title": "Use the exact v2 resource", "resources": [snapshot]},
        rationale="Synthetic resource-bearing proposal.",
        value_schema_id=original.value_schema_id,
        value_schema_version=2,
        navigation_task_id=task.id,
        approval_policy_id=original.approval_policy_id,
        approval_policy_version=original.approval_policy_version,
        deterministic_severity_threshold_snapshot=(
            original.deterministic_severity_threshold_snapshot
        ),
        allow_self_approval_snapshot=original.allow_self_approval_snapshot,
        required_approval_count_snapshot=original.required_approval_count_snapshot,
        required_approver_role_snapshot=original.required_approver_role_snapshot,
    )
    closed_loop_session.add(v2)
    closed_loop_session.flush()
    closed_loop_session.add(
        NavigationTaskResource(
            organization_id=closed_loop_case.organization_id,
            navigation_task_id=task.id,
            resource_id=resource.id,
            proposed_change_id=v2.id,
            resource_name_snapshot=resource.name,
            resource_category_snapshot=resource.category,
            resource_url_snapshot=resource.url,
            resource_metadata_snapshot=resource.metadata_json,
            match_rationale_snapshot=snapshot["match_rationale"],
            proposed_at=v2.proposed_at,
        )
    )
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )
    assert response.status_code == 200, response.text
    proposals = response.json()["tasks"][0]["proposals"]
    by_id = {proposal["id"]: proposal for proposal in proposals}
    assert set(by_id) == {str(original.id), str(v2.id)}
    assert by_id[str(original.id)]["root_proposal_id"] == str(original.id)
    assert by_id[str(original.id)]["value_schema_version"] == 1
    assert by_id[str(original.id)]["resources"] == []
    assert by_id[str(v2.id)]["root_proposal_id"] == str(v2.id)
    assert by_id[str(v2.id)]["supported"] is True
    assert by_id[str(v2.id)]["reviewable"] is True
    assert by_id[str(v2.id)]["resources"][0]["resource_id"] == str(resource.id)
    assert by_id[str(v2.id)]["resources"][0]["name"] == resource.name


def test_workspace_projects_exact_approved_decision_and_policy_snapshot(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "GET",
        (
            "/v1/navigator/needs/"
            f"{approved_closed_loop_case.reported_need_id}/workspace"
        ),
        actor=_navigator(approved_closed_loop_case),
    )

    assert response.status_code == 200, response.text
    proposal = response.json()["tasks"][0]["proposals"][0]
    assert proposal["id"] == str(approved_closed_loop_case.proposed_change_id)
    assert proposal["state"] == "approved"
    assert proposal["execution_authorized"] is True
    assert proposal["reviewable"] is False
    assert proposal["policy"] == {
        "id": proposal["policy"]["id"],
        "version": 1,
        "allow_self_approval": False,
        "required_approval_count": 1,
        "required_approver_role": "navigator",
        "deterministic_severity_threshold": None,
    }
    assert proposal["decisions"] == [
        {
            "id": proposal["decisions"][0]["id"],
            "authorized_by_user_id": str(approved_closed_loop_case.navigator_user_id),
            "qualifying_role_assignment_id": str(
                approved_closed_loop_case.navigator_role_assignment_id
            ),
            "qualifying_role_snapshot": "navigator",
            "decision": "approved",
            "authorized_at": approved_closed_loop_case.approved_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "reason": None,
        }
    ]


def test_workspace_keeps_revision_root_and_effective_states_explicit(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    original = closed_loop_session.get(ProposedChange, closed_loop_case.proposed_change_id)
    assert original is not None
    successor = ProposedChange(
        organization_id=original.organization_id,
        proposed_by_user_id=original.proposed_by_user_id,
        proposed_at=datetime.now(UTC),
        change_type=original.change_type,
        proposed_value={"title": "Revised synthetic transportation support"},
        rationale="Synthetic revision.",
        value_schema_id=original.value_schema_id,
        value_schema_version=original.value_schema_version,
        supersedes_proposed_change_id=original.id,
        navigation_task_id=original.navigation_task_id,
        approval_policy_id=original.approval_policy_id,
        approval_policy_version=original.approval_policy_version,
        deterministic_severity_threshold_snapshot=(
            original.deterministic_severity_threshold_snapshot
        ),
        allow_self_approval_snapshot=original.allow_self_approval_snapshot,
        required_approval_count_snapshot=original.required_approval_count_snapshot,
        required_approver_role_snapshot=original.required_approver_role_snapshot,
    )
    closed_loop_session.add(successor)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(closed_loop_case),
    )
    proposals = {
        proposal["id"]: proposal
        for proposal in response.json()["tasks"][0]["proposals"]
    }
    assert proposals[str(original.id)]["state"] == "superseded"
    assert proposals[str(original.id)]["reviewable"] is False
    assert proposals[str(successor.id)]["state"] == "pending"
    assert proposals[str(successor.id)]["root_proposal_id"] == str(original.id)


def test_workspace_keeps_unsupported_historical_schema_visible_but_non_actionable(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    original = closed_loop_session.get(ProposedChange, closed_loop_case.proposed_change_id)
    assert original is not None
    legacy = ProposedChange(
        organization_id=original.organization_id,
        proposed_by_user_id=original.proposed_by_user_id,
        proposed_at=datetime.now(UTC),
        change_type=original.change_type,
        proposed_value={"title": "Unsupported historical proposal"},
        rationale="Synthetic unsupported-history fixture.",
        value_schema_id=original.value_schema_id,
        value_schema_version=original.value_schema_version,
        navigation_task_id=original.navigation_task_id,
        approval_policy_id=original.approval_policy_id,
        approval_policy_version=original.approval_policy_version,
        deterministic_severity_threshold_snapshot=(
            original.deterministic_severity_threshold_snapshot
        ),
        allow_self_approval_snapshot=original.allow_self_approval_snapshot,
        required_approval_count_snapshot=original.required_approval_count_snapshot,
        required_approver_role_snapshot=original.required_approver_role_snapshot,
    )
    closed_loop_session.add(legacy)
    closed_loop_session.flush()
    nested = closed_loop_session.begin_nested()
    try:
        closed_loop_session.execute(
            text("ALTER TABLE proposed_change DISABLE TRIGGER USER")
        )
        closed_loop_session.execute(
            text(
                "ALTER TABLE proposed_change "
                "DROP CONSTRAINT fk_proposed_change_value_schema"
            )
        )
        closed_loop_session.execute(
            text(
                "UPDATE proposed_change SET value_schema_version = 99 "
                "WHERE id = :proposal_id"
            ),
            {"proposal_id": legacy.id},
        )
        closed_loop_session.execute(
            text("ALTER TABLE proposed_change ENABLE TRIGGER USER")
        )
        closed_loop_session.expire(legacy)

        response = closed_loop_client.request(
            "GET",
            f"/v1/navigator/needs/{closed_loop_case.reported_need_id}/workspace",
            actor=_navigator(closed_loop_case),
        )
        projected = next(
            proposal
            for proposal in response.json()["tasks"][0]["proposals"]
            if proposal["id"] == str(legacy.id)
        )
        assert projected["value_schema_version"] == 99
        assert projected["supported"] is False
        assert projected["reviewable"] is False
        assert projected["execution_authorized"] is False
        assert projected["unsupported_reason"] == "Unsupported task proposal schema."
    finally:
        nested.rollback()


def test_reopened_need_labels_stored_context_as_inherited_without_source_id(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    outcome = Outcome(
        organization_id=closed_loop_case.organization_id,
        patient_id=closed_loop_case.patient_id,
        reported_need_id=closed_loop_case.reported_need_id,
        recorded_by_user_id=closed_loop_case.navigator_user_id,
        disposition=OutcomeDisposition.RESOLVED,
        note="Synthetic closure before recurrence.",
        idempotency_key=f"workspace-recurrence-{closed_loop_case.reported_need_id}",
        recorded_at=datetime.now(UTC),
    )
    closed_loop_session.add(outcome)
    closed_loop_session.flush()
    recurrence = ReportedNeed(
        organization_id=closed_loop_case.organization_id,
        patient_id=closed_loop_case.patient_id,
        care_episode_id=closed_loop_case.care_episode_id,
        reopened_from_need_id=closed_loop_case.reported_need_id,
        kind="transportation",
        status=NeedStatus.OPEN,
        evidence=[
            {
                "field": "reason",
                "text": "Synthetic transportation need recurred.",
            }
        ],
    )
    closed_loop_session.add(recurrence)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{recurrence.id}/workspace",
        actor=_navigator(closed_loop_case),
    )
    assert response.status_code == 200, response.text
    evidence = response.json()["evidence"]
    assert evidence == [
        {
            "source_submission_id": None,
            "field_identifier": "reason",
            "value_present": False,
            "value": None,
            "display_text": "Synthetic transportation need recurred.",
            "provenance_kind": "inherited_history",
        }
    ]
