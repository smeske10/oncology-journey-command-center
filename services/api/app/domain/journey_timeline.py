"""Typed audience-specific journey timeline projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import (
    ApprovalDecision,
    AuditEvent,
    CheckInSubmission,
    FollowUpRequest,
    FollowUpResponse,
    NavigationTask,
    Outcome,
    ReportedNeed,
)
from app.db.repositories import EffectiveProposedChangeRecord
from app.domain.enums import (
    ApprovalDecisionValue,
    EffectiveProposalState,
    FollowUpResponseValue,
    OutcomeDisposition,
)
from app.domain.follow_ups import FOLLOW_UP_PROMPT

CAUSAL_RANK = {
    "check_in_submitted": 10,
    "check_in_corrected": 20,
    "need_reported": 30,
    "proposal_created": 40,
    "proposal_decided": 50,
    "task_claimed": 60,
    "task_started": 70,
    "task_completed": 80,
    "follow_up_requested": 90,
    "follow_up_responded": 100,
    "outcome_recorded": 110,
    "task_cancelled": 120,
}


class CheckInDetail(BaseModel):
    correction_of_submission_id: UUID | None
    inherited: bool


class NeedDetail(BaseModel):
    practical_need_label: str
    inherited: bool


class ProposalCreatedDetail(BaseModel):
    proposal_state: EffectiveProposalState
    value_schema_id: str
    value_schema_version: int


class ProposalDecidedDetail(BaseModel):
    decision: ApprovalDecisionValue


class TaskProgressDetail(BaseModel):
    status: Literal["assigned", "in_progress", "completed"]


class FollowUpRequestedDetail(BaseModel):
    prompt_version: int
    prompt: str


class FollowUpRespondedDetail(BaseModel):
    response: FollowUpResponseValue
    note: str | None


class NavigatorOutcomeDetail(BaseModel):
    disposition: OutcomeDisposition
    note: str | None


class PatientOutcomeDetail(BaseModel):
    disposition: OutcomeDisposition


class TaskCancelledDetail(BaseModel):
    reason: Literal["need_closed"]


class TimelineEventBase(BaseModel):
    event_id: str
    occurred_at: datetime
    source_id: UUID
    need_id: UUID | None
    task_id: UUID | None
    summary: str


class CheckInSubmittedEvent(TimelineEventBase):
    kind: Literal["check_in_submitted"] = "check_in_submitted"
    source_type: Literal["check_in_submission"] = "check_in_submission"
    detail: CheckInDetail


class CheckInCorrectedEvent(TimelineEventBase):
    kind: Literal["check_in_corrected"] = "check_in_corrected"
    source_type: Literal["check_in_submission"] = "check_in_submission"
    detail: CheckInDetail


class NeedReportedEvent(TimelineEventBase):
    kind: Literal["need_reported"] = "need_reported"
    source_type: Literal["reported_need"] = "reported_need"
    detail: NeedDetail


class ProposalCreatedEvent(TimelineEventBase):
    kind: Literal["proposal_created"] = "proposal_created"
    source_type: Literal["proposed_change"] = "proposed_change"
    detail: ProposalCreatedDetail


class ProposalDecidedEvent(TimelineEventBase):
    kind: Literal["proposal_decided"] = "proposal_decided"
    source_type: Literal["approval_decision"] = "approval_decision"
    detail: ProposalDecidedDetail


class TaskClaimedEvent(TimelineEventBase):
    kind: Literal["task_claimed"] = "task_claimed"
    source_type: Literal["audit_event"] = "audit_event"
    detail: TaskProgressDetail


class TaskStartedEvent(TimelineEventBase):
    kind: Literal["task_started"] = "task_started"
    source_type: Literal["audit_event"] = "audit_event"
    detail: TaskProgressDetail


class TaskCompletedEvent(TimelineEventBase):
    kind: Literal["task_completed"] = "task_completed"
    source_type: Literal["audit_event"] = "audit_event"
    detail: TaskProgressDetail


class FollowUpRequestedEvent(TimelineEventBase):
    kind: Literal["follow_up_requested"] = "follow_up_requested"
    source_type: Literal["follow_up_request"] = "follow_up_request"
    detail: FollowUpRequestedDetail


class FollowUpRespondedEvent(TimelineEventBase):
    kind: Literal["follow_up_responded"] = "follow_up_responded"
    source_type: Literal["follow_up_response"] = "follow_up_response"
    detail: FollowUpRespondedDetail


class NavigatorOutcomeRecordedEvent(TimelineEventBase):
    kind: Literal["outcome_recorded"] = "outcome_recorded"
    source_type: Literal["outcome"] = "outcome"
    detail: NavigatorOutcomeDetail


class PatientOutcomeRecordedEvent(TimelineEventBase):
    kind: Literal["outcome_recorded"] = "outcome_recorded"
    source_type: Literal["outcome"] = "outcome"
    detail: PatientOutcomeDetail


class TaskCancelledEvent(TimelineEventBase):
    kind: Literal["task_cancelled"] = "task_cancelled"
    source_type: Literal["audit_event"] = "audit_event"
    detail: TaskCancelledDetail


NavigatorTimelineEvent = Annotated[
    CheckInSubmittedEvent
    | CheckInCorrectedEvent
    | NeedReportedEvent
    | ProposalCreatedEvent
    | ProposalDecidedEvent
    | TaskClaimedEvent
    | TaskStartedEvent
    | TaskCompletedEvent
    | FollowUpRequestedEvent
    | FollowUpRespondedEvent
    | NavigatorOutcomeRecordedEvent
    | TaskCancelledEvent,
    Field(discriminator="kind"),
]

PatientTimelineEvent = Annotated[
    CheckInSubmittedEvent
    | CheckInCorrectedEvent
    | NeedReportedEvent
    | TaskClaimedEvent
    | TaskStartedEvent
    | TaskCompletedEvent
    | FollowUpRequestedEvent
    | FollowUpRespondedEvent
    | PatientOutcomeRecordedEvent
    | TaskCancelledEvent,
    Field(discriminator="kind"),
]


def build_patient_timeline(
    *,
    submissions: Sequence[CheckInSubmission],
    needs: Sequence[ReportedNeed],
    tasks: Sequence[NavigationTask],
    audits: Sequence[AuditEvent],
    requests: Sequence[FollowUpRequest],
    responses: Sequence[FollowUpResponse],
    outcomes: Sequence[Outcome],
) -> list[PatientTimelineEvent]:
    events: list[PatientTimelineEvent] = []
    events.extend(_submission_events(submissions, inherited=False))
    events.extend(_need_events(needs))
    task_by_id = {task.id: task for task in tasks}
    events.extend(_task_audit_events(audits, task_by_id=task_by_id))
    events.extend(_follow_up_events(requests, responses))
    events.extend(_patient_outcome_events(outcomes))
    return sorted(events, key=_sort_key)


def build_navigator_timeline(
    *,
    need: ReportedNeed,
    need_lineage: Sequence[ReportedNeed],
    submissions: Sequence[CheckInSubmission],
    tasks: Sequence[NavigationTask],
    proposals: Sequence[EffectiveProposedChangeRecord],
    decisions: Sequence[ApprovalDecision],
    audits: Sequence[AuditEvent],
    requests: Sequence[FollowUpRequest],
    responses: Sequence[FollowUpResponse],
    outcome: Outcome | None,
) -> list[NavigatorTimelineEvent]:
    events: list[NavigatorTimelineEvent] = []
    lineage = _selected_need_lineage(need, need_lineage)
    included_submission_ids: set[UUID] = set()
    for lineage_need in lineage:
        inherited = lineage_need.id != need.id
        source_records = [
            submission
            for submission in _source_lineage(lineage_need, submissions)
            if submission.id not in included_submission_ids
        ]
        included_submission_ids.update(submission.id for submission in source_records)
        events.extend(_submission_events(source_records, inherited=inherited))
    events.extend(
        _need_events(
            lineage,
            inherited_ids={
                lineage_need.id
                for lineage_need in lineage
                if lineage_need.id != need.id or need.reopened_from_need_id is not None
            },
        )
    )
    task_by_id = {task.id: task for task in tasks}
    proposal_by_id = {record.proposal.id: record for record in proposals}
    for record in proposals:
        proposal = record.proposal
        events.append(
            ProposalCreatedEvent(
                event_id=_event_id("proposal_created", proposal.id),
                occurred_at=proposal.proposed_at,
                source_id=proposal.id,
                need_id=need.id,
                task_id=proposal.navigation_task_id,
                summary="Task proposal created.",
                detail=ProposalCreatedDetail(
                    proposal_state=EffectiveProposalState(record.effective_state),
                    value_schema_id=proposal.value_schema_id,
                    value_schema_version=proposal.value_schema_version,
                ),
            )
        )
    for decision in decisions:
        proposal = proposal_by_id.get(decision.proposed_change_id)
        if proposal is None:
            continue
        events.append(
            ProposalDecidedEvent(
                event_id=_event_id("proposal_decided", decision.id),
                occurred_at=decision.authorized_at,
                source_id=decision.id,
                need_id=need.id,
                task_id=proposal.proposal.navigation_task_id,
                summary=f"Task proposal {decision.decision.value}.",
                detail=ProposalDecidedDetail(
                    decision=ApprovalDecisionValue(decision.decision)
                ),
            )
        )
    events.extend(_task_audit_events(audits, task_by_id=task_by_id))
    events.extend(_follow_up_events(requests, responses))
    if outcome is not None:
        events.append(
            NavigatorOutcomeRecordedEvent(
                event_id=_event_id("outcome_recorded", outcome.id),
                occurred_at=outcome.recorded_at,
                source_id=outcome.id,
                need_id=outcome.reported_need_id,
                task_id=None,
                summary=_outcome_summary(outcome.disposition.value),
                detail=NavigatorOutcomeDetail(
                    disposition=OutcomeDisposition(outcome.disposition),
                    note=outcome.note,
                ),
            )
        )
    return sorted(events, key=_sort_key)


def _submission_events(
    submissions: Sequence[CheckInSubmission],
    *,
    inherited: bool,
) -> list[CheckInSubmittedEvent | CheckInCorrectedEvent]:
    return [
        (
            CheckInCorrectedEvent(
                event_id=_event_id("check_in_corrected", submission.id),
                occurred_at=submission.submitted_at,
                source_id=submission.id,
                need_id=None,
                task_id=None,
                summary="Check-in correction submitted.",
                detail=CheckInDetail(
                    correction_of_submission_id=submission.supersedes_submission_id,
                    inherited=inherited,
                ),
            )
            if submission.supersedes_submission_id is not None
            else CheckInSubmittedEvent(
                event_id=_event_id("check_in_submitted", submission.id),
                occurred_at=submission.submitted_at,
                source_id=submission.id,
                need_id=None,
                task_id=None,
                summary="Check-in submitted.",
                detail=CheckInDetail(
                    correction_of_submission_id=None,
                    inherited=inherited,
                ),
            )
        )
        for submission in submissions
    ]


def _need_events(
    needs: Sequence[ReportedNeed], *, inherited_ids: set[UUID] | None = None
) -> list[NeedReportedEvent]:
    inherited_ids = inherited_ids or set()
    return [
        NeedReportedEvent(
            event_id=_event_id("need_reported", need.id),
            occurred_at=need.created_at,
            source_id=need.id,
            need_id=need.id,
            task_id=None,
            summary=f"{_need_label(need.kind)} need reported.",
            detail=NeedDetail(
                practical_need_label=_need_label(need.kind),
                inherited=(
                    need.id in inherited_ids or need.reopened_from_need_id is not None
                ),
            ),
        )
        for need in needs
    ]


def _task_audit_events(
    audits: Sequence[AuditEvent],
    *,
    task_by_id: Mapping[UUID, NavigationTask],
) -> list[TaskClaimedEvent | TaskStartedEvent | TaskCompletedEvent | TaskCancelledEvent]:
    events: list[
        TaskClaimedEvent | TaskStartedEvent | TaskCompletedEvent | TaskCancelledEvent
    ] = []
    transitions = {
        "navigation_task_claimed": ("task_claimed", "assigned", "Navigation support claimed."),
        "navigation_task_started": (
            "task_started",
            "in_progress",
            "Navigation support started.",
        ),
        "navigation_task_completed": (
            "task_completed",
            "completed",
            "Navigation support task completed.",
        ),
    }
    for audit in audits:
        task = task_by_id.get(audit.entity_id)
        if task is None:
            continue
        transition = transitions.get(audit.event_type)
        if transition is not None:
            kind, status, summary = transition
            event_class = {
                "task_claimed": TaskClaimedEvent,
                "task_started": TaskStartedEvent,
                "task_completed": TaskCompletedEvent,
            }[kind]
            events.append(
                event_class(
                    event_id=_event_id(kind, audit.id),
                    occurred_at=audit.created_at,
                    source_id=audit.id,
                    need_id=task.reported_need_id,
                    task_id=task.id,
                    summary=summary,
                    detail=TaskProgressDetail(status=status),  # type: ignore[arg-type]
                )
            )
        elif audit.event_type == "task_cancelled_by_closure":
            events.append(
                TaskCancelledEvent(
                    event_id=_event_id("task_cancelled", audit.id),
                    occurred_at=audit.created_at,
                    source_id=audit.id,
                    need_id=task.reported_need_id,
                    task_id=task.id,
                    summary="Navigation support closed before task completion.",
                    detail=TaskCancelledDetail(reason="need_closed"),
                )
            )
    return events


def _follow_up_events(
    requests: Sequence[FollowUpRequest],
    responses: Sequence[FollowUpResponse],
) -> list[FollowUpRequestedEvent | FollowUpRespondedEvent]:
    request_by_id = {request.id: request for request in requests}
    events: list[FollowUpRequestedEvent | FollowUpRespondedEvent] = []
    for request in requests:
        events.append(
            FollowUpRequestedEvent(
                event_id=_event_id("follow_up_requested", request.id),
                occurred_at=request.requested_at,
                source_id=request.id,
                need_id=request.reported_need_id,
                task_id=request.navigation_task_id,
                summary="Follow-up requested.",
                detail=FollowUpRequestedDetail(
                    prompt_version=request.prompt_version,
                    prompt=FOLLOW_UP_PROMPT,
                ),
            )
        )
    for response in responses:
        request = request_by_id.get(response.follow_up_request_id)
        if request is None:
            continue
        events.append(
            FollowUpRespondedEvent(
                event_id=_event_id("follow_up_responded", response.id),
                occurred_at=response.submitted_at,
                source_id=response.id,
                need_id=request.reported_need_id,
                task_id=request.navigation_task_id,
                summary="Follow-up response recorded.",
                detail=FollowUpRespondedDetail(
                    response=FollowUpResponseValue(response.response),
                    note=response.note,
                ),
            )
        )
    return events


def _patient_outcome_events(outcomes: Sequence[Outcome]) -> list[PatientOutcomeRecordedEvent]:
    return [
        PatientOutcomeRecordedEvent(
            event_id=_event_id("outcome_recorded", outcome.id),
            occurred_at=outcome.recorded_at,
            source_id=outcome.id,
            need_id=outcome.reported_need_id,
            task_id=None,
            summary=_outcome_summary(outcome.disposition.value),
            detail=PatientOutcomeDetail(
                disposition=OutcomeDisposition(outcome.disposition)
            ),
        )
        for outcome in outcomes
    ]


def _source_lineage(
    need: ReportedNeed,
    submissions: Sequence[CheckInSubmission],
) -> list[CheckInSubmission]:
    if need.source_submission_id is None:
        return []
    by_id = {submission.id: submission for submission in submissions}
    included: set[UUID] = set()
    current_id: UUID | None = need.source_submission_id
    while current_id is not None and current_id in by_id and current_id not in included:
        included.add(current_id)
        current_id = by_id[current_id].supersedes_submission_id
    changed = True
    while changed:
        changed = False
        for submission in submissions:
            if (
                submission.supersedes_submission_id in included
                and submission.id not in included
            ):
                included.add(submission.id)
                changed = True
    return [submission for submission in submissions if submission.id in included]


def _selected_need_lineage(
    selected: ReportedNeed,
    candidates: Sequence[ReportedNeed],
) -> list[ReportedNeed]:
    by_id = {need.id: need for need in candidates}
    lineage = [selected]
    seen = {selected.id}
    predecessor_id = selected.reopened_from_need_id
    while predecessor_id is not None and predecessor_id not in seen:
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            break
        lineage.append(predecessor)
        seen.add(predecessor.id)
        predecessor_id = predecessor.reopened_from_need_id
    return lineage


def _need_label(kind: str) -> str:
    return {
        "transportation": "Transportation support",
        "recurrence": "Ongoing practical support",
    }.get(kind, "Practical support")


def _outcome_summary(disposition: str) -> str:
    return (
        "Navigation support closed as resolved."
        if disposition == "resolved"
        else "Navigation support closed without resolution."
    )


def _event_id(kind: str, source_id: UUID) -> str:
    return f"{kind}:{source_id}"


def _sort_key(
    event: NavigatorTimelineEvent | PatientTimelineEvent,
) -> tuple[datetime, int, str]:
    return (event.occurred_at, CAUSAL_RANK[event.kind], str(event.source_id))
