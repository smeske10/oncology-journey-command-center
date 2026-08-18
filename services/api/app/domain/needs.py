"""Deterministic need extraction with deeply immutable patient-supplied evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias
from uuid import UUID

from app.db.models import CheckInSubmission

NeedKind = Literal[
    "symptom_change",
    "medication_question",
    "transportation",
    "financial_support",
    "other",
]


class FrozenList(tuple):
    """Immutable internal list marker; distinct from objects even when empty."""


class FrozenObject(tuple):
    """Immutable internal object marker; never derived from user string/tag values."""


FrozenJson: TypeAlias = None | bool | int | float | str | FrozenList | FrozenObject


@dataclass(frozen=True)
class Evidence:
    source_submission_id: UUID
    field: str
    text: str
    value: FrozenJson


@dataclass(frozen=True)
class ReportedNeed:
    """Deterministic input for a future idempotent durable reported-need command."""

    kind: NeedKind
    source_submission_id: UUID
    evidence: tuple[Evidence, ...]
    evidence_hash: str
    idempotency_key: str


class NeedFactory:
    """Maps explicit source fields only; it neither infers diagnoses nor calls a model."""

    @classmethod
    def from_submission(cls, submission: CheckInSubmission) -> list[ReportedNeed]:
        answers = submission.answers if isinstance(submission.answers, dict) else {}
        items = answers.get("items", [])
        if not isinstance(items, list):
            return []

        candidates: dict[NeedKind, list[Evidence]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = cls._kind_for_item(item)
            if kind is None:
                continue
            candidates.setdefault(kind, []).append(cls._evidence_from_item(submission.id, item))

        needs: list[ReportedNeed] = []
        for kind, evidence in candidates.items():
            frozen_evidence = tuple(evidence)
            evidence_hash = _evidence_hash(frozen_evidence)
            needs.append(
                ReportedNeed(
                    kind=kind,
                    source_submission_id=submission.id,
                    evidence=frozen_evidence,
                    evidence_hash=evidence_hash,
                    idempotency_key=f"{submission.id}:{kind}:{evidence_hash}",
                )
            )
        return needs

    @classmethod
    def _kind_for_item(cls, item: dict[str, Any]) -> NeedKind | None:
        link_id = item.get("link_id")
        value = item.get("value")
        if not isinstance(link_id, str):
            return None
        if link_id.endswith("_change") and cls._is_worsening(value):
            return "symptom_change"
        if link_id == "medication_question" and cls._is_affirmative(value):
            return "medication_question"
        if link_id == "transportation" and cls._is_affirmative(value):
            return "transportation"
        if link_id == "financial_support" and cls._is_affirmative(value):
            return "financial_support"
        return None

    @staticmethod
    def _evidence_from_item(submission_id: UUID, item: dict[str, Any]) -> Evidence:
        value = _freeze_json(item.get("value"))
        return Evidence(
            source_submission_id=submission_id,
            field=str(item["link_id"]),
            text=_evidence_text(value),
            value=value,
        )

    @staticmethod
    def _is_worsening(value: object) -> bool:
        return isinstance(value, str) and value.casefold() == "worse"

    @classmethod
    def _is_affirmative(cls, value: object) -> bool:
        if value is True or (isinstance(value, str) and value.casefold() in {"yes", "true"}):
            return True
        return isinstance(value, list) and any(cls._is_affirmative(item) for item in value)


def _freeze_json(value: object) -> FrozenJson:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return FrozenList(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Evidence JSON object keys must be strings")
        return FrozenObject((key, _freeze_json(value[key])) for key in sorted(value))
    raise ValueError("Evidence values must be JSON-compatible")


def _evidence_hash(evidence: tuple[Evidence, ...]) -> str:
    payload = [
        {"field": item.field, "text": item.text, "value": _thaw_json(item.value)}
        for item in evidence
    ]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evidence_text(value: FrozenJson) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(_thaw_json(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _thaw_json(value: FrozenJson) -> object:
    if isinstance(value, FrozenObject):
        return {key: _thaw_json(item_value) for key, item_value in value}
    if isinstance(value, FrozenList):
        return [_thaw_json(item) for item in value]
    return value
