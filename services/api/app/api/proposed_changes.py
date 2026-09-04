"""Navigator proposal and approval-decision command boundaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.auth.models import CurrentActor, Role
from app.db.models import ProposedChange
from app.db.session import get_session
from app.domain.approvals import (
    ApprovalConflict,
    ApprovalForbidden,
    ApprovalPolicyNotFound,
    ProposalNotFound,
    create_proposal,
    record_decision,
    validate_target_shape,
)
from app.domain.enums import ApprovalChangeType, ApprovalDecisionValue, SafetySeverity, UserRole

router = APIRouter(prefix="/v1/navigator/proposed-changes", tags=["navigator"])


class ProposedChangeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_type: ApprovalChangeType
    safety_signal_id: UUID | None = None
    navigation_task_id: UUID | None = None
    patient_message_id: UUID | None = None
    proposed_value: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=4000)
    value_schema_id: str = Field(min_length=1, max_length=255)
    value_schema_version: int = Field(ge=1)
    supersedes_proposed_change_id: UUID | None = None

    @model_validator(mode="after")
    def valid_target(self) -> ProposedChangeCreate:
        validate_target_shape(
            change_type=self.change_type,
            safety_signal_id=self.safety_signal_id,
            navigation_task_id=self.navigation_task_id,
            patient_message_id=self.patient_message_id,
        )
        return self


class ProposedChangeRead(BaseModel):
    id: UUID
    organization_id: UUID
    proposed_by_user_id: UUID | None
    proposed_at: datetime
    change_type: ApprovalChangeType
    proposed_value: dict[str, Any]
    rationale: str
    value_schema_id: str
    value_schema_version: int
    supersedes_proposed_change_id: UUID | None
    safety_signal_id: UUID | None
    navigation_task_id: UUID | None
    patient_message_id: UUID | None
    approval_policy_id: UUID
    approval_policy_version: int
    deterministic_severity_threshold_snapshot: SafetySeverity | None
    allow_self_approval_snapshot: bool
    required_approval_count_snapshot: int
    required_approver_role_snapshot: UserRole
    state: str


class ApprovalDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: ApprovalDecisionValue
    qualifying_role_assignment_id: UUID
    reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def decline_has_reason(self) -> ApprovalDecisionCreate:
        if self.decision is ApprovalDecisionValue.DECLINED and not (
            self.reason and self.reason.strip()
        ):
            raise ValueError("Decline reason is required")
        return self


class ApprovalDecisionRead(BaseModel):
    id: UUID
    proposed_change_id: UUID
    authorized_by_user_id: UUID
    qualifying_role_assignment_id: UUID
    qualifying_role_snapshot: UserRole
    decision: ApprovalDecisionValue
    authorized_at: datetime
    reason: str | None
    proposal_state: str
    applied: bool


@router.post("", response_model=ProposedChangeRead, status_code=status.HTTP_201_CREATED)
def post_proposed_change(
    command: ProposedChangeCreate,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> ProposedChangeRead:
    try:
        result = create_proposal(
            session,
            organization_id=actor.organization_id,
            proposed_by_user_id=actor.user_id,
            **command.model_dump(),
        )
        session.commit()
    except (ProposalNotFound, ApprovalPolicyNotFound) as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApprovalConflict as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return _proposal_read(result.proposal, result.state)


@router.post(
    "/{proposed_change_id}/decisions",
    response_model=ApprovalDecisionRead,
    status_code=status.HTTP_201_CREATED,
)
def post_approval_decision(
    proposed_change_id: UUID,
    command: ApprovalDecisionCreate,
    actor: CurrentActor = Depends(require_role(Role.NAVIGATOR)),
    session: Session = Depends(get_session),
) -> ApprovalDecisionRead:
    try:
        result = record_decision(
            session,
            organization_id=actor.organization_id,
            proposed_change_id=proposed_change_id,
            authorized_by_user_id=actor.user_id,
            **command.model_dump(),
        )
        session.commit()
    except ProposalNotFound as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ApprovalForbidden as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ApprovalConflict as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    decision = result.decision
    return ApprovalDecisionRead(
        id=decision.id,
        proposed_change_id=decision.proposed_change_id,
        authorized_by_user_id=decision.authorized_by_user_id,
        qualifying_role_assignment_id=decision.qualifying_role_assignment_id,
        qualifying_role_snapshot=decision.qualifying_role_snapshot,
        decision=decision.decision,
        authorized_at=decision.authorized_at,
        reason=decision.reason,
        proposal_state=result.proposal_state,
        applied=result.applied,
    )

def _proposal_read(proposal: ProposedChange, state: str) -> ProposedChangeRead:
    return ProposedChangeRead(
        id=proposal.id,
        organization_id=proposal.organization_id,
        proposed_by_user_id=proposal.proposed_by_user_id,
        proposed_at=proposal.proposed_at,
        change_type=proposal.change_type,
        proposed_value=proposal.proposed_value,
        rationale=proposal.rationale,
        value_schema_id=proposal.value_schema_id,
        value_schema_version=proposal.value_schema_version,
        supersedes_proposed_change_id=proposal.supersedes_proposed_change_id,
        safety_signal_id=proposal.safety_signal_id,
        navigation_task_id=proposal.navigation_task_id,
        patient_message_id=proposal.patient_message_id,
        approval_policy_id=proposal.approval_policy_id,
        approval_policy_version=proposal.approval_policy_version,
        deterministic_severity_threshold_snapshot=(
            proposal.deterministic_severity_threshold_snapshot
        ),
        allow_self_approval_snapshot=proposal.allow_self_approval_snapshot,
        required_approval_count_snapshot=proposal.required_approval_count_snapshot,
        required_approver_role_snapshot=proposal.required_approver_role_snapshot,
        state=state,
    )
