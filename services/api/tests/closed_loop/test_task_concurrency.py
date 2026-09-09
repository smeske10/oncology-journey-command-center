from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from typing import Any, Literal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, FollowUpRequest, NavigationTask, Outcome
from app.domain.navigation_tasks import (
    TaskConflict,
    claim_navigation_task,
    complete_navigation_task,
    start_navigation_task,
)
from app.domain.outcomes import record_outcome


def _claim(
    session: Session,
    case: Any,
    *,
    due_at: datetime,
) -> Any:
    return claim_navigation_task(
        session,
        organization_id=case.organization_id,
        task_id=case.navigation_task_id,
        actor_user_id=case.navigator_user_id,
        proposed_change_id=case.proposed_change_id,
        due_at=due_at,
    )


def _prepare_task(
    engine: Engine,
    case: Any,
    *,
    status: Literal["assigned", "in_progress"],
) -> datetime:
    due_at = datetime.now(UTC) + timedelta(days=2)
    with Session(engine, expire_on_commit=False) as session:
        _claim(session, case, due_at=due_at)
        if status == "in_progress":
            start_navigation_task(
                session,
                organization_id=case.organization_id,
                task_id=case.navigation_task_id,
                actor_user_id=case.navigator_user_id,
            )
        session.commit()
    return due_at


def _set_lock_timeout(session: Session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '5s'"))


def test_rejected_claim_leaves_no_partial_state_on_committed_aggregate(
    committed_closed_loop_case: tuple[Engine, Any],
) -> None:
    engine, case = committed_closed_loop_case
    with Session(engine) as session:
        with pytest.raises(ValueError, match="future"):
            _claim(
                session,
                case,
                due_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        session.rollback()

    with engine.connect() as connection:
        task = connection.execute(
            text(
                "SELECT status, assignee_user_id, authorized_proposed_change_id "
                "FROM navigation_task WHERE id = :task_id"
            ),
            {"task_id": case.navigation_task_id},
        ).mappings().one()
        event_count = connection.scalar(
            text("SELECT count(*) FROM audit_event WHERE entity_id = :task_id"),
            {"task_id": case.navigation_task_id},
        )
    assert task.status == "open"
    assert task.assignee_user_id is None
    assert task.authorized_proposed_change_id is None
    assert event_count == 0


def test_competing_claims_commit_exactly_one_execution_tuple(
    committed_closed_loop_case: tuple[Engine, Any],
) -> None:
    engine, case = committed_closed_loop_case
    barrier = Barrier(2)
    first_due = datetime.now(UTC) + timedelta(days=2)
    second_due = first_due + timedelta(hours=1)

    def claim(due_at: datetime) -> str:
        with Session(engine) as session:
            _set_lock_timeout(session)
            barrier.wait(timeout=10)
            try:
                _claim(session, case, due_at=due_at)
                session.commit()
                return "claimed"
            except TaskConflict as error:
                session.rollback()
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(claim, first_due)
        second = executor.submit(claim, second_due)
        results = sorted([first.result(timeout=20), second.result(timeout=20)])

    assert results == ["claimed", "task_claim_mismatch"]
    with engine.connect() as connection:
        task = connection.execute(
            text(
                "SELECT status, due_at, authorized_proposed_change_id "
                "FROM navigation_task WHERE id = :task_id"
            ),
            {"task_id": case.navigation_task_id},
        ).mappings().one()
        event_count = connection.scalar(
            text(
                "SELECT count(*) FROM audit_event "
                "WHERE entity_id = :task_id AND event_type = 'navigation_task_claimed'"
            ),
            {"task_id": case.navigation_task_id},
        )
    assert task.status == "assigned"
    assert task.due_at in {first_due, second_due}
    assert task.authorized_proposed_change_id == case.proposed_change_id
    assert event_count == 1


def test_concurrent_complete_replays_one_request_and_one_event(
    committed_closed_loop_case: tuple[Engine, Any],
) -> None:
    engine, case = committed_closed_loop_case
    _prepare_task(engine, case, status="in_progress")
    barrier = Barrier(2)

    def complete() -> tuple[str, str | None]:
        with Session(engine) as session:
            _set_lock_timeout(session)
            barrier.wait(timeout=10)
            result = complete_navigation_task(
                session,
                organization_id=case.organization_id,
                task_id=case.navigation_task_id,
                actor_user_id=case.navigator_user_id,
            )
            session.commit()
            return (
                "replayed" if result.replayed else "completed",
                str(result.follow_up_request_id),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(complete)
        second = executor.submit(complete)
        results = [first.result(timeout=20), second.result(timeout=20)]

    assert sorted(result[0] for result in results) == ["completed", "replayed"]
    assert results[0][1] == results[1][1]
    with engine.connect() as connection:
        request_count = connection.scalar(
            text("SELECT count(*) FROM follow_up_request WHERE navigation_task_id = :task_id"),
            {"task_id": case.navigation_task_id},
        )
        complete_event_count = connection.scalar(
            text(
                "SELECT count(*) FROM audit_event "
                "WHERE entity_id = :task_id AND event_type = 'navigation_task_completed'"
            ),
            {"task_id": case.navigation_task_id},
        )
    assert request_count == 1
    assert complete_event_count == 1


@pytest.mark.parametrize("first", ["task", "outcome"])
def test_complete_outcome_race_has_only_a_valid_terminal_history(
    committed_closed_loop_case: tuple[Engine, Any],
    first: Literal["task", "outcome"],
) -> None:
    engine, case = committed_closed_loop_case
    _prepare_task(engine, case, status="in_progress")
    results = _run_task_outcome_race(engine, case, command="complete", first=first)

    if first == "task":
        assert results == {"task": "completed", "outcome": "recorded"}
        expected_status = "completed"
        expected_requests = 1
        expected_events = 1
    else:
        assert results == {"task": "need_closed", "outcome": "recorded"}
        expected_status = "cancelled"
        expected_requests = 0
        expected_events = 0
    _assert_race_rows(
        engine,
        case,
        status=expected_status,
        request_count=expected_requests,
        event_type="navigation_task_completed",
        event_count=expected_events,
    )


@pytest.mark.parametrize("first", ["task", "outcome"])
def test_start_outcome_race_preserves_outcome_cancellation_authority(
    committed_closed_loop_case: tuple[Engine, Any],
    first: Literal["task", "outcome"],
) -> None:
    engine, case = committed_closed_loop_case
    _prepare_task(engine, case, status="assigned")
    results = _run_task_outcome_race(engine, case, command="start", first=first)

    assert results["outcome"] == "recorded"
    assert results["task"] == ("started" if first == "task" else "need_closed")
    _assert_race_rows(
        engine,
        case,
        status="cancelled",
        request_count=0,
        event_type="navigation_task_started",
        event_count=1 if first == "task" else 0,
    )


def _run_task_outcome_race(
    engine: Engine,
    case: Any,
    *,
    command: Literal["start", "complete"],
    first: Literal["task", "outcome"],
) -> dict[str, str]:
    first_written = Event()
    contender_started = Event()
    release_first = Event()

    def task_worker() -> str:
        with Session(engine) as session:
            _set_lock_timeout(session)
            if first == "outcome":
                assert first_written.wait(timeout=10)
                contender_started.set()
            try:
                if command == "start":
                    result = start_navigation_task(
                        session,
                        organization_id=case.organization_id,
                        task_id=case.navigation_task_id,
                        actor_user_id=case.navigator_user_id,
                    )
                    label = "started"
                else:
                    result = complete_navigation_task(
                        session,
                        organization_id=case.organization_id,
                        task_id=case.navigation_task_id,
                        actor_user_id=case.navigator_user_id,
                    )
                    label = "completed"
                assert result.replayed is False
                if first == "task":
                    first_written.set()
                    assert release_first.wait(timeout=10)
                session.commit()
                return label
            except TaskConflict as error:
                session.rollback()
                return error.code

    def outcome_worker() -> str:
        with Session(engine) as session:
            _set_lock_timeout(session)
            if first == "task":
                assert first_written.wait(timeout=10)
                contender_started.set()
            record_outcome(
                session,
                organization_id=case.organization_id,
                need_id=case.reported_need_id,
                recorded_by_user_id=case.navigator_user_id,
                disposition="closed_unresolved",
                note=None,
                idempotency_key=f"race-{command}-{first}-{uuid4()}",
            )
            if first == "outcome":
                first_written.set()
                assert release_first.wait(timeout=10)
            session.commit()
            return "recorded"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            task_future = executor.submit(task_worker)
            outcome_future = executor.submit(outcome_worker)
            assert contender_started.wait(timeout=10)
            release_first.set()
            return {
                "task": task_future.result(timeout=20),
                "outcome": outcome_future.result(timeout=20),
            }
    finally:
        release_first.set()


def _assert_race_rows(
    engine: Engine,
    case: Any,
    *,
    status: str,
    request_count: int,
    event_type: str,
    event_count: int,
) -> None:
    with Session(engine) as session:
        task = session.get(NavigationTask, case.navigation_task_id)
        assert task is not None
        assert task.status.value == status
        assert (
            session.scalar(
                select(FollowUpRequest)
                .where(FollowUpRequest.navigation_task_id == case.navigation_task_id)
                .with_for_update()
            )
            is not None
        ) == (request_count == 1)
        assert (
            session.scalar(
                select(Outcome).where(
                    Outcome.reported_need_id == case.reported_need_id
                )
            )
            is not None
        )
        events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == case.navigation_task_id,
                AuditEvent.event_type == event_type,
            )
        ).all()
        assert len(events) == event_count
