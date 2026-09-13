from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.models import CurrentActor, Role
from app.db.models import CheckInSubmission, NavigationTask
from app.domain.enums import CheckInStatus, NavigationTaskStatus, SubmissionSource
from app.domain.navigation_tasks import (
    claim_navigation_task,
    complete_navigation_task,
    start_navigation_task,
)
from app.domain.needs import reopen_need
from tests.database_support import user_triggers_disabled


def _patient(case: Any) -> CurrentActor:
    return CurrentActor(
        user_id=case.patient_user_id,
        organization_id=case.organization_id,
        role=Role.SUPPORTING_ACTOR,
        patient_id=case.patient_id,
    )


def _navigator(case: Any) -> CurrentActor:
    return CurrentActor(
        user_id=case.navigator_user_id,
        organization_id=case.organization_id,
        role=Role.NAVIGATOR,
    )


def _complete_task(session: Session, case: Any) -> Any:
    claim_navigation_task(
        session,
        organization_id=case.organization_id,
        task_id=case.navigation_task_id,
        actor_user_id=case.navigator_user_id,
        proposed_change_id=case.proposed_change_id,
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    start_navigation_task(
        session,
        organization_id=case.organization_id,
        task_id=case.navigation_task_id,
        actor_user_id=case.navigator_user_id,
    )
    return complete_navigation_task(
        session,
        organization_id=case.organization_id,
        task_id=case.navigation_task_id,
        actor_user_id=case.navigator_user_id,
    )


def test_patient_timeline_projects_own_check_in_and_need_with_typed_sources(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "GET",
        "/v1/patient/journey-timeline",
        actor=_patient(approved_closed_loop_case),
    )

    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert [event["kind"] for event in events] == [
        "check_in_submitted",
        "need_reported",
    ]
    assert events[0]["source_type"] == "check_in_submission"
    assert events[0]["source_id"] == str(
        approved_closed_loop_case.source_submission_id
    )
    assert events[0]["need_id"] is None
    assert events[1]["source_type"] == "reported_need"
    assert events[1]["source_id"] == str(
        approved_closed_loop_case.reported_need_id
    )
    assert events[1]["need_id"] == str(approved_closed_loop_case.reported_need_id)


def test_full_timelines_allowlist_each_audience_and_keep_closed_workspace_readable(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    source = closed_loop_session.get(
        CheckInSubmission, approved_closed_loop_case.source_submission_id
    )
    assert source is not None
    correction = CheckInSubmission(
        organization_id=source.organization_id,
        patient_id=source.patient_id,
        care_episode_id=source.care_episode_id,
        check_in_definition_id=source.check_in_definition_id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": False},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=approved_closed_loop_case.patient_user_id,
        submitted_at=source.submitted_at + timedelta(minutes=1),
        supersedes_submission_id=source.id,
    )
    cancelled_task = NavigationTask(
        organization_id=approved_closed_loop_case.organization_id,
        patient_id=approved_closed_loop_case.patient_id,
        reported_need_id=approved_closed_loop_case.reported_need_id,
        title="INTERNAL_TASK_TITLE_SENTINEL",
        status=NavigationTaskStatus.OPEN,
    )
    closed_loop_session.add_all([correction, cancelled_task])
    closed_loop_session.flush()
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    assert completion.follow_up_request_id is not None
    answered = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={
            "response": "still_needs_help",
            "note": "Synthetic patient-visible note.",
        },
    )
    assert answered.status_code == 201, answered.text
    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=_navigator(approved_closed_loop_case),
        headers={"Idempotency-Key": f"timeline-{uuid4()}"},
        json={
            "disposition": "closed_unresolved",
            "note": "INTERNAL_OUTCOME_NOTE_SENTINEL",
        },
    )
    assert outcome.status_code == 201, outcome.text
    with user_triggers_disabled(closed_loop_session, "audit_event"):
        closed_loop_session.execute(
            text(
                "UPDATE audit_event SET payload = payload || "
                "'{\"internal\": \"RAW_AUDIT_PAYLOAD_SENTINEL\"}'::jsonb "
                "WHERE organization_id = :organization_id "
                "AND entity_type = 'navigation_task'"
            ),
            {"organization_id": approved_closed_loop_case.organization_id},
        )

    patient = closed_loop_client.request(
        "GET",
        "/v1/patient/journey-timeline",
        actor=_patient(approved_closed_loop_case),
    )
    assert patient.status_code == 200, patient.text
    patient_payload = patient.text
    patient_kinds = {event["kind"] for event in patient.json()["events"]}
    assert patient_kinds == {
        "check_in_submitted",
        "check_in_corrected",
        "need_reported",
        "task_claimed",
        "task_started",
        "task_completed",
        "follow_up_requested",
        "follow_up_responded",
        "outcome_recorded",
        "task_cancelled",
    }
    assert "Synthetic patient-visible note." in patient_payload
    for forbidden in (
        "INTERNAL_TASK_TITLE_SENTINEL",
        "Arrange synthetic transportation support",
        "Synthetic transportation support is requested.",
        "INTERNAL_OUTCOME_NOTE_SENTINEL",
        "RAW_AUDIT_PAYLOAD_SENTINEL",
        str(approved_closed_loop_case.proposed_change_id),
        str(approved_closed_loop_case.navigator_user_id),
        str(approved_closed_loop_case.navigator_role_assignment_id),
    ):
        assert forbidden not in patient_payload

    workspace = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(approved_closed_loop_case),
    )
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["need"]["effective_state"] == "closed"
    navigator_kinds = {event["kind"] for event in workspace.json()["timeline"]}
    assert navigator_kinds == {
        "check_in_submitted",
        "check_in_corrected",
        "need_reported",
        "proposal_created",
        "proposal_decided",
        "task_claimed",
        "task_started",
        "task_completed",
        "follow_up_requested",
        "follow_up_responded",
        "outcome_recorded",
        "task_cancelled",
    }
    assert "INTERNAL_OUTCOME_NOTE_SENTINEL" in workspace.text
    ordered_kinds = [event["kind"] for event in workspace.json()["timeline"]]
    assert ordered_kinds.index("outcome_recorded") < ordered_kinds.index(
        "task_cancelled"
    )


def test_patient_timeline_orders_equal_timestamp_sources_by_uuid_and_excludes_foreign(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    closed_loop_case: Any,
) -> None:
    source = closed_loop_session.get(
        CheckInSubmission, approved_closed_loop_case.source_submission_id
    )
    assert source is not None
    tied_at = source.submitted_at + timedelta(minutes=2)
    lower_id = UUID("00000000-0000-4000-8000-000000000001")
    higher_id = UUID("00000000-0000-4000-8000-000000000002")
    for submission_id in (higher_id, lower_id):
        closed_loop_session.add(
            CheckInSubmission(
                id=submission_id,
                organization_id=source.organization_id,
                patient_id=source.patient_id,
                care_episode_id=source.care_episode_id,
                check_in_definition_id=source.check_in_definition_id,
                status=CheckInStatus.SUBMITTED,
                answers={"transportation": True},
                submission_source=SubmissionSource.PATIENT,
                submitted_by_user_id=approved_closed_loop_case.patient_user_id,
                submitted_at=tied_at,
            )
        )
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "GET",
        "/v1/patient/journey-timeline",
        actor=_patient(approved_closed_loop_case),
    )
    assert response.status_code == 200, response.text
    tied_ids = [
        event["source_id"]
        for event in response.json()["events"]
        if event["occurred_at"] == tied_at.isoformat().replace("+00:00", "Z")
    ]
    assert tied_ids == [str(lower_id), str(higher_id)]
    assert str(closed_loop_case.source_submission_id) not in response.text
    assert str(closed_loop_case.reported_need_id) not in response.text


def test_reopened_need_is_labeled_inherited_without_fabricated_submission(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=_navigator(approved_closed_loop_case),
        headers={"Idempotency-Key": f"reopen-timeline-{uuid4()}"},
        json={"disposition": "closed_unresolved", "note": None},
    )
    assert outcome.status_code == 201, outcome.text
    reopened = reopen_need(
        closed_loop_session,
        organization_id=approved_closed_loop_case.organization_id,
        predecessor_need_id=approved_closed_loop_case.reported_need_id,
    )

    workspace = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{reopened.id}/workspace",
        actor=_navigator(approved_closed_loop_case),
    )
    assert workspace.status_code == 200, workspace.text
    timeline = workspace.json()["timeline"]
    assert [event["kind"] for event in timeline] == [
        "check_in_submitted",
        "need_reported",
        "need_reported",
    ]
    assert timeline[0]["detail"]["inherited"] is True
    assert timeline[0]["source_type"] == "check_in_submission"
    assert timeline[-1]["detail"]["inherited"] is True
    assert timeline[-1]["source_type"] == "reported_need"
    assert all(item["source_submission_id"] is None for item in workspace.json()["evidence"])
