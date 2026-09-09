from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from app.db.models import (
    ApprovalDecision,
    CheckInDefinition,
    CheckInSubmission,
    FollowUpRequest,
    FollowUpResponse,
    NavigationTask,
    NavigationTaskResource,
    Outcome,
    ProposedChange,
    ReportedNeed,
    SyntheticPatient,
)
from app.db.repositories import EffectiveProposedChangeRecord
from app.domain.enums import ApprovalChangeType

ComparisonStatus = Literal["available", "insufficient_history", "not_comparable"]
ProvenanceKind = Literal[
    "source_submission", "inherited_history", "stored_need_evidence"
]


class WorkspaceNeedRead(BaseModel):
    id: UUID
    patient_id: UUID
    care_episode_id: UUID
    patient_display_name: str
    kind: str
    raw_status: str
    effective_state: str
    created_at: datetime
    reopened_from_need_id: UUID | None


class WorkspaceEvidenceRead(BaseModel):
    source_submission_id: UUID | None
    field_identifier: str
    value_present: bool
    value: JsonValue
    display_text: str
    provenance_kind: ProvenanceKind


class ComparisonDeltaRead(BaseModel):
    field_identifier: str
    previous_present: bool
    current_present: bool
    previous_value: JsonValue
    current_value: JsonValue


class SubmissionComparisonRead(BaseModel):
    status: ComparisonStatus
    label: str
    previous_submission_id: UUID | None = None
    current_submission_id: UUID | None = None
    deltas: list[ComparisonDeltaRead] = Field(default_factory=list)


class WorkspaceComparisonsRead(BaseModel):
    correction: SubmissionComparisonRead
    between_check_ins: SubmissionComparisonRead


class WorkspaceApprovalPolicyRead(BaseModel):
    id: UUID
    version: int
    allow_self_approval: bool
    required_approval_count: int
    required_approver_role: str
    deterministic_severity_threshold: str | None


class WorkspaceApprovalDecisionRead(BaseModel):
    id: UUID
    authorized_by_user_id: UUID
    qualifying_role_assignment_id: UUID
    qualifying_role_snapshot: str
    decision: str
    authorized_at: datetime
    reason: str | None


class WorkspaceResourceRead(BaseModel):
    id: UUID
    resource_id: UUID
    name: str
    category: str
    url: str | None
    metadata: dict[str, JsonValue]
    match_rationale: str
    proposed_at: datetime
    approved_at: datetime | None
    delivered_at: datetime | None


class WorkspaceProposalRead(BaseModel):
    id: UUID
    root_proposal_id: UUID
    supersedes_proposed_change_id: UUID | None
    proposed_at: datetime
    state: str
    supported: bool
    reviewable: bool
    execution_authorized: bool
    unsupported_reason: str | None
    change_type: str
    title: str | None
    proposed_value: dict[str, JsonValue]
    rationale: str
    value_schema_id: str
    value_schema_version: int
    policy: WorkspaceApprovalPolicyRead
    decisions: list[WorkspaceApprovalDecisionRead]
    resources: list[WorkspaceResourceRead]


class WorkspaceTaskRead(BaseModel):
    id: UUID
    title: str
    status: str
    assignee_user_id: UUID | None
    due_at: datetime | None
    authorized_proposed_change_id: UUID | None
    completed_at: datetime | None
    created_at: datetime
    proposals: list[WorkspaceProposalRead]


class WorkspaceFollowUpRead(BaseModel):
    request_id: UUID
    navigation_task_id: UUID
    requested_by_user_id: UUID
    requested_at: datetime
    prompt_version: int
    status: Literal["awaiting_response", "answered", "unavailable_need_closed"]
    response: str | None
    response_note: str | None
    responded_at: datetime | None


class WorkspaceOutcomeRead(BaseModel):
    id: UUID
    disposition: str
    note: str | None
    recorded_by_user_id: UUID
    recorded_at: datetime


class NavigatorTimelineEventRead(BaseModel):
    id: UUID
    kind: str
    occurred_at: datetime
    label: str


class NavigatorNeedWorkspaceRead(BaseModel):
    need: WorkspaceNeedRead
    evidence: list[WorkspaceEvidenceRead]
    comparisons: WorkspaceComparisonsRead
    tasks: list[WorkspaceTaskRead]
    follow_ups: list[WorkspaceFollowUpRead]
    outcome: WorkspaceOutcomeRead | None
    timeline: list[NavigatorTimelineEventRead]


def build_navigator_workspace(
    *,
    need: ReportedNeed,
    effective_state: str,
    patient: SyntheticPatient,
    submissions: Sequence[CheckInSubmission],
    definitions: Sequence[CheckInDefinition],
    tasks: Sequence[NavigationTask],
    proposals: Sequence[EffectiveProposedChangeRecord],
    decisions: Sequence[ApprovalDecision],
    resources: Sequence[NavigationTaskResource],
    follow_up_requests: Sequence[FollowUpRequest],
    follow_up_responses: Sequence[FollowUpResponse],
    outcome: Outcome | None,
) -> NavigatorNeedWorkspaceRead:
    submission_by_id = {submission.id: submission for submission in submissions}
    definition_by_id = {definition.id: definition for definition in definitions}
    source_leaf = _source_leaf(need, submission_by_id)
    evidence = _workspace_evidence(need, source_leaf)
    comparisons = WorkspaceComparisonsRead(
        correction=_correction_comparison(
            source_leaf, submission_by_id, definition_by_id
        ),
        between_check_ins=_between_check_ins_comparison(
            submissions, definition_by_id
        ),
    )
    decisions_by_proposal: dict[UUID, list[ApprovalDecision]] = {}
    for decision in decisions:
        decisions_by_proposal.setdefault(decision.proposed_change_id, []).append(decision)
    resources_by_proposal: dict[UUID, list[NavigationTaskResource]] = {}
    for resource in resources:
        resources_by_proposal.setdefault(resource.proposed_change_id, []).append(resource)
    proposal_by_task: dict[UUID, list[EffectiveProposedChangeRecord]] = {}
    for record in proposals:
        task_id = record.proposal.navigation_task_id
        if task_id is not None:
            proposal_by_task.setdefault(task_id, []).append(record)
    proposal_map = {record.proposal.id: record.proposal for record in proposals}

    response_by_request = {
        response.follow_up_request_id: response for response in follow_up_responses
    }
    return NavigatorNeedWorkspaceRead(
        need=WorkspaceNeedRead(
            id=need.id,
            patient_id=need.patient_id,
            care_episode_id=need.care_episode_id,
            patient_display_name=patient.display_name,
            kind=need.kind,
            raw_status=need.status.value,
            effective_state=effective_state,
            created_at=need.created_at,
            reopened_from_need_id=need.reopened_from_need_id,
        ),
        evidence=evidence,
        comparisons=comparisons,
        tasks=[
            WorkspaceTaskRead(
                id=task.id,
                title=task.title,
                status=task.status.value,
                assignee_user_id=task.assignee_user_id,
                due_at=task.due_at,
                authorized_proposed_change_id=task.authorized_proposed_change_id,
                completed_at=task.completed_at,
                created_at=task.created_at,
                proposals=[
                    _proposal_read(
                        record,
                        proposal_map=proposal_map,
                        decisions=decisions_by_proposal.get(record.proposal.id, []),
                        resources=resources_by_proposal.get(record.proposal.id, []),
                    )
                    for record in proposal_by_task.get(task.id, [])
                ],
            )
            for task in tasks
        ],
        follow_ups=[
            _follow_up_read(
                request,
                response_by_request.get(request.id),
                need_is_closed=outcome is not None,
            )
            for request in follow_up_requests
        ],
        outcome=(
            WorkspaceOutcomeRead(
                id=outcome.id,
                disposition=outcome.disposition.value,
                note=outcome.note,
                recorded_by_user_id=outcome.recorded_by_user_id,
                recorded_at=outcome.recorded_at,
            )
            if outcome is not None
            else None
        ),
        timeline=[],
    )


def _source_leaf(
    need: ReportedNeed, submission_by_id: Mapping[UUID, CheckInSubmission]
) -> CheckInSubmission | None:
    if need.source_submission_id is None:
        return None
    source = submission_by_id.get(need.source_submission_id)
    if source is None:
        return None
    root = source
    seen = {root.id}
    while root.supersedes_submission_id is not None:
        predecessor = submission_by_id.get(root.supersedes_submission_id)
        if predecessor is None or predecessor.id in seen:
            break
        root = predecessor
        seen.add(root.id)
    return _chain_leaf(root, submission_by_id)


def _chain_leaf(
    root: CheckInSubmission,
    submission_by_id: Mapping[UUID, CheckInSubmission],
) -> CheckInSubmission:
    successor_by_predecessor = {
        submission.supersedes_submission_id: submission
        for submission in submission_by_id.values()
        if submission.supersedes_submission_id is not None
    }
    leaf = root
    seen = {leaf.id}
    while leaf.id in successor_by_predecessor:
        successor = successor_by_predecessor[leaf.id]
        if successor.id in seen:
            break
        leaf = successor
        seen.add(leaf.id)
    return leaf


def _submission_fields(submission: CheckInSubmission) -> dict[str, JsonValue]:
    answers = submission.answers if isinstance(submission.answers, Mapping) else {}
    items = answers.get("items")
    if isinstance(items, list):
        fields: dict[str, JsonValue] = {}
        for item in items:
            if not isinstance(item, Mapping):
                continue
            identifier = item.get("link_id", item.get("question_id", item.get("field")))
            if identifier is not None and "value" in item:
                fields[str(identifier)] = item["value"]  # type: ignore[assignment]
        return fields
    ignored = {"free_text", "provenance", "questionnaire_canonical", "questionnaire_version"}
    return {
        str(key): value  # type: ignore[misc]
        for key, value in answers.items()
        if key not in ignored
    }


def _workspace_evidence(
    need: ReportedNeed, source: CheckInSubmission | None
) -> list[WorkspaceEvidenceRead]:
    if source is not None:
        return [
            WorkspaceEvidenceRead(
                source_submission_id=source.id,
                field_identifier=field,
                value_present=True,
                value=value,
                display_text=_display_value(value),
                provenance_kind="source_submission",
            )
            for field, value in sorted(_submission_fields(source).items())
        ]
    provenance: ProvenanceKind = (
        "inherited_history" if need.reopened_from_need_id is not None else "stored_need_evidence"
    )
    return [
        WorkspaceEvidenceRead(
            source_submission_id=None,
            field_identifier=str(item.get("field", item.get("question_id", "stored_evidence"))),
            value_present="value" in item,
            value=item.get("value"),  # type: ignore[arg-type]
            display_text=str(item.get("text", _display_value(item.get("value")))),
            provenance_kind=provenance,
        )
        for item in need.evidence
        if isinstance(item, Mapping)
    ]


def _display_value(value: object) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None:
        return "Not provided"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _correction_comparison(
    current: CheckInSubmission | None,
    submission_by_id: Mapping[UUID, CheckInSubmission],
    definition_by_id: Mapping[UUID, CheckInDefinition],
) -> SubmissionComparisonRead:
    label = "Correction to the same check-in."
    if current is None or current.supersedes_submission_id is None:
        return SubmissionComparisonRead(status="insufficient_history", label=label)
    previous = submission_by_id.get(current.supersedes_submission_id)
    if previous is None:
        return SubmissionComparisonRead(status="insufficient_history", label=label)
    return _compare_submissions(previous, current, definition_by_id, label=label)


def _between_check_ins_comparison(
    submissions: Sequence[CheckInSubmission],
    definition_by_id: Mapping[UUID, CheckInDefinition],
) -> SubmissionComparisonRead:
    label = "Between independent check-ins."
    submission_by_id = {submission.id: submission for submission in submissions}
    roots = sorted(
        (
            submission
            for submission in submissions
            if submission.supersedes_submission_id is None
        ),
        key=lambda submission: (submission.submitted_at, str(submission.id)),
    )
    if len(roots) < 2:
        return SubmissionComparisonRead(status="insufficient_history", label=label)
    previous_root, current_root = roots[-2:]
    return _compare_submissions(
        _chain_leaf(previous_root, submission_by_id),
        _chain_leaf(current_root, submission_by_id),
        definition_by_id,
        label=label,
    )


def _compare_submissions(
    previous: CheckInSubmission,
    current: CheckInSubmission,
    definition_by_id: Mapping[UUID, CheckInDefinition],
    *,
    label: str,
) -> SubmissionComparisonRead:
    previous_definition = definition_by_id.get(previous.check_in_definition_id)
    current_definition = definition_by_id.get(current.check_in_definition_id)
    if (
        previous_definition is None
        or current_definition is None
        or (
            previous_definition.slug,
            previous_definition.version,
        )
        != (current_definition.slug, current_definition.version)
    ):
        return SubmissionComparisonRead(
            status="not_comparable",
            label=label,
            previous_submission_id=previous.id,
            current_submission_id=current.id,
        )
    previous_fields = _submission_fields(previous)
    current_fields = _submission_fields(current)
    deltas = [
        ComparisonDeltaRead(
            field_identifier=field,
            previous_present=field in previous_fields,
            current_present=field in current_fields,
            previous_value=previous_fields.get(field),
            current_value=current_fields.get(field),
        )
        for field in sorted(previous_fields.keys() | current_fields.keys())
        if (field in previous_fields) != (field in current_fields)
        or previous_fields.get(field) != current_fields.get(field)
    ]
    return SubmissionComparisonRead(
        status="available",
        label=label,
        previous_submission_id=previous.id,
        current_submission_id=current.id,
        deltas=deltas,
    )


def _proposal_read(
    record: EffectiveProposedChangeRecord,
    *,
    proposal_map: Mapping[UUID, ProposedChange],
    decisions: Sequence[ApprovalDecision],
    resources: Sequence[NavigationTaskResource],
) -> WorkspaceProposalRead:
    proposal = record.proposal
    supported, unsupported_reason = _proposal_support(proposal, resources)
    title = proposal.proposed_value.get("title")
    return WorkspaceProposalRead(
        id=proposal.id,
        root_proposal_id=_proposal_root(proposal, proposal_map),
        supersedes_proposed_change_id=proposal.supersedes_proposed_change_id,
        proposed_at=proposal.proposed_at,
        state=record.effective_state,
        supported=supported,
        reviewable=supported and record.effective_state == "pending",
        execution_authorized=supported and record.effective_state == "approved",
        unsupported_reason=unsupported_reason,
        change_type=proposal.change_type.value,
        title=title if isinstance(title, str) else None,
        proposed_value=proposal.proposed_value,  # type: ignore[arg-type]
        rationale=proposal.rationale,
        value_schema_id=proposal.value_schema_id,
        value_schema_version=proposal.value_schema_version,
        policy=WorkspaceApprovalPolicyRead(
            id=proposal.approval_policy_id,
            version=proposal.approval_policy_version,
            allow_self_approval=proposal.allow_self_approval_snapshot,
            required_approval_count=proposal.required_approval_count_snapshot,
            required_approver_role=proposal.required_approver_role_snapshot.value,
            deterministic_severity_threshold=(
                proposal.deterministic_severity_threshold_snapshot.value
                if proposal.deterministic_severity_threshold_snapshot is not None
                else None
            ),
        ),
        decisions=[
            WorkspaceApprovalDecisionRead(
                id=decision.id,
                authorized_by_user_id=decision.authorized_by_user_id,
                qualifying_role_assignment_id=decision.qualifying_role_assignment_id,
                qualifying_role_snapshot=decision.qualifying_role_snapshot.value,
                decision=decision.decision.value,
                authorized_at=decision.authorized_at,
                reason=decision.reason,
            )
            for decision in decisions
        ],
        resources=[
            WorkspaceResourceRead(
                id=resource.id,
                resource_id=resource.resource_id,
                name=resource.resource_name_snapshot,
                category=resource.resource_category_snapshot,
                url=resource.resource_url_snapshot,
                metadata=resource.resource_metadata_snapshot,  # type: ignore[arg-type]
                match_rationale=resource.match_rationale_snapshot,
                proposed_at=resource.proposed_at,
                approved_at=resource.approved_at,
                delivered_at=resource.delivered_at,
            )
            for resource in resources
        ],
    )


def _proposal_support(
    proposal: ProposedChange, resources: Sequence[NavigationTaskResource]
) -> tuple[bool, str | None]:
    if (
        proposal.change_type is not ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK
        or proposal.value_schema_id != "ojcc.authorize-navigation-task"
        or proposal.value_schema_version not in (1, 2)
    ):
        return False, "Unsupported task proposal schema."
    value = proposal.proposed_value
    if not isinstance(value, Mapping) or not isinstance(value.get("title"), str):
        return False, "Task proposal value is invalid."
    if proposal.value_schema_version == 1:
        return (
            (True, None)
            if set(value) == {"title"}
            else (False, "Task proposal value is invalid.")
        )
    proposed_resources = value.get("resources")
    if set(value) != {"title", "resources"} or not isinstance(proposed_resources, list):
        return False, "Task resource proposal value is invalid."
    snapshots = {
        str(resource.resource_id): {
            "resource_id": str(resource.resource_id),
            "name": resource.resource_name_snapshot,
            "category": resource.resource_category_snapshot,
            "url": resource.resource_url_snapshot,
            "metadata": resource.resource_metadata_snapshot,
            "match_rationale": resource.match_rationale_snapshot,
        }
        for resource in resources
        if resource.navigation_task_id == proposal.navigation_task_id
        and resource.proposed_change_id == proposal.id
    }
    if any(
        not isinstance(item, Mapping)
        or set(item)
        != {"resource_id", "name", "category", "url", "metadata", "match_rationale"}
        or snapshots.get(str(item.get("resource_id"))) != dict(item)
        for item in proposed_resources
    ) or len(snapshots) != len(proposed_resources):
        return False, "Task resource snapshots do not match the proposal."
    return True, None


def _proposal_root(
    proposal: ProposedChange, proposal_map: Mapping[UUID, ProposedChange]
) -> UUID:
    current = proposal
    seen = {current.id}
    while current.supersedes_proposed_change_id is not None:
        predecessor = proposal_map.get(current.supersedes_proposed_change_id)
        if predecessor is None or predecessor.id in seen:
            break
        current = predecessor
        seen.add(current.id)
    return current.id


def _follow_up_read(
    request: FollowUpRequest,
    response: FollowUpResponse | None,
    *,
    need_is_closed: bool,
) -> WorkspaceFollowUpRead:
    status: Literal["awaiting_response", "answered", "unavailable_need_closed"]
    if response is not None:
        status = "answered"
    elif need_is_closed:
        status = "unavailable_need_closed"
    else:
        status = "awaiting_response"
    return WorkspaceFollowUpRead(
        request_id=request.id,
        navigation_task_id=request.navigation_task_id,
        requested_by_user_id=request.requested_by_user_id,
        requested_at=request.requested_at,
        prompt_version=request.prompt_version,
        status=status,
        response=response.response.value if response is not None else None,
        response_note=response.note if response is not None else None,
        responded_at=response.submitted_at if response is not None else None,
    )
