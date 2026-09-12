from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth.models import CurrentActor, Role
from app.db.models import (
    ApprovalDecision,
    AuditEvent,
    FollowUpRequest,
    NavigationTask,
    NavigationTaskResource,
    ProposedChange,
    Resource,
    RoleAssignment,
)
from app.domain.enums import (
    ApprovalDecisionValue,
    NavigationTaskStatus,
    UserRole,
)


def _navigator(case: Any, *, user_id: Any | None = None) -> CurrentActor:
    return CurrentActor(
        user_id=user_id or case.navigator_user_id,
        organization_id=case.organization_id,
        role=Role.NAVIGATOR,
    )


def _claim(
    client: Any,
    case: Any,
    *,
    due_at: datetime,
    actor: CurrentActor | None = None,
    proposed_change_id: Any | None = None,
) -> Any:
    return client.request(
        "POST",
        f"/v1/navigator/tasks/{case.navigation_task_id}/claim",
        actor=actor or _navigator(case),
        json={
            "proposed_change_id": str(proposed_change_id or case.proposed_change_id),
            "due_at": due_at.isoformat(),
        },
    )


def _assert_conflict(response: Any, code: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code


def test_navigator_can_claim_exact_approved_task_proposal(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    due_at = datetime.now(UTC) + timedelta(days=2)

    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/claim",
        actor=_navigator(approved_closed_loop_case),
        json={
            "proposed_change_id": str(approved_closed_loop_case.proposed_change_id),
            "due_at": due_at.isoformat(),
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result == {
        "task_id": str(approved_closed_loop_case.navigation_task_id),
        "need_id": str(approved_closed_loop_case.reported_need_id),
        "status": "assigned",
        "assignee_user_id": str(approved_closed_loop_case.navigator_user_id),
        "due_at": due_at.isoformat().replace("+00:00", "Z"),
        "authorized_proposed_change_id": str(
            approved_closed_loop_case.proposed_change_id
        ),
        "completed_at": None,
        "follow_up_request_id": None,
        "replayed": False,
    }
    task = closed_loop_session.get(
        NavigationTask, approved_closed_loop_case.navigation_task_id
    )
    assert task is not None
    assert task.title == "Arrange synthetic transportation support"
    assert task.status is NavigationTaskStatus.ASSIGNED
    assert task.assignee_user_id == approved_closed_loop_case.navigator_user_id
    assert task.authorized_proposed_change_id == (
        approved_closed_loop_case.proposed_change_id
    )
    assert task.due_at == due_at
    assert (
        closed_loop_session.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == approved_closed_loop_case.organization_id,
                AuditEvent.entity_id == task.id,
                AuditEvent.event_type == "navigation_task_claimed",
            )
        )
        is not None
    )
    assert (
        closed_loop_session.scalar(
            select(FollowUpRequest).where(
                FollowUpRequest.organization_id
                == approved_closed_loop_case.organization_id,
                FollowUpRequest.navigation_task_id == task.id,
            )
        )
        is None
    )


def test_assignee_can_start_and_complete_with_database_authored_follow_up(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    actor = _navigator(approved_closed_loop_case)
    due_at = datetime.now(UTC) + timedelta(days=2)
    claim = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/claim",
        actor=actor,
        json={
            "proposed_change_id": str(approved_closed_loop_case.proposed_change_id),
            "due_at": due_at.isoformat(),
        },
    )
    assert claim.status_code == 200, claim.text

    started = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=actor,
        json={},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"
    assert started.json()["replayed"] is False

    completed = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/complete",
        actor=actor,
        json={},
    )
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["status"] == "completed"
    assert result["completed_at"] is not None
    assert result["follow_up_request_id"] is not None
    assert result["replayed"] is False

    request = closed_loop_session.scalar(
        select(FollowUpRequest).where(
            FollowUpRequest.organization_id
            == approved_closed_loop_case.organization_id,
            FollowUpRequest.navigation_task_id
            == approved_closed_loop_case.navigation_task_id,
        )
    )
    assert request is not None
    assert str(request.id) == result["follow_up_request_id"]
    assert request.requested_at.isoformat().replace("+00:00", "Z") == result[
        "completed_at"
    ]
    events = closed_loop_session.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == approved_closed_loop_case.organization_id,
            AuditEvent.entity_id == approved_closed_loop_case.navigation_task_id,
            AuditEvent.event_type.in_(
                (
                    "navigation_task_claimed",
                    "navigation_task_started",
                    "navigation_task_completed",
                )
            ),
        )
    ).all()
    assert len(events) == 3


def test_claim_replay_is_exact_and_does_not_duplicate_audit(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    due_at = datetime.now(UTC) + timedelta(days=2)
    first = _claim(closed_loop_client, approved_closed_loop_case, due_at=due_at)
    replay = _claim(closed_loop_client, approved_closed_loop_case, due_at=due_at)

    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["task_id"] == first.json()["task_id"]
    event_count = len(
        closed_loop_session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == approved_closed_loop_case.organization_id,
                AuditEvent.entity_id == approved_closed_loop_case.navigation_task_id,
                AuditEvent.event_type == "navigation_task_claimed",
            )
        ).all()
    )
    assert event_count == 1

    changed = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=due_at + timedelta(hours=1),
    )
    _assert_conflict(changed, "task_claim_mismatch")
    task = closed_loop_session.get(
        NavigationTask, approved_closed_loop_case.navigation_task_id
    )
    assert task is not None
    assert task.due_at == due_at


@pytest.mark.parametrize(
    ("due_at", "payload_due"),
    [
        (datetime.now(UTC) - timedelta(minutes=1), None),
        (None, "2030-01-01T12:00:00"),
    ],
)
def test_initial_claim_rejects_past_or_naive_due_without_partial_write(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    due_at: datetime | None,
    payload_due: str | None,
) -> None:
    if payload_due is None:
        assert due_at is not None
        payload_due = due_at.isoformat()
    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/claim",
        actor=_navigator(approved_closed_loop_case),
        json={
            "proposed_change_id": str(approved_closed_loop_case.proposed_change_id),
            "due_at": payload_due,
        },
    )

    assert response.status_code == 422, response.text


def test_claim_rejects_pending_proposal(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    pending = _claim(
        closed_loop_client,
        closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    _assert_conflict(pending, "proposal_not_approved")


def test_claim_rejects_superseded_proposal(
    closed_loop_session: Session,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    due_at = datetime.now(UTC) + timedelta(days=2)
    original = closed_loop_session.get(
        ProposedChange, closed_loop_case.proposed_change_id
    )
    assert original is not None
    successor = ProposedChange(
        organization_id=original.organization_id,
        proposed_by_user_id=original.proposed_by_user_id,
        proposed_at=datetime.now(UTC),
        change_type=original.change_type,
        proposed_value={"title": "Revised synthetic task"},
        rationale="Synthetic revision makes the predecessor stale.",
        value_schema_id=original.value_schema_id,
        value_schema_version=1,
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
    superseded = _claim(
        closed_loop_client, closed_loop_case, due_at=due_at
    )
    _assert_conflict(superseded, "proposal_not_approved")


def test_claim_rejects_approved_proposal_for_another_task(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    original = closed_loop_session.get(
        ProposedChange, approved_closed_loop_case.proposed_change_id
    )
    assert original is not None
    second_task = NavigationTask(
        organization_id=approved_closed_loop_case.organization_id,
        patient_id=approved_closed_loop_case.patient_id,
        reported_need_id=approved_closed_loop_case.reported_need_id,
        title="Second placeholder",
        status=NavigationTaskStatus.OPEN,
    )
    closed_loop_session.add(second_task)
    closed_loop_session.flush()
    wrong_target = ProposedChange(
        organization_id=original.organization_id,
        proposed_by_user_id=original.proposed_by_user_id,
        proposed_at=datetime.now(UTC),
        change_type=original.change_type,
        proposed_value={"title": "Authorize only the second task"},
        rationale="A distinct exact task authorization.",
        value_schema_id=original.value_schema_id,
        value_schema_version=1,
        navigation_task_id=second_task.id,
        approval_policy_id=original.approval_policy_id,
        approval_policy_version=original.approval_policy_version,
        deterministic_severity_threshold_snapshot=(
            original.deterministic_severity_threshold_snapshot
        ),
        allow_self_approval_snapshot=original.allow_self_approval_snapshot,
        required_approval_count_snapshot=original.required_approval_count_snapshot,
        required_approver_role_snapshot=original.required_approver_role_snapshot,
    )
    closed_loop_session.add(wrong_target)
    closed_loop_session.flush()
    closed_loop_session.add(
        ApprovalDecision(
            organization_id=wrong_target.organization_id,
            proposed_change_id=wrong_target.id,
            authorized_by_user_id=approved_closed_loop_case.navigator_user_id,
            qualifying_role_assignment_id=(
                approved_closed_loop_case.navigator_role_assignment_id
            ),
            qualifying_role_snapshot=UserRole.NAVIGATOR,
            decision=ApprovalDecisionValue.APPROVED,
            authorized_at=datetime.now(UTC),
        )
    )
    closed_loop_session.flush()
    mismatched_target = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
        proposed_change_id=wrong_target.id,
    )
    _assert_conflict(mismatched_target, "proposal_not_approved")


def test_claim_accepts_an_exact_approved_v2_root_and_materialized_resources(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    original = closed_loop_session.get(
        ProposedChange, approved_closed_loop_case.proposed_change_id
    )
    assert original is not None
    resource = Resource(
        organization_id=approved_closed_loop_case.organization_id,
        name="Synthetic transport service",
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
        "match_rationale": "Matches the synthetic need.",
    }
    v2 = ProposedChange(
        organization_id=original.organization_id,
        proposed_by_user_id=original.proposed_by_user_id,
        proposed_at=datetime.now(UTC),
        change_type=original.change_type,
        proposed_value={"title": "Use approved v2 support", "resources": [snapshot]},
        rationale="Synthetic v2 authorization.",
        value_schema_id=original.value_schema_id,
        value_schema_version=2,
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
    closed_loop_session.add(v2)
    closed_loop_session.flush()
    closed_loop_session.add(
        NavigationTaskResource(
            organization_id=v2.organization_id,
            navigation_task_id=approved_closed_loop_case.navigation_task_id,
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
    closed_loop_session.add(
        ApprovalDecision(
            organization_id=v2.organization_id,
            proposed_change_id=v2.id,
            authorized_by_user_id=approved_closed_loop_case.navigator_user_id,
            qualifying_role_assignment_id=(
                approved_closed_loop_case.navigator_role_assignment_id
            ),
            qualifying_role_snapshot=UserRole.NAVIGATOR,
            decision=ApprovalDecisionValue.APPROVED,
            authorized_at=datetime.now(UTC),
        )
    )
    closed_loop_session.flush()

    response = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
        proposed_change_id=v2.id,
    )

    assert response.status_code == 200, response.text
    assert response.json()["authorized_proposed_change_id"] == str(v2.id)
    task = closed_loop_session.get(
        NavigationTask, approved_closed_loop_case.navigation_task_id
    )
    assert task is not None
    assert task.title == "Use approved v2 support"


def test_foreign_task_identifier_is_indistinguishable(
    closed_loop_client: Any,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    foreign = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{closed_loop_case.navigation_task_id}/claim",
        actor=_navigator(approved_closed_loop_case),
        json={
            "proposed_change_id": str(closed_loop_case.proposed_change_id),
            "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert foreign.status_code == 404


def test_unknown_task_identifier_is_indistinguishable(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    unknown = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{uuid4()}/claim",
        actor=_navigator(approved_closed_loop_case),
        json={
            "proposed_change_id": str(approved_closed_loop_case.proposed_change_id),
            "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert unknown.status_code == 404


def test_only_claimed_owner_can_start_task(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    due_at = datetime.now(UTC) + timedelta(days=2)
    claimed = _claim(closed_loop_client, approved_closed_loop_case, due_at=due_at)
    assert claimed.status_code == 200, claimed.text
    wrong_owner = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=_navigator(
            approved_closed_loop_case,
            user_id=approved_closed_loop_case.proposer_user_id,
        ),
        json={},
    )
    assert wrong_owner.status_code == 403


def test_revoked_navigator_cannot_claim(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    role = closed_loop_session.get(
        RoleAssignment, approved_closed_loop_case.navigator_role_assignment_id
    )
    assert role is not None
    role.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
    closed_loop_session.flush()

    denied = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    assert denied.status_code == 403


def test_unbound_task_cannot_start(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    unbound_start = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=_navigator(approved_closed_loop_case),
        json={},
    )
    _assert_conflict(unbound_start, "task_unbound")


def test_initial_claim_on_closed_need_conflicts_without_reopening_task(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=_navigator(approved_closed_loop_case),
        headers={"Idempotency-Key": f"closed-before-claim-{uuid4()}"},
        json={"disposition": "closed_unresolved", "note": None},
    )
    assert outcome.status_code == 201, outcome.text

    response = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    _assert_conflict(response, "need_closed")


def test_empty_task_commands_forbid_injected_fields(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=_navigator(approved_closed_loop_case),
        json={"owner_user_id": str(approved_closed_loop_case.proposer_user_id)},
    )
    assert response.status_code == 422


def test_completed_task_replays_claim_start_and_complete_after_closure_and_due(
    monkeypatch: pytest.MonkeyPatch,
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    import app.domain.navigation_tasks as navigation_tasks_domain

    actor = _navigator(approved_closed_loop_case)
    due_at = datetime.now(UTC) + timedelta(days=2)
    first_claim = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=due_at,
        actor=actor,
    )
    assert first_claim.status_code == 200, first_claim.text
    first_start = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=actor,
        json={},
    )
    assert first_start.status_code == 200, first_start.text
    first_complete = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/complete",
        actor=actor,
        json={},
    )
    assert first_complete.status_code == 200, first_complete.text
    original = first_complete.json()

    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=actor,
        headers={"Idempotency-Key": f"after-completion-{uuid4()}"},
        json={"disposition": "resolved", "note": None},
    )
    assert outcome.status_code == 201, outcome.text

    class LaterDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            later = due_at + timedelta(days=1)
            return later if tz is not None else later.replace(tzinfo=None)

    monkeypatch.setattr(navigation_tasks_domain, "datetime", LaterDateTime)
    replay_claim = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=due_at,
        actor=actor,
    )
    replay_start = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=actor,
        json={},
    )
    replay_complete = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/complete",
        actor=actor,
        json={},
    )

    for response in (replay_claim, replay_start, replay_complete):
        assert response.status_code == 200, response.text
        assert response.json()["replayed"] is True
        assert response.json()["status"] == "completed"
        assert response.json()["completed_at"] == original["completed_at"]
        assert response.json()["follow_up_request_id"] == original[
            "follow_up_request_id"
        ]
    requests = closed_loop_session.scalars(
        select(FollowUpRequest).where(
            FollowUpRequest.organization_id
            == approved_closed_loop_case.organization_id,
            FollowUpRequest.navigation_task_id
            == approved_closed_loop_case.navigation_task_id,
        )
    ).all()
    events = closed_loop_session.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == approved_closed_loop_case.organization_id,
            AuditEvent.entity_id == approved_closed_loop_case.navigation_task_id,
            AuditEvent.event_type.in_(
                (
                    "navigation_task_claimed",
                    "navigation_task_started",
                    "navigation_task_completed",
                )
            ),
        )
    ).all()
    assert len(requests) == 1
    assert len(events) == 3


def test_first_start_after_outcome_conflicts(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    actor = _navigator(approved_closed_loop_case)
    claimed = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
        actor=actor,
    )
    assert claimed.status_code == 200, claimed.text
    outcome = closed_loop_client.request(
        "POST",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/outcomes",
        actor=actor,
        headers={"Idempotency-Key": f"before-start-{uuid4()}"},
        json={"disposition": "closed_unresolved", "note": None},
    )
    assert outcome.status_code == 201, outcome.text
    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=actor,
        json={},
    )
    _assert_conflict(response, "need_closed")


def test_complete_requires_in_progress_and_claim_replay_requires_same_owner(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    due_at = datetime.now(UTC) + timedelta(days=2)
    claimed = _claim(closed_loop_client, approved_closed_loop_case, due_at=due_at)
    assert claimed.status_code == 200, claimed.text
    premature = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/complete",
        actor=_navigator(approved_closed_loop_case),
        json={},
    )
    _assert_conflict(premature, "task_state_conflict")


def test_claim_replay_rejects_different_owner(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    due_at = datetime.now(UTC) + timedelta(days=2)
    claimed = _claim(closed_loop_client, approved_closed_loop_case, due_at=due_at)
    assert claimed.status_code == 200, claimed.text
    response = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=due_at,
        actor=_navigator(
            approved_closed_loop_case,
            user_id=approved_closed_loop_case.proposer_user_id,
        ),
    )
    assert response.status_code == 403


def test_foreign_proposal_identifier_is_indistinguishable(
    closed_loop_client: Any,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
        proposed_change_id=closed_loop_case.proposed_change_id,
    )
    assert response.status_code == 404


def test_claim_body_forbids_injected_owner_or_organization(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/claim",
        actor=_navigator(approved_closed_loop_case),
        json={
            "proposed_change_id": str(approved_closed_loop_case.proposed_change_id),
            "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "assignee_user_id": str(approved_closed_loop_case.proposer_user_id),
            "organization_id": str(approved_closed_loop_case.organization_id),
        },
    )
    assert response.status_code == 422


def test_claim_normalizes_timezone_before_exact_replay_comparison(
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    due_utc = datetime.now(UTC) + timedelta(days=2)
    due_with_offset = due_utc.astimezone(UTC).replace(tzinfo=None).isoformat() + "-04:00"
    represented_instant = datetime.fromisoformat(due_with_offset).astimezone(UTC)
    first = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/claim",
        actor=_navigator(approved_closed_loop_case),
        json={
            "proposed_change_id": str(approved_closed_loop_case.proposed_change_id),
            "due_at": due_with_offset,
        },
    )
    assert first.status_code == 200, first.text
    replay = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=represented_instant,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True


def _set_legacy_unbound_task(session: Session, case: Any, status: str) -> None:
    completed_at = datetime.now(UTC) if status == "completed" else None
    cancelled_at = datetime.now(UTC) if status == "cancelled" else None
    session.execute(text("ALTER TABLE navigation_task DISABLE TRIGGER USER"))
    try:
        session.execute(
            text(
                "UPDATE navigation_task SET status = CAST(:status AS navigation_task_status), "
                "assignee_user_id = :assignee, due_at = :due_at, "
                "authorized_proposed_change_id = NULL, completed_at = :completed_at, "
                "cancelled_by_user_id = :cancelled_by, cancelled_at = :cancelled_at, "
                "cancellation_reason = CAST(:cancellation_reason AS task_cancellation_reason) "
                "WHERE id = :task_id"
            ),
            {
                "status": status,
                "assignee": case.navigator_user_id,
                "due_at": datetime.now(UTC) + timedelta(days=2),
                "completed_at": completed_at,
                "cancelled_by": case.navigator_user_id if status == "cancelled" else None,
                "cancelled_at": cancelled_at,
                "cancellation_reason": "need_closed" if status == "cancelled" else None,
                "task_id": case.navigation_task_id,
            },
        )
    finally:
        session.execute(text("ALTER TABLE navigation_task ENABLE TRIGGER USER"))
    session.flush()


def _legacy_history_snapshot(session: Session, task_id: Any) -> tuple[str, int]:
    row = session.scalar(
        text(
            "SELECT row_to_json(task)::text FROM navigation_task AS task "
            "WHERE id = :task_id"
        ),
        {"task_id": task_id},
    )
    audit_count = session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == task_id)
    )
    assert isinstance(row, str)
    assert isinstance(audit_count, int)
    return row, audit_count


@pytest.mark.parametrize(
    ("historical_status", "command"),
    [("assigned", "start"), ("in_progress", "complete")],
)
def test_historical_executing_task_without_binding_is_read_only(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    historical_status: str,
    command: str,
) -> None:
    _set_legacy_unbound_task(
        closed_loop_session,
        approved_closed_loop_case,
        historical_status,
    )
    closed_loop_session.commit()
    before = _legacy_history_snapshot(
        closed_loop_session, approved_closed_loop_case.navigation_task_id
    )

    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/{command}",
        actor=_navigator(approved_closed_loop_case),
        json={},
    )
    _assert_conflict(response, "task_unbound")
    assert _legacy_history_snapshot(
        closed_loop_session, approved_closed_loop_case.navigation_task_id
    ) == before


@pytest.mark.parametrize("historical_status", ["completed", "cancelled"])
def test_historical_terminal_unbound_task_is_display_only(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
    historical_status: str,
) -> None:
    _set_legacy_unbound_task(
        closed_loop_session,
        approved_closed_loop_case,
        historical_status,
    )
    closed_loop_session.commit()
    before = _legacy_history_snapshot(
        closed_loop_session, approved_closed_loop_case.navigation_task_id
    )

    response = closed_loop_client.request(
        "GET",
        f"/v1/navigator/needs/{approved_closed_loop_case.reported_need_id}/workspace",
        actor=_navigator(approved_closed_loop_case),
    )

    assert response.status_code == 200, response.text
    task = next(
        item
        for item in response.json()["tasks"]
        if item["id"] == str(approved_closed_loop_case.navigation_task_id)
    )
    assert task["status"] == historical_status
    assert task["authorized_proposed_change_id"] is None
    assert _legacy_history_snapshot(
        closed_loop_session, approved_closed_loop_case.navigation_task_id
    ) == before


def test_non_open_unbound_task_cannot_be_claimed(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    task = closed_loop_session.get(
        NavigationTask, approved_closed_loop_case.navigation_task_id
    )
    assert task is not None
    task.assignee_user_id = approved_closed_loop_case.navigator_user_id
    task.due_at = datetime.now(UTC) + timedelta(days=2)
    task.status = NavigationTaskStatus.ASSIGNED
    closed_loop_session.flush()
    claimed_due_at = task.due_at
    assert claimed_due_at is not None

    response = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=claimed_due_at,
    )
    _assert_conflict(response, "task_state_conflict")


def test_revocation_after_claim_blocks_first_start(
    closed_loop_session: Session,
    closed_loop_client: Any,
    approved_closed_loop_case: Any,
) -> None:
    claimed = _claim(
        closed_loop_client,
        approved_closed_loop_case,
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    assert claimed.status_code == 200, claimed.text
    role = closed_loop_session.get(
        RoleAssignment, approved_closed_loop_case.navigator_role_assignment_id
    )
    assert role is not None
    role.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "POST",
        f"/v1/navigator/tasks/{approved_closed_loop_case.navigation_task_id}/start",
        actor=_navigator(approved_closed_loop_case),
        json={},
    )
    assert response.status_code == 403
