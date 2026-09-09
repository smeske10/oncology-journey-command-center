from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import CurrentActor, Role
from app.db.models import (
    NavigationTask,
    Outcome,
    PatientIdentityLink,
    ReportedNeed,
    RoleAssignment,
    User,
)
from app.domain.enums import NavigationTaskStatus, UserRole
from app.domain.navigation_tasks import (
    claim_navigation_task,
    complete_navigation_task,
    start_navigation_task,
)


def _patient(case: Any) -> CurrentActor:
    return CurrentActor(
        user_id=case.patient_user_id,
        organization_id=case.organization_id,
        role=Role.SUPPORTING_ACTOR,
        patient_id=case.patient_id,
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


def test_patient_lists_and_answers_their_completed_task_follow_up(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    assert completion.follow_up_request_id is not None
    assert completion.completed_at is not None

    pending = closed_loop_client.request(
        "GET",
        "/v1/patient/follow-ups",
        actor=_patient(approved_closed_loop_case),
    )
    assert pending.status_code == 200, pending.text
    assert pending.json() == {
        "items": [
            {
                "request_id": str(completion.follow_up_request_id),
                "need_id": str(approved_closed_loop_case.reported_need_id),
                "task_id": str(approved_closed_loop_case.navigation_task_id),
                "requested_at": completion.completed_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "prompt_version": 1,
                "prompt": "Did this navigation support address your reported need?",
                "status": "awaiting_response",
                "response": None,
                "note": None,
                "responded_at": None,
            }
        ]
    }

    answered = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": "resolved", "note": "  The synthetic need is addressed.  "},
    )
    assert answered.status_code == 201, answered.text
    result = answered.json()
    assert result["request_id"] == str(completion.follow_up_request_id)
    assert result["response"] == "resolved"
    assert result["note"] == "The synthetic need is addressed."
    assert result["submitted_at"] is not None
    assert result["replayed"] is False

    refreshed = closed_loop_client.request(
        "GET",
        "/v1/patient/follow-ups",
        actor=_patient(approved_closed_loop_case),
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["items"][0]["status"] == "answered"
    assert refreshed.json()["items"][0]["response"] == "resolved"
    assert refreshed.json()["items"][0]["note"] == (
        "The synthetic need is addressed."
    )


@pytest.mark.parametrize("response_value", ["resolved", "unresolved", "still_needs_help"])
def test_all_controlled_response_values_are_accepted_without_closing_need(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    response_value: str,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    response = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": response_value, "note": "   "},
    )

    assert response.status_code == 201, response.text
    assert response.json()["response"] == response_value
    assert response.json()["note"] is None
    assert (
        closed_loop_session.scalar(
            select(Outcome).where(
                Outcome.reported_need_id == approved_closed_loop_case.reported_need_id
            )
        )
        is None
    )
    task = closed_loop_session.get(
        NavigationTask, approved_closed_loop_case.navigation_task_id
    )
    need = closed_loop_session.get(
        ReportedNeed, approved_closed_loop_case.reported_need_id
    )
    assert task is not None and task.status is NavigationTaskStatus.COMPLETED
    assert need is not None and need.status.value == "in_progress"


def test_blank_note_is_normalized_before_length_validation(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    response = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": "resolved", "note": " " * 2001},
    )
    assert response.status_code == 201, response.text
    assert response.json()["note"] is None


def test_matching_response_replays_after_closure_with_same_identity_and_time(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    actor = _patient(approved_closed_loop_case)
    first = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=actor,
        json={"response": "unresolved", "note": None},
    )
    assert first.status_code == 201, first.text
    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=CurrentActor(
            user_id=approved_closed_loop_case.navigator_user_id,
            organization_id=approved_closed_loop_case.organization_id,
            role=Role.NAVIGATOR,
        ),
        headers={"Idempotency-Key": f"follow-up-replay-{uuid4()}"},
        json={"disposition": "closed_unresolved", "note": None},
    )
    assert outcome.status_code == 201, outcome.text

    replay = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=actor,
        json={"response": "unresolved", "note": "   "},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["submitted_at"] == first.json()["submitted_at"]


def test_changed_response_content_conflicts_after_first_answer(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    actor = _patient(approved_closed_loop_case)
    first = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=actor,
        json={"response": "resolved", "note": None},
    )
    assert first.status_code == 201, first.text
    changed = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=actor,
        json={"response": "still_needs_help", "note": None},
    )

    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"]["code"] == "follow_up_already_answered"


def test_same_response_from_a_different_linked_author_conflicts(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    first = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": "resolved", "note": None},
    )
    assert first.status_code == 201, first.text
    old_link = closed_loop_session.get(
        PatientIdentityLink, approved_closed_loop_case.patient_identity_link_id
    )
    assert old_link is not None
    old_link.revoked_at = datetime.now(UTC) + timedelta(seconds=1)
    closed_loop_session.flush()

    other = User(
        email=f"other-patient-author-{uuid4()}@example.test",
        display_name="Other synthetic supporting actor",
    )
    closed_loop_session.add(other)
    closed_loop_session.flush()
    other.primary_organization_id = approved_closed_loop_case.organization_id
    role = RoleAssignment(
        organization_id=approved_closed_loop_case.organization_id,
        user_id=other.id,
        role=UserRole.SUPPORTING_ACTOR,
        granted_at=datetime.now(UTC) - timedelta(hours=1),
    )
    link = PatientIdentityLink(
        organization_id=approved_closed_loop_case.organization_id,
        user_id=other.id,
        patient_id=approved_closed_loop_case.patient_id,
        linked_at=datetime.now(UTC) - timedelta(hours=1),
    )
    closed_loop_session.add_all([role, link])
    closed_loop_session.flush()

    changed_author = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=CurrentActor(
            user_id=other.id,
            organization_id=approved_closed_loop_case.organization_id,
            role=Role.SUPPORTING_ACTOR,
            patient_id=approved_closed_loop_case.patient_id,
        ),
        json={"response": "resolved", "note": None},
    )
    assert changed_author.status_code == 409, changed_author.text
    assert changed_author.json()["detail"]["code"] == "follow_up_already_answered"


def test_unanswered_closed_request_is_unavailable_and_rejects_late_first_response(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    navigator = CurrentActor(
        user_id=approved_closed_loop_case.navigator_user_id,
        organization_id=approved_closed_loop_case.organization_id,
        role=Role.NAVIGATOR,
    )
    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=navigator,
        headers={"Idempotency-Key": f"early-close-{uuid4()}"},
        json={"disposition": "resolved", "note": None},
    )
    assert outcome.status_code == 201, outcome.text
    listed = closed_loop_client.request(
        "GET",
        "/v1/patient/follow-ups",
        actor=_patient(approved_closed_loop_case),
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["status"] == "unavailable_need_closed"

    late = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": "resolved", "note": None},
    )
    assert late.status_code == 409, late.text
    assert late.json()["detail"]["code"] == "need_closed"


def test_unlinked_revoked_or_wrong_role_actor_cannot_answer(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    unlinked = CurrentActor(
        user_id=approved_closed_loop_case.patient_user_id,
        organization_id=approved_closed_loop_case.organization_id,
        role=Role.SUPPORTING_ACTOR,
        patient_id=None,
    )
    denied = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=unlinked,
        json={"response": "resolved", "note": None},
    )
    assert denied.status_code == 403
    wrong_role = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=CurrentActor(
            user_id=approved_closed_loop_case.navigator_user_id,
            organization_id=approved_closed_loop_case.organization_id,
            role=Role.NAVIGATOR,
        ),
        json={"response": "resolved", "note": None},
    )
    assert wrong_role.status_code == 403


@pytest.mark.parametrize("revoked_kind", ["link", "role"])
def test_revoked_link_or_role_is_rechecked_before_response(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    revoked_kind: str,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    if revoked_kind == "link":
        record = closed_loop_session.get(
            PatientIdentityLink, approved_closed_loop_case.patient_identity_link_id
        )
    else:
        record = closed_loop_session.get(
            RoleAssignment, approved_closed_loop_case.patient_role_assignment_id
        )
    assert record is not None
    record.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
    closed_loop_session.flush()

    denied = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": "resolved", "note": None},
    )
    assert denied.status_code == 403


def test_foreign_request_is_not_disclosed_and_identity_fields_are_forbidden(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    foreign = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(closed_loop_case),
        json={"response": "resolved", "note": None},
    )
    assert foreign.status_code == 404


def test_response_body_rejects_injected_author_link_or_timestamp(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    response = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={
            "response": "resolved",
            "note": None,
            "submitted_by_user_id": str(approved_closed_loop_case.navigator_user_id),
            "patient_identity_link_id": str(
                approved_closed_loop_case.patient_identity_link_id
            ),
            "submitted_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 422


def test_follow_up_note_rejects_phi_like_contact_content(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    completion = _complete_task(closed_loop_session, approved_closed_loop_case)
    response = closed_loop_client.request(
        "POST",
        f"/v1/patient/follow-ups/{completion.follow_up_request_id}/responses",
        actor=_patient(approved_closed_loop_case),
        json={"response": "resolved", "note": "Call 202-555-0199."},
    )
    assert response.status_code == 422
    assert "public synthetic demo" in response.text


def test_newly_exposed_outcome_note_rejects_phi_like_contact_content(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=CurrentActor(
            user_id=approved_closed_loop_case.navigator_user_id,
            organization_id=approved_closed_loop_case.organization_id,
            role=Role.NAVIGATOR,
        ),
        headers={"Idempotency-Key": f"phi-outcome-{uuid4()}"},
        json={
            "disposition": "closed_unresolved",
            "note": "Email patient@example.com.",
        },
    )
    assert response.status_code == 422
    assert "public synthetic demo" in response.text
