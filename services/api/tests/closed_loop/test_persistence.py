from datetime import UTC, datetime, timedelta
from hashlib import md5
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models import (
    AuditEvent,
    FollowUpRequest,
    FollowUpResponse,
    NavigationTask,
    Outcome,
    PatientIdentityLink,
    ProposedChange,
    RoleAssignment,
)
from app.domain.enums import (
    FollowUpResponseValue,
    NavigationTaskStatus,
    OutcomeDisposition,
)


def test_closed_loop_schema_exposes_exact_task_binding_and_follow_up_history(
    closed_loop_session: Session,
) -> None:
    """Removing migration 0006 must remove a binding or follow-up table this contract needs."""
    inspector = inspect(closed_loop_session.get_bind())
    task_columns = {
        column["name"]: column for column in inspector.get_columns("navigation_task")
    }

    assert "authorized_proposed_change_id" in task_columns
    assert task_columns["authorized_proposed_change_id"]["nullable"] is True
    assert {"follow_up_request", "follow_up_response"} <= set(inspector.get_table_names())

    request_columns = {
        column["name"]: column for column in inspector.get_columns("follow_up_request")
    }
    assert set(request_columns) == {
        "id",
        "organization_id",
        "patient_id",
        "care_episode_id",
        "reported_need_id",
        "navigation_task_id",
        "requested_by_user_id",
        "requested_at",
        "prompt_version",
    }
    assert all(column["nullable"] is False for column in request_columns.values())

    response_columns = {
        column["name"]: column for column in inspector.get_columns("follow_up_response")
    }
    assert set(response_columns) == {
        "id",
        "organization_id",
        "follow_up_request_id",
        "submitted_by_user_id",
        "patient_identity_link_id",
        "response",
        "note",
        "submitted_at",
    }
    assert response_columns["note"]["nullable"] is True
    assert all(
        column["nullable"] is False
        for name, column in response_columns.items()
        if name != "note"
    )

    unique_keys = {
        table_name: {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        }
        for table_name in (
            "navigation_task",
            "proposed_change",
            "patient_identity_link",
            "follow_up_request",
            "follow_up_response",
        )
    }
    assert (
        "organization_id",
        "patient_id",
        "reported_need_id",
        "id",
    ) in unique_keys["navigation_task"]
    assert (
        "organization_id",
        "navigation_task_id",
        "id",
    ) in unique_keys["proposed_change"]
    assert (
        "organization_id",
        "user_id",
        "id",
    ) in unique_keys["patient_identity_link"]
    assert ("organization_id", "navigation_task_id") in unique_keys["follow_up_request"]
    assert ("organization_id", "follow_up_request_id") in unique_keys["follow_up_response"]

    foreign_keys = {
        table_name: {
            tuple(constraint["constrained_columns"]): (
                constraint["referred_table"],
                tuple(constraint["referred_columns"]),
            )
            for constraint in inspector.get_foreign_keys(table_name)
        }
        for table_name in (
            "navigation_task",
            "follow_up_request",
            "follow_up_response",
        )
    }
    assert foreign_keys["navigation_task"][
        ("organization_id", "id", "authorized_proposed_change_id")
    ] == (
        "proposed_change",
        ("organization_id", "navigation_task_id", "id"),
    )
    assert foreign_keys["follow_up_request"][
        ("organization_id", "patient_id", "care_episode_id", "reported_need_id")
    ] == (
        "reported_need",
        ("organization_id", "patient_id", "care_episode_id", "id"),
    )
    assert foreign_keys["follow_up_request"][
        ("organization_id", "patient_id", "reported_need_id", "navigation_task_id")
    ] == (
        "navigation_task",
        ("organization_id", "patient_id", "reported_need_id", "id"),
    )
    assert foreign_keys["follow_up_response"][
        ("organization_id", "follow_up_request_id")
    ] == (
        "follow_up_request",
        ("organization_id", "id"),
    )
    assert foreign_keys["follow_up_response"][
        ("organization_id", "submitted_by_user_id", "patient_identity_link_id")
    ] == (
        "patient_identity_link",
        ("organization_id", "user_id", "id"),
    )


def _bind_approved_task(
    session: Session,
    case: Any,
    *,
    due_at: datetime | None = None,
) -> NavigationTask:
    task = session.get(NavigationTask, case.navigation_task_id)
    proposal = session.get(ProposedChange, case.proposed_change_id)
    assert task is not None
    assert proposal is not None
    task.authorized_proposed_change_id = proposal.id
    task.title = proposal.proposed_value["title"]
    task.assignee_user_id = case.navigator_user_id
    task.due_at = due_at or datetime.now(UTC) + timedelta(days=1)
    task.status = NavigationTaskStatus.ASSIGNED
    session.flush()
    return task


def test_task_binding_requires_the_exact_approved_proposal(
    closed_loop_session: Session,
    closed_loop_case: Any,
    approved_closed_loop_case: Any,
) -> None:
    """Treating a pending proposal as executable must fail without changing its task."""
    pending_task = closed_loop_session.get(
        NavigationTask, closed_loop_case.navigation_task_id
    )
    pending_proposal = closed_loop_session.get(
        ProposedChange, closed_loop_case.proposed_change_id
    )
    assert pending_task is not None
    assert pending_proposal is not None

    with pytest.raises(DBAPIError, match="approved"):
        with closed_loop_session.begin_nested():
            pending_task.authorized_proposed_change_id = pending_proposal.id
            pending_task.title = pending_proposal.proposed_value["title"]
            pending_task.assignee_user_id = closed_loop_case.navigator_user_id
            pending_task.due_at = datetime.now(UTC) + timedelta(days=1)
            pending_task.status = NavigationTaskStatus.ASSIGNED
            closed_loop_session.flush()

    closed_loop_session.refresh(pending_task)
    assert pending_task.status == NavigationTaskStatus.OPEN
    assert pending_task.authorized_proposed_change_id is None
    assert pending_task.assignee_user_id is None

    approved_task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    assert approved_task.status == NavigationTaskStatus.ASSIGNED
    assert approved_task.authorized_proposed_change_id == (
        approved_closed_loop_case.proposed_change_id
    )
    assert approved_task.assignee_user_id == approved_closed_loop_case.navigator_user_id
    assert approved_task.title == "Arrange synthetic transportation support"


@pytest.mark.parametrize("mutation", ["title", "owner", "due_at"])
def test_bound_task_execution_tuple_is_frozen_after_claim(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
    mutation: str,
) -> None:
    """Changing any material approved-execution field after claim must be rejected."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)

    with pytest.raises(DBAPIError, match="binding|title|owner|due"):
        with closed_loop_session.begin_nested():
            if mutation == "title":
                task.title = "Unapproved replacement title"
            elif mutation == "owner":
                task.assignee_user_id = approved_closed_loop_case.proposer_user_id
            else:
                assert task.due_at is not None
                task.due_at = task.due_at + timedelta(hours=1)
            closed_loop_session.flush()


def _deterministic_uuid(value: str) -> UUID:
    return UUID(md5(value.encode("utf-8"), usedforsecurity=False).hexdigest())


def test_bound_task_transitions_write_one_typed_audit_each_and_one_follow_up_request(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Removing a transition side effect must leave an observable missing history record."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    task.status = NavigationTaskStatus.IN_PROGRESS
    closed_loop_session.flush()
    task.status = NavigationTaskStatus.COMPLETED
    closed_loop_session.flush()
    closed_loop_session.refresh(task)

    request = closed_loop_session.scalar(
        select(FollowUpRequest).where(
            FollowUpRequest.organization_id == approved_closed_loop_case.organization_id,
            FollowUpRequest.navigation_task_id == task.id,
        )
    )
    assert request is not None
    assert request.id == _deterministic_uuid(f"{task.id}follow_up_request")
    assert request.patient_id == approved_closed_loop_case.patient_id
    assert request.care_episode_id == approved_closed_loop_case.care_episode_id
    assert request.reported_need_id == approved_closed_loop_case.reported_need_id
    assert request.requested_by_user_id == approved_closed_loop_case.navigator_user_id
    assert request.prompt_version == 1
    assert task.completed_at == request.requested_at
    assert task.due_at is not None

    events = closed_loop_session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == approved_closed_loop_case.organization_id,
            AuditEvent.entity_type == "navigation_task",
            AuditEvent.entity_id == task.id,
            AuditEvent.event_type.in_(
                (
                    "navigation_task_claimed",
                    "navigation_task_started",
                    "navigation_task_completed",
                )
            ),
        )
        .order_by(AuditEvent.created_at, AuditEvent.event_type)
    ).all()
    assert {event.event_type for event in events} == {
        "navigation_task_claimed",
        "navigation_task_started",
        "navigation_task_completed",
    }
    assert len(events) == 3
    for event in events:
        assert event.id == _deterministic_uuid(f"{task.id}{event.event_type}")
        assert event.actor_type.value == "user"
        assert event.actor_user_id == approved_closed_loop_case.navigator_user_id
        assert event.payload["need_id"] == str(approved_closed_loop_case.reported_need_id)
        assert event.payload["authorized_proposed_change_id"] == str(
            approved_closed_loop_case.proposed_change_id
        )
        assert event.payload["assignee_user_id"] == str(
            approved_closed_loop_case.navigator_user_id
        )
        assert datetime.fromisoformat(event.payload["due_at"]) == task.due_at

    completed = next(
        event for event in events if event.event_type == "navigation_task_completed"
    )
    assert completed.created_at == task.completed_at
    assert completed.payload["follow_up_request_id"] == str(request.id)


def test_bound_task_rejects_skipped_and_reversed_transitions(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Allowing a skipped or reversed state would bypass the approved execution sequence."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    with pytest.raises(DBAPIError, match="transition"):
        with closed_loop_session.begin_nested():
            task.status = NavigationTaskStatus.COMPLETED
            closed_loop_session.flush()
    closed_loop_session.refresh(task)

    task.status = NavigationTaskStatus.IN_PROGRESS
    closed_loop_session.flush()
    with pytest.raises(DBAPIError, match="transition"):
        with closed_loop_session.begin_nested():
            task.status = NavigationTaskStatus.ASSIGNED
            closed_loop_session.flush()


def test_bound_task_transition_rechecks_current_navigator_authority(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Revoking the assignee before start must prevent new work under stale authority."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    role = closed_loop_session.get(
        RoleAssignment, approved_closed_loop_case.navigator_role_assignment_id
    )
    assert role is not None
    role.revoked_at = datetime.now(UTC)
    closed_loop_session.flush()

    with pytest.raises(DBAPIError, match="active navigator authority"):
        with closed_loop_session.begin_nested():
            task.status = NavigationTaskStatus.IN_PROGRESS
            closed_loop_session.flush()


def test_bound_task_cannot_be_deleted(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Deleting approved execution would erase durable authorization history."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)

    with pytest.raises(DBAPIError, match="bound.*deleted|deleted.*bound"):
        with closed_loop_session.begin_nested():
            closed_loop_session.delete(task)
            closed_loop_session.flush()


def _complete_bound_task(session: Session, case: Any) -> tuple[NavigationTask, FollowUpRequest]:
    task = _bind_approved_task(session, case)
    task.status = NavigationTaskStatus.IN_PROGRESS
    session.flush()
    task.status = NavigationTaskStatus.COMPLETED
    session.flush()
    request = session.scalar(
        select(FollowUpRequest).where(
            FollowUpRequest.organization_id == case.organization_id,
            FollowUpRequest.navigation_task_id == task.id,
        )
    )
    assert request is not None
    return task, request


def test_follow_up_request_requires_completed_bound_task_and_is_append_only(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """A request cannot be forged early or altered after the completion transaction."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    premature = FollowUpRequest(
        organization_id=approved_closed_loop_case.organization_id,
        patient_id=approved_closed_loop_case.patient_id,
        care_episode_id=approved_closed_loop_case.care_episode_id,
        reported_need_id=approved_closed_loop_case.reported_need_id,
        navigation_task_id=task.id,
        requested_by_user_id=approved_closed_loop_case.navigator_user_id,
        requested_at=datetime.now(UTC),
        prompt_version=1,
    )
    with pytest.raises(DBAPIError, match="completed bound"):
        with closed_loop_session.begin_nested():
            closed_loop_session.add(premature)
            closed_loop_session.flush()

    closed_loop_session.refresh(task)
    task.status = NavigationTaskStatus.IN_PROGRESS
    closed_loop_session.flush()
    task.status = NavigationTaskStatus.COMPLETED
    closed_loop_session.flush()
    request = closed_loop_session.scalar(
        select(FollowUpRequest).where(
            FollowUpRequest.organization_id == approved_closed_loop_case.organization_id,
            FollowUpRequest.navigation_task_id == task.id,
        )
    )
    assert request is not None
    with pytest.raises(DBAPIError, match="append-only"):
        with closed_loop_session.begin_nested():
            request.prompt_version = 2
            closed_loop_session.flush()


def test_follow_up_response_uses_server_time_and_is_append_only(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Patient evidence receives a database timestamp and immutable attribution."""
    _task, request = _complete_bound_task(
        closed_loop_session, approved_closed_loop_case
    )
    client_supplied_time = request.requested_at - timedelta(days=1)
    response = FollowUpResponse(
        organization_id=approved_closed_loop_case.organization_id,
        follow_up_request_id=request.id,
        submitted_by_user_id=approved_closed_loop_case.patient_user_id,
        patient_identity_link_id=approved_closed_loop_case.patient_identity_link_id,
        response=FollowUpResponseValue.RESOLVED,
        note="Synthetic support was received.",
        submitted_at=client_supplied_time,
    )
    closed_loop_session.add(response)
    closed_loop_session.flush()
    closed_loop_session.refresh(response)

    assert response.submitted_at >= request.requested_at
    assert response.submitted_at != client_supplied_time
    with pytest.raises(DBAPIError, match="append-only"):
        with closed_loop_session.begin_nested():
            response.note = "Rewritten evidence"
            closed_loop_session.flush()


def test_follow_up_response_allows_only_one_answer_per_request(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """A second insert cannot create competing patient-reported evidence."""
    _task, request = _complete_bound_task(
        closed_loop_session, approved_closed_loop_case
    )
    first = _new_follow_up_response(approved_closed_loop_case, request)
    closed_loop_session.add(first)
    closed_loop_session.flush()

    with pytest.raises(DBAPIError, match="organization_request|unique"):
        with closed_loop_session.begin_nested():
            closed_loop_session.add(
                _new_follow_up_response(approved_closed_loop_case, request)
            )
            closed_loop_session.flush()


def _new_follow_up_response(case: Any, request: FollowUpRequest) -> FollowUpResponse:
    return FollowUpResponse(
        organization_id=case.organization_id,
        follow_up_request_id=request.id,
        submitted_by_user_id=case.patient_user_id,
        patient_identity_link_id=case.patient_identity_link_id,
        response=FollowUpResponseValue.STILL_NEEDS_HELP,
        note=None,
        submitted_at=datetime(2000, 1, 1, tzinfo=UTC),
    )


def test_first_follow_up_response_rejects_closed_need_and_revoked_authority(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """A first response needs both an open need and current supporting-actor authority."""
    _task, request = _complete_bound_task(
        closed_loop_session, approved_closed_loop_case
    )
    role = closed_loop_session.get(
        RoleAssignment, approved_closed_loop_case.patient_role_assignment_id
    )
    assert role is not None
    role.revoked_at = datetime.now(UTC)
    closed_loop_session.flush()
    with pytest.raises(DBAPIError, match="supporting actor authority"):
        with closed_loop_session.begin_nested():
            closed_loop_session.add(
                _new_follow_up_response(approved_closed_loop_case, request)
            )
            closed_loop_session.flush()

    role.revoked_at = None
    closed_loop_session.flush()
    outcome = Outcome(
        organization_id=approved_closed_loop_case.organization_id,
        patient_id=approved_closed_loop_case.patient_id,
        reported_need_id=approved_closed_loop_case.reported_need_id,
        recorded_by_user_id=approved_closed_loop_case.navigator_user_id,
        disposition=OutcomeDisposition.CLOSED_UNRESOLVED,
        note=None,
        idempotency_key=f"closed-loop-{request.id}",
        recorded_at=datetime.now(UTC),
    )
    closed_loop_session.add(outcome)
    closed_loop_session.flush()
    with pytest.raises(DBAPIError, match="after need closure"):
        with closed_loop_session.begin_nested():
            closed_loop_session.add(
                _new_follow_up_response(approved_closed_loop_case, request)
            )
            closed_loop_session.flush()


@pytest.mark.parametrize("mutation", ["linked_at", "backdated_revocation"])
def test_response_attribution_blocks_link_history_rewrites(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
    mutation: str,
) -> None:
    """Accepted patient evidence keeps the identity facts that authorized it."""
    _task, request = _complete_bound_task(
        closed_loop_session, approved_closed_loop_case
    )
    response = _new_follow_up_response(approved_closed_loop_case, request)
    closed_loop_session.add(response)
    closed_loop_session.flush()
    closed_loop_session.refresh(response)
    link = closed_loop_session.get(
        PatientIdentityLink, approved_closed_loop_case.patient_identity_link_id
    )
    assert link is not None

    with pytest.raises(DBAPIError, match="response attribution"):
        with closed_loop_session.begin_nested():
            if mutation == "linked_at":
                link.linked_at = link.linked_at - timedelta(days=1)
            else:
                link.revoked_at = response.submitted_at - timedelta(microseconds=1)
            closed_loop_session.flush()


def test_response_attribution_allows_later_link_revocation(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Normal later revocation remains possible without rewriting accepted evidence."""
    _task, request = _complete_bound_task(
        closed_loop_session, approved_closed_loop_case
    )
    response = _new_follow_up_response(approved_closed_loop_case, request)
    closed_loop_session.add(response)
    closed_loop_session.flush()
    closed_loop_session.refresh(response)
    link = closed_loop_session.get(
        PatientIdentityLink, approved_closed_loop_case.patient_identity_link_id
    )
    assert link is not None
    link.revoked_at = response.submitted_at + timedelta(seconds=1)
    closed_loop_session.flush()


def test_follow_up_request_failure_rolls_back_task_completion(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """Completion cannot commit without its deterministic follow-up request."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    task.status = NavigationTaskStatus.IN_PROGRESS
    closed_loop_session.flush()
    closed_loop_session.execute(
        text(
            """
            CREATE FUNCTION fail_closed_loop_follow_up_request()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'synthetic follow-up request failure';
            END;
            $$;
            CREATE TRIGGER trg_fail_closed_loop_follow_up_request
            BEFORE INSERT ON follow_up_request
            FOR EACH ROW EXECUTE FUNCTION fail_closed_loop_follow_up_request();
            """
        )
    )

    with pytest.raises(DBAPIError, match="synthetic follow-up request failure"):
        with closed_loop_session.begin_nested():
            task.status = NavigationTaskStatus.COMPLETED
            closed_loop_session.flush()

    closed_loop_session.refresh(task)
    assert task.status == NavigationTaskStatus.IN_PROGRESS
    assert task.completed_at is None
    assert closed_loop_session.scalar(
        select(FollowUpRequest).where(FollowUpRequest.navigation_task_id == task.id)
    ) is None
    assert closed_loop_session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == task.id,
            AuditEvent.event_type == "navigation_task_completed",
        )
    ) is None


def test_audit_failure_rolls_back_task_transition(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """A task state cannot advance if its user-attributed audit event fails."""
    task = _bind_approved_task(closed_loop_session, approved_closed_loop_case)
    closed_loop_session.execute(
        text(
            """
            CREATE FUNCTION fail_closed_loop_start_audit()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.event_type = 'navigation_task_started' THEN
                    RAISE EXCEPTION 'synthetic transition audit failure';
                END IF;
                RETURN NEW;
            END;
            $$;
            CREATE TRIGGER trg_fail_closed_loop_start_audit
            BEFORE INSERT ON audit_event
            FOR EACH ROW EXECUTE FUNCTION fail_closed_loop_start_audit();
            """
        )
    )

    with pytest.raises(DBAPIError, match="synthetic transition audit failure"):
        with closed_loop_session.begin_nested():
            task.status = NavigationTaskStatus.IN_PROGRESS
            closed_loop_session.flush()

    closed_loop_session.refresh(task)
    assert task.status == NavigationTaskStatus.ASSIGNED
    assert closed_loop_session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_id == task.id,
            AuditEvent.event_type == "navigation_task_started",
        )
    ) is None


def test_ojcc_app_can_append_guarded_response_but_cannot_rewrite_history(
    closed_loop_session: Session,
    approved_closed_loop_case: Any,
) -> None:
    """The least-privilege application role uses the guarded append surface."""
    _task, request = _complete_bound_task(
        closed_loop_session, approved_closed_loop_case
    )
    response_id = uuid4()
    closed_loop_session.execute(text("SET LOCAL ROLE ojcc_app"))
    try:
        submitted_at = closed_loop_session.execute(
            text(
                """
                INSERT INTO follow_up_response
                    (id, organization_id, follow_up_request_id,
                     submitted_by_user_id, patient_identity_link_id,
                     response, note, submitted_at)
                VALUES
                    (:id, :organization_id, :request_id,
                     :user_id, :link_id,
                     'resolved', NULL, '2000-01-01T00:00:00Z')
                RETURNING submitted_at
                """
            ),
            {
                "id": response_id,
                "organization_id": approved_closed_loop_case.organization_id,
                "request_id": request.id,
                "user_id": approved_closed_loop_case.patient_user_id,
                "link_id": approved_closed_loop_case.patient_identity_link_id,
            },
        ).scalar_one()
        assert submitted_at >= request.requested_at

        with pytest.raises(DBAPIError, match="permission denied|append-only"):
            with closed_loop_session.begin_nested():
                closed_loop_session.execute(
                    text("UPDATE follow_up_response SET note = 'rewrite' WHERE id = :id"),
                    {"id": response_id},
                )

        with pytest.raises(DBAPIError, match="permission denied"):
            with closed_loop_session.begin_nested():
                closed_loop_session.execute(
                    text(
                        """
                        INSERT INTO follow_up_request
                            (id, organization_id, patient_id, care_episode_id,
                             reported_need_id, navigation_task_id,
                             requested_by_user_id, requested_at, prompt_version)
                        SELECT :id, organization_id, patient_id, care_episode_id,
                               reported_need_id, navigation_task_id,
                               requested_by_user_id, requested_at, prompt_version
                        FROM follow_up_request WHERE id = :request_id
                        """
                    ),
                    {"id": uuid4(), "request_id": request.id},
                )
    finally:
        closed_loop_session.execute(text("RESET ROLE"))
