"""Typed contracts for immutable, database-derived need creation commands."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.public_demo import validate_public_demo_text


class NeedCreationProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    chain_root_id: UUID
    source_submission_id: UUID
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_rationale(self):
        validate_public_demo_text(self.rationale)
        if not self.rationale.strip():
            raise ValueError("A rationale is required")
        return self


class NeedCreationDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    decision: Literal["approved", "declined"]
    reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_reason(self):
        validate_public_demo_text(self.reason)
        if self.decision == "declined" and not (self.reason and self.reason.strip()):
            raise ValueError("A decline reason is required")
        return self


class NeedCreationDecisionRead(BaseModel):
    id: UUID
    proposal_id: UUID
    authorized_by_user_id: UUID
    qualifying_role_assignment_id: UUID
    qualifying_role_snapshot: str
    decision: str
    reason: str | None
    outcome: str
    reported_need_id: UUID | None
    authorized_at: datetime


class NeedCreationEvidenceRead(BaseModel):
    schema_version: int
    canonicalization: str
    organization_id: UUID
    patient_id: UUID
    care_episode_id: UUID
    chain_root_id: UUID
    source_submission_id: UUID
    check_in_definition_id: UUID
    definition_version: int
    questionnaire_version: str
    question: dict[str, Any]
    answer: dict[str, Any]
    display_text: str
    kind: str
    initial_state: str


class NeedCreationProposalRead(BaseModel):
    id: UUID
    organization_id: UUID
    patient_id: UUID
    patient_display_name: str
    care_episode_id: UUID
    chain_root_id: UUID
    source_submission_id: UUID
    proposed_by_user_id: UUID
    proposer_role_assignment_id: UUID
    proposed_at: datetime
    rationale: str
    evidence: NeedCreationEvidenceRead
    canonical_evidence: str
    evidence_sha256: str
    policy_snapshot: dict[str, Any]
    state: Literal["pending", "created", "declined", "stale", "already_created"]
    source_changed: bool
    current_source_submission_id: UUID | None
    current_answer_text: str
    decisions: list[NeedCreationDecisionRead]


class NeedCreationHistoryRead(BaseModel):
    proposals: list[NeedCreationProposalRead]
