from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.integrity import inspect_integrity
from app.db.models import FollowUpRequest, FollowUpResponse, NavigationTask, ProposedChange
from app.domain.enums import FollowUpResponseValue, NavigationTaskStatus


def _bind_and_complete(session: Session, case: Any) -> tuple[NavigationTask, FollowUpRequest]:
    task = session.get(NavigationTask, case.navigation_task_id)
    proposal = session.get(ProposedChange, case.proposed_change_id)
    assert task is not None
    assert proposal is not None
    task.authorized_proposed_change_id = proposal.id
    task.title = proposal.proposed_value["title"]
    task.assignee_user_id = case.navigator_user_id
    task.due_at = datetime.now(UTC) + timedelta(days=1)
    task.status = NavigationTaskStatus.ASSIGNED
    session.flush()
    task.status = NavigationTaskStatus.IN_PROGRESS
    session.flush()
    task.status = NavigationTaskStatus.COMPLETED
    session.flush()
    request = session.scalar(
        select(FollowUpRequest).where(FollowUpRequest.navigation_task_id == task.id)
    )
    assert request is not None
    return task, request


@contextmanager
def _corruption_savepoint(session: Session):
    """Keep deliberate corruption local while still allowing the auditor to inspect it."""
    nested = session.begin_nested()
    try:
        yield
    finally:
        nested.rollback()


def _categories(session: Session) -> set[str]:
    return {violation.category for violation in inspect_integrity(session)}


def test_closed_loop_integrity_accepts_valid_and_legacy_unbound_history(
    closed_loop_session: Session,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    """Legacy unbound tasks need no fabricated history; valid closed-loop history is clean."""
    _task, request = _bind_and_complete(closed_loop_session, approved_closed_loop_case)
    response = FollowUpResponse(
        organization_id=approved_closed_loop_case.organization_id,
        follow_up_request_id=request.id,
        submitted_by_user_id=approved_closed_loop_case.patient_user_id,
        patient_identity_link_id=approved_closed_loop_case.patient_identity_link_id,
        response=FollowUpResponseValue.RESOLVED,
        note=None,
        submitted_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    closed_loop_session.add(response)
    closed_loop_session.flush()

    assert closed_loop_case.navigation_task_id != approved_closed_loop_case.navigation_task_id
    assert inspect_integrity(closed_loop_session) == []


def test_integrity_reports_invalid_bound_authorization_and_lifecycle(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    task, _request = _bind_and_complete(closed_loop_session, approved_closed_loop_case)
    with _corruption_savepoint(closed_loop_session):
        closed_loop_session.execute(
            text("ALTER TABLE navigation_task DISABLE TRIGGER USER")
        )
        closed_loop_session.execute(
            text(
                "UPDATE navigation_task SET title = 'corrupted title', due_at = NULL "
                "WHERE id = :task_id"
            ),
            {"task_id": task.id},
        )
        closed_loop_session.execute(
            text("ALTER TABLE navigation_task ENABLE TRIGGER USER")
        )
        categories = _categories(closed_loop_session)
        assert "invalid_bound_task_authorization" in categories
        assert "inconsistent_bound_task_lifecycle" in categories


def test_integrity_reports_missing_completion_request_and_transition_audit(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    task, request = _bind_and_complete(closed_loop_session, approved_closed_loop_case)
    with _corruption_savepoint(closed_loop_session):
        closed_loop_session.execute(
            text("ALTER TABLE follow_up_request DISABLE TRIGGER USER")
        )
        closed_loop_session.execute(
            text("DELETE FROM follow_up_request WHERE id = :request_id"),
            {"request_id": request.id},
        )
        closed_loop_session.execute(
            text("ALTER TABLE follow_up_request ENABLE TRIGGER USER")
        )
        closed_loop_session.execute(text("ALTER TABLE audit_event DISABLE TRIGGER USER"))
        closed_loop_session.execute(
            text(
                "DELETE FROM audit_event WHERE entity_id = :task_id "
                "AND event_type = 'navigation_task_started'"
            ),
            {"task_id": task.id},
        )
        closed_loop_session.execute(text("ALTER TABLE audit_event ENABLE TRIGGER USER"))
        categories = _categories(closed_loop_session)
        assert "invalid_follow_up_request" in categories
        assert "invalid_navigation_task_transition_audit" in categories


def test_integrity_reports_response_time_and_request_scope_corruption(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    _task, request = _bind_and_complete(closed_loop_session, approved_closed_loop_case)
    response = FollowUpResponse(
        organization_id=approved_closed_loop_case.organization_id,
        follow_up_request_id=request.id,
        submitted_by_user_id=approved_closed_loop_case.patient_user_id,
        patient_identity_link_id=approved_closed_loop_case.patient_identity_link_id,
        response=FollowUpResponseValue.UNRESOLVED,
        note=None,
        submitted_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    closed_loop_session.add(response)
    closed_loop_session.flush()

    with _corruption_savepoint(closed_loop_session):
        closed_loop_session.execute(
            text("ALTER TABLE follow_up_response DISABLE TRIGGER USER")
        )
        closed_loop_session.execute(
            text(
                "UPDATE follow_up_response SET submitted_at = :submitted_at "
                "WHERE id = :response_id"
            ),
            {
                "submitted_at": request.requested_at - timedelta(seconds=1),
                "response_id": response.id,
            },
        )
        closed_loop_session.execute(
            text("ALTER TABLE follow_up_response ENABLE TRIGGER USER")
        )
        closed_loop_session.execute(
            text("ALTER TABLE follow_up_request DISABLE TRIGGER USER")
        )
        closed_loop_session.execute(
            text(
                "ALTER TABLE follow_up_request "
                "DROP CONSTRAINT fk_follow_up_request_reported_need"
            )
        )
        closed_loop_session.execute(
            text(
                "UPDATE follow_up_request SET care_episode_id = :bad_episode "
                "WHERE id = :request_id"
            ),
            {"bad_episode": uuid4(), "request_id": request.id},
        )
        closed_loop_session.execute(
            text("ALTER TABLE follow_up_request ENABLE TRIGGER USER")
        )
        categories = _categories(closed_loop_session)
        assert "invalid_follow_up_response" in categories
        assert "invalid_follow_up_request" in categories


def test_integrity_reports_duplicate_request_and_transition_audit(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    task, request = _bind_and_complete(closed_loop_session, approved_closed_loop_case)
    with _corruption_savepoint(closed_loop_session):
        closed_loop_session.execute(
            text(
                "ALTER TABLE follow_up_request DROP CONSTRAINT "
                "uq_follow_up_request_organization_navigation_task"
            )
        )
        closed_loop_session.execute(
            text(
                "INSERT INTO follow_up_request "
                "(id, organization_id, patient_id, care_episode_id, reported_need_id, "
                "navigation_task_id, requested_by_user_id, requested_at, prompt_version) "
                "SELECT :duplicate_id, organization_id, patient_id, care_episode_id, "
                "reported_need_id, navigation_task_id, requested_by_user_id, "
                "requested_at, prompt_version FROM follow_up_request WHERE id = :id"
            ),
            {"duplicate_id": uuid4(), "id": request.id},
        )
        closed_loop_session.execute(
            text(
                "INSERT INTO audit_event "
                "(id, organization_id, actor_type, actor_user_id, entity_type, "
                "entity_id, event_type, payload, created_at) "
                "SELECT :duplicate_id, organization_id, actor_type, actor_user_id, "
                "entity_type, entity_id, event_type, payload, created_at "
                "FROM audit_event WHERE entity_id = :task_id "
                "AND event_type = 'navigation_task_claimed'"
            ),
            {"duplicate_id": uuid4(), "task_id": task.id},
        )
        violations = inspect_integrity(closed_loop_session)
        assert any(
            violation.category == "invalid_follow_up_request"
            and violation.evidence["issue"] == "request_count"
            for violation in violations
        )
        assert any(
            violation.category == "invalid_navigation_task_transition_audit"
            and violation.evidence["issue"] == "duplicate_event"
            for violation in violations
        )
