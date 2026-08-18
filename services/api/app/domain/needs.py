"""Deterministic extraction of navigation needs from immutable check-in source data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.db.models import CheckInSubmission

NeedKind = Literal[
    "symptom_change",
    "medication_question",
    "transportation",
    "financial_support",
    "other",
]


@dataclass(frozen=True)
class Evidence:
    source_submission_id: UUID
    field: str
    text: str
    value: Any


@dataclass(frozen=True)
class ReportedNeed:
    """Idempotency-friendly input for a persisted reported need command."""

    kind: NeedKind
    source_submission_id: UUID
    evidence: tuple[Evidence, ...]
    idempotency_key: str


class NeedFactory:
    """Maps explicit submitted fields to workflow needs without inference or model calls."""

    @classmethod
    def from_submission(cls, submission: CheckInSubmission) -> list[ReportedNeed]:
        items = submission.answers.get("items", [])
        if not isinstance(items, list):
            return []

        exact_evidence = tuple(
            cls._evidence_from_item(submission.id, item)
            for item in items
            if isinstance(item, dict) and isinstance(item.get("link_id"), str)
        )
        free_text = submission.answers.get("free_text")
        if isinstance(free_text, str) and free_text:
            exact_evidence += (
                Evidence(
                    source_submission_id=submission.id,
                    field="free_text",
                    text=free_text,
                    value=free_text,
                ),
            )

        kinds: list[NeedKind] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            link_id = item.get("link_id")
            value = item.get("value")
            if not isinstance(link_id, str):
                continue
            if link_id.endswith("_change") and cls._is_worsening(value):
                kinds.append("symptom_change")
            elif link_id == "medication_question" and cls._is_affirmative(value):
                kinds.append("medication_question")
            elif link_id == "transportation" and cls._is_affirmative(value):
                kinds.append("transportation")
            elif link_id == "financial_support" and cls._is_affirmative(value):
                kinds.append("financial_support")

        return [
            ReportedNeed(
                kind=kind,
                source_submission_id=submission.id,
                evidence=exact_evidence,
                idempotency_key=f"{submission.id}:{kind}",
            )
            for kind in dict.fromkeys(kinds)
        ]

    @staticmethod
    def _evidence_from_item(submission_id: UUID, item: dict[str, Any]) -> Evidence:
        value = item.get("value")
        return Evidence(
            source_submission_id=submission_id,
            field=str(item["link_id"]),
            text=str(value),
            value=value,
        )

    @staticmethod
    def _is_worsening(value: object) -> bool:
        return isinstance(value, str) and value.casefold() == "worse"

    @staticmethod
    def _is_affirmative(value: object) -> bool:
        return value is True or (isinstance(value, str) and value.casefold() in {"yes", "true"})
