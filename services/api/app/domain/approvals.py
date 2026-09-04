"""Policy-snapshotted proposals and human approval commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    ApprovalDecision,
    ApprovalPolicy,
    NavigationTask,
    PatientMessage,
    ProposedChange,
    RoleAssignment,
    SafetySignal,
)
from app.domain.enums import (
    ApprovalChangeType,
    ApprovalDecisionValue,
    SafetySeverity,
)
from app.domain.safety import severity_rank

logger = logging.getLogger(__name__)

_PROPOSAL_CONFLICT_TRIGGER_DIAGNOSTICS = frozenset(
    {
        "Agents cannot propose dismissal",
        "Proposal must reference the canonical effective policy with matching snapshots",
        "Dismissal proposal requires a deterministic threshold",
        "Unknown or mismatched proposed-value schema identity",
        "Proposed value does not match its registered schema",
        "Proposed value does not match its registered dismissal schema",
        "Proposed value does not match its registered severity schema",
        "Proposed value does not match its registered task schema",
        "Proposed value does not match its registered message schema",
        "Proposal predecessor is outside the organization",
        "Only a pending or declined current proposal can be revised",
        "An applied proposal cannot be revised",
        "Proposal revision must preserve organization, target, and change type",
    }
)
_DUPLICATE_PROPOSAL_REVISION_CONSTRAINT = (
    "uq_proposed_change_supersedes_proposed_change_id"
)
_DUPLICATE_PROPOSAL_REVISION_DIAGNOSTIC = "Proposal predecessor already has a revision"
_DECISION_FORBIDDEN_TRIGGER_DIAGNOSTICS = frozenset(
    {
        "Role assignment does not qualify for this proposal",
        "The proposer cannot approve this change",
    }
)
_DECISION_CONFLICT_TRIGGER_DIAGNOSTICS = frozenset(
    {
        "Proposed change is outside the decision organization",
        "Approval decisions require a current pending proposal",
        "Proposal target is outside the organization",
        "Safety signal must be acknowledged before dismissal",
        "Resolved safety signal cannot be dismissed",
        "Safety signal is already dismissed",
        "Severity override value is invalid",
    }
)
_DUPLICATE_DECISION_CONSTRAINT = "uq_approval_decision_proposal_authorizer"
_DUPLICATE_DECISION_DIAGNOSTIC = (
    "A decision by this authorizer already exists for this proposal"
)


class ProposalNotFound(LookupError):
    """The proposal or target is outside the selected organization or absent."""


class ApprovalPolicyNotFound(LookupError):
    """No effective organization policy governs this change."""


class ApprovalConflict(ValueError):
    """The proposal is no longer pending or the command is a replay."""


class ApprovalForbidden(PermissionError):
    """The supplied human authority does not qualify for this decision."""


@dataclass(frozen=True)
class ProposalResult:
    proposal: ProposedChange
    state: str


@dataclass(frozen=True)
class DecisionResult:
    decision: ApprovalDecision
    proposal_state: str
    applied: bool


ValueValidator = Callable[[dict[str, Any]], None]
DISMISSAL_CATEGORIES = frozenset({"false_positive", "duplicate", "not_applicable"})


def _require_exact_keys(value: dict[str, Any], required: set[str]) -> None:
    if set(value) != required:
        raise ValueError(f"Proposed value requires exactly: {', '.join(sorted(required))}")


def _validate_dismissal_value(value: dict[str, Any]) -> None:
    _require_exact_keys(value, {"category"})
    category = value["category"]
    if not isinstance(category, str) or category not in DISMISSAL_CATEGORIES:
        raise ValueError("Dismissal category is not in the controlled vocabulary")


def _validate_severity_value(value: dict[str, Any]) -> None:
    _require_exact_keys(value, {"level"})
    level = value["level"]
    if not isinstance(level, str):
        raise ValueError("Severity override level must be text")
    SafetySeverity(level)


def _validate_nonblank_text_value(value: dict[str, Any], key: str) -> None:
    _require_exact_keys(value, {key})
    text_value = value[key]
    if not isinstance(text_value, str) or not text_value.strip():
        raise ValueError(f"Proposed value {key} must be nonblank text")


def _validate_navigation_task_value(value: dict[str, Any]) -> None:
    _validate_nonblank_text_value(value, "title")
    if len(value["title"]) > 255:
        raise ValueError("Navigation task title exceeds 255 characters")


def _validate_patient_message_value(value: dict[str, Any]) -> None:
    _validate_nonblank_text_value(value, "body")


VALUE_SCHEMA_REGISTRY: dict[tuple[ApprovalChangeType, str, int], ValueValidator] = {
    (ApprovalChangeType.DISMISS_SIGNAL, "ojcc.dismiss-signal", 1): _validate_dismissal_value,
    (
        ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY,
        "ojcc.override-signal-severity",
        1,
    ): _validate_severity_value,
    (
        ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK,
        "ojcc.authorize-navigation-task",
        1,
    ): _validate_navigation_task_value,
    (
        ApprovalChangeType.AUTHORIZE_PATIENT_MESSAGE,
        "ojcc.authorize-patient-message",
        1,
    ): _validate_patient_message_value,
}


def validate_proposed_value(
    change_type: ApprovalChangeType,
    value_schema_id: str,
    value_schema_version: int,
    proposed_value: dict[str, Any],
) -> None:
    validator = VALUE_SCHEMA_REGISTRY.get(
        (change_type, value_schema_id, value_schema_version)
    )
    if validator is None:
        raise ValueError("Unknown or mismatched proposed-value schema identity")
    validator(proposed_value)


def validate_target_shape(
    *,
    change_type: str | ApprovalChangeType,
    safety_signal_id: UUID | None,
    navigation_task_id: UUID | None,
    patient_message_id: UUID | None,
) -> None:
    targets = {
        "safety_signal_id": safety_signal_id,
        "navigation_task_id": navigation_task_id,
        "patient_message_id": patient_message_id,
    }
    if sum(value is not None for value in targets.values()) != 1:
        raise ValueError("Proposed change requires exactly one explicit target")
    expected = {
        ApprovalChangeType.DISMISS_SIGNAL: "safety_signal_id",
        ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY: "safety_signal_id",
        ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK: "navigation_task_id",
        ApprovalChangeType.AUTHORIZE_PATIENT_MESSAGE: "patient_message_id",
    }[ApprovalChangeType(change_type)]
    if targets[expected] is None:
        raise ValueError(f"Change type requires target {expected}")


def create_proposal(
    session: Session,
    *,
    organization_id: UUID,
    proposed_by_user_id: UUID,
    change_type: str | ApprovalChangeType,
    proposed_value: dict[str, Any],
    rationale: str,
    value_schema_id: str,
    value_schema_version: int,
    safety_signal_id: UUID | None,
    navigation_task_id: UUID | None,
    patient_message_id: UUID | None,
    supersedes_proposed_change_id: UUID | None,
) -> ProposalResult:
    selected_change_type = ApprovalChangeType(change_type)
    validate_target_shape(
        change_type=selected_change_type,
        safety_signal_id=safety_signal_id,
        navigation_task_id=navigation_task_id,
        patient_message_id=patient_message_id,
    )
    if not rationale.strip():
        raise ValueError("Proposal rationale must not be blank")
    if not value_schema_id.strip() or value_schema_version < 1:
        raise ValueError("Proposal value schema identity and positive version are required")
    validate_proposed_value(
        selected_change_type,
        value_schema_id.strip(),
        value_schema_version,
        proposed_value,
    )
    proposed_at = datetime.now(UTC)
    _require_target(
        session,
        organization_id=organization_id,
        safety_signal_id=safety_signal_id,
        navigation_task_id=navigation_task_id,
        patient_message_id=patient_message_id,
    )
    policy = session.scalars(
        select(ApprovalPolicy)
        .where(
            ApprovalPolicy.organization_id == organization_id,
            ApprovalPolicy.change_type == selected_change_type,
            ApprovalPolicy.effective_from <= proposed_at,
            (
                ApprovalPolicy.effective_to.is_(None)
                | (proposed_at < ApprovalPolicy.effective_to)
            ),
        )
        .order_by(ApprovalPolicy.version.desc())
    ).first()
    if policy is None:
        raise ApprovalPolicyNotFound("No effective approval policy for requested change")
    if (
        selected_change_type is ApprovalChangeType.DISMISS_SIGNAL
        and policy.deterministic_severity_threshold is None
    ):
        raise ApprovalPolicyNotFound("Dismissal policy requires a deterministic threshold")

    proposal = ProposedChange(
        organization_id=organization_id,
        proposed_by_user_id=proposed_by_user_id,
        proposed_at=proposed_at,
        change_type=selected_change_type,
        proposed_value=proposed_value,
        rationale=rationale.strip(),
        value_schema_id=value_schema_id.strip(),
        value_schema_version=value_schema_version,
        supersedes_proposed_change_id=supersedes_proposed_change_id,
        safety_signal_id=safety_signal_id,
        navigation_task_id=navigation_task_id,
        patient_message_id=patient_message_id,
        approval_policy_id=policy.id,
        approval_policy_version=policy.version,
        deterministic_severity_threshold_snapshot=policy.deterministic_severity_threshold,
        allow_self_approval_snapshot=policy.allow_self_approval,
        required_approval_count_snapshot=policy.required_approval_count,
        required_approver_role_snapshot=policy.required_approver_role,
    )
    try:
        with session.begin_nested():
            session.add(proposal)
            session.flush()
    except (IntegrityError, DBAPIError) as error:
        conflict = _proposal_domain_error(error)
        if conflict is None:
            logger.exception("Unexpected database error while creating a proposal")
            raise
        raise conflict from None
    return ProposalResult(proposal=proposal, state="pending")


def record_decision(
    session: Session,
    *,
    organization_id: UUID,
    proposed_change_id: UUID,
    authorized_by_user_id: UUID,
    qualifying_role_assignment_id: UUID,
    decision: str | ApprovalDecisionValue,
    reason: str | None,
) -> DecisionResult:
    decision_value = ApprovalDecisionValue(decision)
    if decision_value is ApprovalDecisionValue.DECLINED and not (reason and reason.strip()):
        raise ValueError("Decline reason is required")
    proposal = session.scalar(
        select(ProposedChange)
        .where(
            ProposedChange.organization_id == organization_id,
            ProposedChange.id == proposed_change_id,
        )
        .with_for_update()
    )
    if proposal is None:
        raise ProposalNotFound("Proposed change not found")
    authorized_at = datetime.now(UTC)
    assignment = session.scalar(
        select(RoleAssignment).where(
            RoleAssignment.id == qualifying_role_assignment_id,
            RoleAssignment.organization_id == organization_id,
            RoleAssignment.user_id == authorized_by_user_id,
            RoleAssignment.role == proposal.required_approver_role_snapshot,
            RoleAssignment.granted_at <= authorized_at,
            (
                RoleAssignment.revoked_at.is_(None)
                | (authorized_at < RoleAssignment.revoked_at)
            ),
        )
    )
    if assignment is None:
        raise ApprovalForbidden("Role assignment does not qualify for this proposal")
    if decision_value is ApprovalDecisionValue.APPROVED and _self_approval_is_forbidden(
        session, proposal
    ) and proposal.proposed_by_user_id == authorized_by_user_id:
        raise ApprovalForbidden("The proposer cannot approve this change")

    approval = ApprovalDecision(
        organization_id=organization_id,
        proposed_change_id=proposal.id,
        authorized_by_user_id=authorized_by_user_id,
        qualifying_role_assignment_id=assignment.id,
        qualifying_role_snapshot=assignment.role,
        decision=decision_value,
        authorized_at=authorized_at,
        reason=reason.strip() if reason else None,
    )
    try:
        with session.begin_nested():
            session.add(approval)
            session.flush()
    except (IntegrityError, DBAPIError) as error:
        domain_error = _decision_domain_error(error)
        if domain_error is None:
            logger.exception("Unexpected database error while recording an approval decision")
            raise
        raise domain_error from None

    state = proposal_state(session, organization_id, proposal.id)
    session.refresh(proposal)
    applied = False
    if proposal.safety_signal_id is not None:
        signal = session.get(SafetySignal, proposal.safety_signal_id)
        assert signal is not None
        session.refresh(signal)
        applied = proposal.id in {
            signal.dismissal_proposed_change_id,
            signal.current_severity_override_proposed_change_id,
        }
    return DecisionResult(decision=approval, proposal_state=state, applied=applied)


def proposal_state(session: Session, organization_id: UUID, proposal_id: UUID) -> str:
    value = session.scalar(
        text(
            "SELECT effective_state FROM effective_proposed_change_state "
            "WHERE organization_id = :organization_id AND id = :proposal_id"
        ),
        {"organization_id": organization_id, "proposal_id": proposal_id},
    )
    if value is None:
        raise ProposalNotFound("Proposed change not found")
    return str(value)


def _require_target(
    session: Session,
    *,
    organization_id: UUID,
    safety_signal_id: UUID | None,
    navigation_task_id: UUID | None,
    patient_message_id: UUID | None,
) -> None:
    target: object | None
    if safety_signal_id is not None:
        target = session.scalar(
            select(SafetySignal.id).where(
                SafetySignal.organization_id == organization_id,
                SafetySignal.id == safety_signal_id,
            )
        )
    elif navigation_task_id is not None:
        target = session.scalar(
            select(NavigationTask.id).where(
                NavigationTask.organization_id == organization_id,
                NavigationTask.id == navigation_task_id,
            )
        )
    else:
        target = session.scalar(
            select(PatientMessage.id).where(
                PatientMessage.organization_id == organization_id,
                PatientMessage.id == patient_message_id,
            )
        )
    if target is None:
        raise ProposalNotFound("Proposed change target not found")


def _self_approval_is_forbidden(session: Session, proposal: ProposedChange) -> bool:
    if not proposal.allow_self_approval_snapshot:
        return True
    if proposal.change_type is not ApprovalChangeType.DISMISS_SIGNAL:
        return False
    if proposal.deterministic_severity_threshold_snapshot is None:
        return True
    signal = session.get(SafetySignal, proposal.safety_signal_id)
    assert signal is not None
    return severity_rank(signal.deterministic_level) >= severity_rank(
        proposal.deterministic_severity_threshold_snapshot
    )


def _database_message(error: DBAPIError) -> str:
    return str(getattr(error, "orig", error)).splitlines()[0]


def _proposal_domain_error(error: DBAPIError) -> ApprovalConflict | None:
    original = getattr(error, "orig", error)
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate == "P0001":
        diagnostic = _database_message(error)
        if diagnostic in _PROPOSAL_CONFLICT_TRIGGER_DIAGNOSTICS:
            return ApprovalConflict(diagnostic)
        return None
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if sqlstate == "23505" and constraint_name == _DUPLICATE_PROPOSAL_REVISION_CONSTRAINT:
        return ApprovalConflict(_DUPLICATE_PROPOSAL_REVISION_DIAGNOSTIC)
    return None


def _decision_domain_error(error: DBAPIError) -> ApprovalForbidden | ApprovalConflict | None:
    original = getattr(error, "orig", error)
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate == "P0001":
        diagnostic = _database_message(error)
        if diagnostic in _DECISION_FORBIDDEN_TRIGGER_DIAGNOSTICS:
            return ApprovalForbidden(diagnostic)
        if diagnostic in _DECISION_CONFLICT_TRIGGER_DIAGNOSTICS:
            return ApprovalConflict(diagnostic)
        return None
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if sqlstate == "23505" and constraint_name == _DUPLICATE_DECISION_CONSTRAINT:
        return ApprovalConflict(_DUPLICATE_DECISION_DIAGNOSTIC)
    return None
