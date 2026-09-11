"""Human task commands with deterministic locking and replay semantics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.advisory_locks import acquire_transaction_lock
from app.db.models import (
    FollowUpRequest,
    NavigationTask,
    NavigationTaskResource,
    Outcome,
    ProposedChange,
    ReportedNeed,
    RoleAssignment,
)
from app.db.repositories import effective_proposed_change_state
from app.domain.enums import ApprovalChangeType, NavigationTaskStatus, UserRole

TaskConflictCode = Literal[
    "need_closed",
    "task_state_conflict",
    "task_claim_mismatch",
    "task_unbound",
    "proposal_not_approved",
    "concurrent_change",
]


class TaskNotFound(LookupError):
    """The selected task or proposal is absent from the actor's tenant."""


class TaskForbidden(PermissionError):
    """The actor can see the task but cannot operate it."""


class TaskConflict(ValueError):
    def __init__(self, code: TaskConflictCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TaskCommandResult:
    task_id: UUID
    need_id: UUID
    status: NavigationTaskStatus
    assignee_user_id: UUID | None
    due_at: datetime | None
    authorized_proposed_change_id: UUID | None
    completed_at: datetime | None
    follow_up_request_id: UUID | None
    replayed: bool


def claim_navigation_task(
    session: Session,
    *,
    organization_id: UUID,
    task_id: UUID,
    actor_user_id: UUID,
    proposed_change_id: UUID,
    due_at: datetime,
) -> TaskCommandResult:
    normalized_due_at = _normalize_aware_datetime(due_at)
    preliminary = _visible_task(session, organization_id=organization_id, task_id=task_id)
    acquire_transaction_lock(
        session,
        namespace="reported_need",
        identifier=preliminary.reported_need_id,
    )
    need = _lock_need_for_task(session, preliminary)
    closure_exists = _need_has_outcome(session, need)

    proposal = session.scalar(
        select(ProposedChange)
        .where(
            ProposedChange.organization_id == organization_id,
            ProposedChange.id == proposed_change_id,
        )
    )
    if proposal is None:
        raise TaskNotFound("Navigation task command target not found")

    task = session.scalar(
        select(NavigationTask)
        .where(
            NavigationTask.organization_id == organization_id,
            NavigationTask.id == task_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise TaskNotFound("Navigation task command target not found")

    _lock_current_navigator_authority(
        session,
        organization_id=organization_id,
        user_id=actor_user_id,
    )

    if task.authorized_proposed_change_id is not None:
        if task.assignee_user_id != actor_user_id:
            raise TaskForbidden("Only the assigned navigator can operate this task")
        if task.status is NavigationTaskStatus.CANCELLED:
            raise TaskConflict("task_state_conflict", "Cancelled task cannot be claimed")
        if (
            task.authorized_proposed_change_id != proposed_change_id
            or task.due_at != normalized_due_at
        ):
            raise TaskConflict(
                "task_claim_mismatch",
                "Task was already claimed with a different proposal, owner, or due time",
            )
        if task.status not in (
            NavigationTaskStatus.ASSIGNED,
            NavigationTaskStatus.IN_PROGRESS,
            NavigationTaskStatus.COMPLETED,
        ):
            raise TaskConflict("task_state_conflict", "Task cannot replay this claim")
        return _result(session, task, replayed=True)

    if closure_exists:
        raise TaskConflict("need_closed", "Reported need is already closed")
    if task.status is not NavigationTaskStatus.OPEN:
        raise TaskConflict("task_state_conflict", "Task is not open for claim")
    if normalized_due_at <= datetime.now(UTC):
        raise ValueError("Due time must be strictly in the future")
    title = _approved_task_title(
        session,
        proposal=proposal,
        organization_id=organization_id,
        task_id=task.id,
    )

    try:
        with session.begin_nested():
            task.authorized_proposed_change_id = proposal.id
            task.title = title
            task.assignee_user_id = actor_user_id
            task.due_at = normalized_due_at
            task.status = NavigationTaskStatus.ASSIGNED
            session.flush()
    except DBAPIError as error:
        mapped = map_task_database_error(error)
        if mapped is not None:
            raise mapped from error
        raise
    session.refresh(task)
    return _result(session, task, replayed=False)


def start_navigation_task(
    session: Session,
    *,
    organization_id: UUID,
    task_id: UUID,
    actor_user_id: UUID,
) -> TaskCommandResult:
    return _transition_navigation_task(
        session,
        organization_id=organization_id,
        task_id=task_id,
        actor_user_id=actor_user_id,
        command="start",
    )


def complete_navigation_task(
    session: Session,
    *,
    organization_id: UUID,
    task_id: UUID,
    actor_user_id: UUID,
) -> TaskCommandResult:
    return _transition_navigation_task(
        session,
        organization_id=organization_id,
        task_id=task_id,
        actor_user_id=actor_user_id,
        command="complete",
    )


def _transition_navigation_task(
    session: Session,
    *,
    organization_id: UUID,
    task_id: UUID,
    actor_user_id: UUID,
    command: Literal["start", "complete"],
) -> TaskCommandResult:
    preliminary = _visible_task(session, organization_id=organization_id, task_id=task_id)
    acquire_transaction_lock(
        session,
        namespace="reported_need",
        identifier=preliminary.reported_need_id,
    )
    need = _lock_need_for_task(session, preliminary)
    closure_exists = _need_has_outcome(session, need)
    task = session.scalar(
        select(NavigationTask)
        .where(
            NavigationTask.organization_id == organization_id,
            NavigationTask.id == task_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise TaskNotFound("Navigation task command target not found")
    _lock_current_navigator_authority(
        session,
        organization_id=organization_id,
        user_id=actor_user_id,
    )
    if task.authorized_proposed_change_id is None:
        raise TaskConflict("task_unbound", "Task has no approved execution binding")
    if task.assignee_user_id != actor_user_id:
        raise TaskForbidden("Only the assigned navigator can operate this task")

    if command == "start":
        if task.status in (
            NavigationTaskStatus.IN_PROGRESS,
            NavigationTaskStatus.COMPLETED,
        ):
            return _result(session, task, replayed=True)
        expected_status = NavigationTaskStatus.ASSIGNED
        next_status = NavigationTaskStatus.IN_PROGRESS
    else:
        if task.status is NavigationTaskStatus.COMPLETED:
            return _result(session, task, replayed=True)
        expected_status = NavigationTaskStatus.IN_PROGRESS
        next_status = NavigationTaskStatus.COMPLETED

    if closure_exists:
        raise TaskConflict("need_closed", "Reported need is already closed")
    if task.status is not expected_status:
        raise TaskConflict(
            "task_state_conflict",
            f"Task is not ready to {command}",
        )
    try:
        with session.begin_nested():
            task.status = next_status
            session.flush()
    except DBAPIError as error:
        mapped = map_task_database_error(error)
        if mapped is not None:
            raise mapped from error
        raise
    session.refresh(task)
    return _result(session, task, replayed=False)


def _visible_task(
    session: Session, *, organization_id: UUID, task_id: UUID
) -> NavigationTask:
    task = session.scalar(
        select(NavigationTask).where(
            NavigationTask.organization_id == organization_id,
            NavigationTask.id == task_id,
        )
    )
    if task is None:
        raise TaskNotFound("Navigation task command target not found")
    return task


def _lock_need_for_task(session: Session, task: NavigationTask) -> ReportedNeed:
    need = session.scalar(
        select(ReportedNeed)
        .where(
            ReportedNeed.organization_id == task.organization_id,
            ReportedNeed.patient_id == task.patient_id,
            ReportedNeed.id == task.reported_need_id,
        )
    )
    if need is None:
        raise TaskNotFound("Navigation task command target not found")
    return need


def _need_has_outcome(session: Session, need: ReportedNeed) -> bool:
    return (
        session.scalar(
            select(Outcome.id).where(
                Outcome.organization_id == need.organization_id,
                Outcome.patient_id == need.patient_id,
                Outcome.reported_need_id == need.id,
            )
        )
        is not None
    )


def _lock_current_navigator_authority(
    session: Session, *, organization_id: UUID, user_id: UUID
) -> RoleAssignment:
    at = datetime.now(UTC)
    assignment = session.scalar(
        select(RoleAssignment)
        .where(
            RoleAssignment.organization_id == organization_id,
            RoleAssignment.user_id == user_id,
            RoleAssignment.role == UserRole.NAVIGATOR,
            RoleAssignment.granted_at <= at,
            (RoleAssignment.revoked_at.is_(None) | (at < RoleAssignment.revoked_at)),
        )
        .order_by(RoleAssignment.granted_at.desc(), RoleAssignment.id.asc())
        .limit(1)
    )
    if assignment is None:
        raise TaskForbidden("Current navigator authority is required")
    return assignment


def _approved_task_title(
    session: Session,
    *,
    proposal: ProposedChange,
    organization_id: UUID,
    task_id: UUID,
) -> str:
    state = session.scalar(
        select(effective_proposed_change_state.c.effective_state).where(
            effective_proposed_change_state.c.organization_id == organization_id,
            effective_proposed_change_state.c.id == proposal.id,
        )
    )
    if (
        proposal.navigation_task_id != task_id
        or proposal.change_type is not ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK
        or proposal.value_schema_id != "ojcc.authorize-navigation-task"
        or proposal.value_schema_version not in (1, 2)
        or state != "approved"
    ):
        raise TaskConflict(
            "proposal_not_approved",
            "The exact task proposal is not currently approved and supported",
        )
    value = proposal.proposed_value
    title = value.get("title") if isinstance(value, Mapping) else None
    if not isinstance(title, str) or not title.strip() or len(title) > 255:
        raise TaskConflict("proposal_not_approved", "Task proposal title is invalid")
    if proposal.value_schema_version == 1:
        if set(value) != {"title"}:
            raise TaskConflict("proposal_not_approved", "Task proposal value is invalid")
        return title

    proposed_resources = value.get("resources")
    if set(value) != {"title", "resources"} or not isinstance(proposed_resources, list):
        raise TaskConflict("proposal_not_approved", "Task resource proposal is invalid")
    materialized = session.scalars(
        select(NavigationTaskResource)
        .where(
            NavigationTaskResource.organization_id == organization_id,
            NavigationTaskResource.navigation_task_id == task_id,
            NavigationTaskResource.proposed_change_id == proposal.id,
        )
        .order_by(NavigationTaskResource.id)
    ).all()
    snapshots = {
        str(resource.resource_id): {
            "resource_id": str(resource.resource_id),
            "name": resource.resource_name_snapshot,
            "category": resource.resource_category_snapshot,
            "url": resource.resource_url_snapshot,
            "metadata": resource.resource_metadata_snapshot,
            "match_rationale": resource.match_rationale_snapshot,
        }
        for resource in materialized
    }
    if any(
        not isinstance(item, Mapping)
        or set(item)
        != {"resource_id", "name", "category", "url", "metadata", "match_rationale"}
        or snapshots.get(str(item.get("resource_id"))) != dict(item)
        for item in proposed_resources
    ) or len(snapshots) != len(proposed_resources):
        raise TaskConflict(
            "proposal_not_approved",
            "Task resource snapshots do not match the approved proposal",
        )
    return title


def _normalize_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Due time must include a timezone offset")
    return value.astimezone(UTC)


def _result(
    session: Session, task: NavigationTask, *, replayed: bool
) -> TaskCommandResult:
    request_id = session.scalar(
        select(FollowUpRequest.id).where(
            FollowUpRequest.organization_id == task.organization_id,
            FollowUpRequest.navigation_task_id == task.id,
        )
    )
    return TaskCommandResult(
        task_id=task.id,
        need_id=task.reported_need_id,
        status=task.status,
        assignee_user_id=task.assignee_user_id,
        due_at=task.due_at,
        authorized_proposed_change_id=task.authorized_proposed_change_id,
        completed_at=task.completed_at,
        follow_up_request_id=request_id,
        replayed=replayed,
    )


def map_task_database_error(error: DBAPIError) -> TaskConflict | TaskForbidden | None:
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    if sqlstate in {"40001", "40P01", "55P03"}:
        return TaskConflict("concurrent_change", "Concurrent task change; retry safely")
    message = str(getattr(error, "orig", error))
    if "active navigator authority" in message:
        return TaskForbidden("Current navigator authority is required")
    if "closed" in message:
        return TaskConflict("need_closed", "Reported need is already closed")
    if "approved" in message or "supported task proposal" in message:
        return TaskConflict(
            "proposal_not_approved",
            "The exact task proposal is not currently approved and supported",
        )
    if "transition" in message or "binding" in message:
        return TaskConflict("task_state_conflict", "Task state changed concurrently")
    return None
