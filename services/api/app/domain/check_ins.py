from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import DateTime, String, Uuid, column, table

from app.auth.models import CurrentActor
from app.db.models import CheckInDefinition, CheckInSubmission
from app.domain.enums import CheckInStatus, SubmissionSource
from app.domain.public_demo import PUBLIC_DEMO_PHI_WARNING, contains_real_phi
from app.domain.types import uuid7

active_check_in_submission = table(
    "active_check_in_submission",
    column("id", Uuid),
    column("organization_id", Uuid),
    column("patient_id", Uuid),
    column("care_episode_id", Uuid),
    column("check_in_definition_id", Uuid),
    column("status", String),
    column("submitted_at", DateTime(timezone=True)),
)


class AnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str = Field(min_length=1, max_length=80)
    value: str | int | bool | list[str]


class CheckInSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionnaire_version: str = Field(min_length=1, max_length=100)
    answers: list[AnswerInput] = Field(min_length=1, max_length=40)
    free_text: str | None = Field(default=None, max_length=2000)
    supersedes_submission_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_real_phi(cls, values: Any) -> Any:
        if contains_real_phi(values):
            raise ValueError(PUBLIC_DEMO_PHI_WARNING)
        return values


class CheckInDefinitionMismatchError(ValueError):
    """The submitted source data does not match the tenant-scoped questionnaire definition."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def create_immutable_submission(
    *,
    actor: CurrentActor,
    definition: CheckInDefinition,
    care_episode_id: UUID,
    payload: CheckInSubmissionCreate,
    predecessor: CheckInSubmission | None = None,
) -> CheckInSubmission:
    """Build the source-of-truth record before any policy or orchestration work starts."""
    _validate_submission_against_definition(definition, payload)
    if actor.patient_id is None:
        raise CheckInDefinitionMismatchError(
            "Patient identity link is required to submit a check-in",
            code="configuration_invalid",
        )
    _validate_correction_predecessor(
        actor=actor,
        definition=definition,
        care_episode_id=care_episode_id,
        payload=payload,
        predecessor=predecessor,
    )
    labels = _question_labels(definition.questionnaire)
    answers = [
        {
            "link_id": answer.link_id,
            "label": labels.get(answer.link_id, answer.link_id),
            "value": answer.value,
        }
        for answer in payload.answers
    ]
    source_data: dict[str, Any] = {
        "questionnaire_version": questionnaire_version_for(definition),
        "questionnaire_canonical": _questionnaire_canonical(definition),
        "items": answers,
        "free_text": payload.free_text,
        "provenance": {"source": "patient-supplied", "actor_id": str(actor.user_id)},
    }
    return CheckInSubmission(
        id=uuid7(),
        organization_id=actor.organization_id,
        patient_id=actor.patient_id,
        care_episode_id=care_episode_id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers=source_data,
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=actor.user_id,
        supersedes_submission_id=payload.supersedes_submission_id,
        submitted_at=datetime.now(UTC),
    )


def _validate_correction_predecessor(
    *,
    actor: CurrentActor,
    definition: CheckInDefinition,
    care_episode_id: UUID,
    payload: CheckInSubmissionCreate,
    predecessor: CheckInSubmission | None,
) -> None:
    if payload.supersedes_submission_id is None:
        if predecessor is not None:
            raise CheckInDefinitionMismatchError(
                "Correction predecessor was not requested", code="correction_stale"
            )
        return
    if predecessor is None or predecessor.id != payload.supersedes_submission_id:
        raise CheckInDefinitionMismatchError(
            "A newer check-in is available. Reload before correcting it.",
            code="correction_stale",
        )
    if (
        predecessor.organization_id != actor.organization_id
        or predecessor.patient_id != actor.patient_id
        or predecessor.care_episode_id != care_episode_id
        or predecessor.check_in_definition_id != definition.id
    ):
        raise CheckInDefinitionMismatchError(
            "A newer check-in is available. Reload before correcting it.",
            code="correction_stale",
        )


def _question_labels(questionnaire: Mapping[str, Any]) -> dict[str, str]:
    questions = questionnaire.get("questions", [])
    if not isinstance(questions, list):
        return {}
    return {
        str(question["link_id"]): str(question["label"])
        for question in questions
        if isinstance(question, Mapping) and "link_id" in question and "label" in question
    }


def questionnaire_version_for(definition: CheckInDefinition) -> str:
    version = definition.questionnaire.get("version")
    if isinstance(version, str) and version:
        return version
    return f"{definition.slug}-v{definition.version}"


def _validate_submission_against_definition(
    definition: CheckInDefinition, payload: CheckInSubmissionCreate
) -> None:
    expected_version = questionnaire_version_for(definition)
    if payload.questionnaire_version != expected_version:
        raise CheckInDefinitionMismatchError(
            "This check-in has changed. Reload the current check-in before submitting.",
            code="questionnaire_stale",
        )

    questions = _questions_by_link_id(definition.questionnaire)
    submitted_link_ids = [answer.link_id for answer in payload.answers]
    unknown_link_ids = set(submitted_link_ids) - set(questions)
    if unknown_link_ids:
        raise CheckInDefinitionMismatchError(
            "Answers must use known questionnaire link IDs", code="answers_invalid"
        )
    if len(submitted_link_ids) != len(set(submitted_link_ids)):
        raise CheckInDefinitionMismatchError(
            "Answers must not repeat questionnaire link IDs", code="answers_invalid"
        )

    required_link_ids = {
        link_id for link_id, question in questions.items() if question.get("required", True) is True
    }
    missing_link_ids = required_link_ids - set(submitted_link_ids)
    if missing_link_ids:
        raise CheckInDefinitionMismatchError(
            "Please answer every required questionnaire item", code="answers_invalid"
        )


def _questions_by_link_id(questionnaire: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    questions = questionnaire.get("questions", [])
    if not isinstance(questions, list):
        return {}
    return {
        str(question["link_id"]): question
        for question in questions
        if isinstance(question, Mapping) and "link_id" in question and "label" in question
    }


def _questionnaire_canonical(definition: CheckInDefinition) -> str:
    canonical = definition.questionnaire.get("canonical")
    if isinstance(canonical, str) and canonical:
        return canonical
    return f"urn:ojcc:demo:questionnaire:{definition.id}|{definition.version}"
