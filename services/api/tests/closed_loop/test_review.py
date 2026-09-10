from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth.models import CurrentActor, Role
from app.db.models import ProposedChange, RoleAssignment
from app.domain.enums import UserRole


def _navigator(case: Any) -> CurrentActor:
    return CurrentActor(
        user_id=case.navigator_user_id,
        organization_id=case.organization_id,
        role=Role.NAVIGATOR,
    )


def test_decision_can_resolve_current_qualifying_role_when_id_is_omitted(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={"decision": "approved", "reason": None},
    )

    assert response.status_code == 201, response.text
    decision = response.json()
    assert decision["qualifying_role_assignment_id"] == str(
        closed_loop_case.navigator_role_assignment_id
    )
    assert decision["authorized_by_user_id"] == str(
        closed_loop_case.navigator_user_id
    )
    assert decision["proposal_state"] == "approved"
    assert decision["applied"] is False


def test_approval_reason_rejects_contact_details_in_public_demo(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={
            "decision": "declined",
            "reason": "Call the patient at 202-555-0199.",
        },
    )

    assert response.status_code == 422
    assert "public synthetic demo" in response.text


def test_supplied_qualifying_role_id_remains_compatible(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={
            "decision": "declined",
            "qualifying_role_assignment_id": str(
                closed_loop_case.navigator_role_assignment_id
            ),
            "reason": "The synthetic action needs revision.",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["proposal_state"] == "declined"
    assert response.json()["applied"] is False


def test_supplied_role_id_still_rejects_wrong_user(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(
                closed_loop_case.proposer_role_assignment_id
            ),
            "reason": None,
        },
    )
    assert response.status_code == 403


def test_supplied_role_id_still_rejects_foreign_tenant(
    closed_loop_client: Any,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(
                approved_closed_loop_case.navigator_role_assignment_id
            ),
            "reason": None,
        },
    )
    assert response.status_code == 403


def test_omitted_role_resolution_rejects_revoked_or_missing_authority(
    closed_loop_session: Any,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    role = closed_loop_session.get(
        RoleAssignment, closed_loop_case.navigator_role_assignment_id
    )
    assert role is not None
    role.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={"decision": "approved", "reason": None},
    )
    assert response.status_code == 403


def test_supplied_role_id_still_rejects_revoked_authority(
    closed_loop_session: Any,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    role = closed_loop_session.get(
        RoleAssignment, closed_loop_case.navigator_role_assignment_id
    )
    assert role is not None
    role.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(role.id),
            "reason": None,
        },
    )

    assert response.status_code == 403


def test_omitted_role_resolution_uses_newest_eligible_interval_deterministically(
    closed_loop_session: Any,
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    now = datetime.now(UTC)
    existing = closed_loop_session.get(
        RoleAssignment, closed_loop_case.navigator_role_assignment_id
    )
    assert existing is not None
    existing.revoked_at = now + timedelta(days=1)
    newer = RoleAssignment(
        organization_id=closed_loop_case.organization_id,
        user_id=closed_loop_case.navigator_user_id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(minutes=1),
        revoked_at=now + timedelta(days=1),
    )
    closed_loop_session.add(newer)
    closed_loop_session.flush()

    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={"decision": "approved", "reason": None},
    )
    assert response.status_code == 201, response.text
    assert response.json()["qualifying_role_assignment_id"] == str(newer.id)


def test_omitted_role_resolution_does_not_bypass_self_approval_policy(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    proposer = CurrentActor(
        user_id=closed_loop_case.proposer_user_id,
        organization_id=closed_loop_case.organization_id,
        role=Role.NAVIGATOR,
    )
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=proposer,
        json={"decision": "approved", "reason": None},
    )
    assert response.status_code == 403


def test_decision_on_superseded_proposal_returns_stale_conflict(
    closed_loop_session: Any,
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
        proposed_value={"title": "Revised synthetic task"},
        rationale="Synthetic stale-decision fixture.",
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
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{original.id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={"decision": "approved", "reason": None},
    )
    assert response.status_code == 409


def test_phi_guard_does_not_scan_identifiers_or_timestamps_as_contact_prose(
    closed_loop_client: Any,
    closed_loop_case: Any,
) -> None:
    response = closed_loop_client.request(
        "POST",
        (
            "/v1/navigator/proposed-changes/"
            f"{closed_loop_case.proposed_change_id}/decisions"
        ),
        actor=_navigator(closed_loop_case),
        json={
            "decision": "declined",
            "reason": (
                f"Synthetic proposal {closed_loop_case.proposed_change_id} "
                "was reviewed at 2026-09-09T12:34:56Z."
            ),
        },
    )
    assert response.status_code == 201, response.text
