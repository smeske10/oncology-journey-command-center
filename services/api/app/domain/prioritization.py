"""Transparent, deterministic operational ordering for navigator work."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

PriorityLevel = Literal["high", "medium", "routine"]

_KIND_NAMES = (
    "symptom_change",
    "medication_question",
    "transportation",
    "financial_support",
    "other",
)
_DEFAULT_POLICY_VALUES: dict[str, int | dict[str, int]] = {
    "kind_weights": {kind: 0 for kind in _KIND_NAMES},
    "worsening_report": 55,
    "medication_uncertainty": 45,
    "due_soon": 20,
    "unresolved_over_24_hours": 5,
    "unresolved_over_48_hours": 10,
    "high_threshold": 100,
    "medium_threshold": 50,
}


@dataclass(frozen=True)
class PriorityResult:
    level: PriorityLevel
    score: int
    reasons: list[str]


@dataclass(frozen=True)
class OperationalPriorityWeights:
    """Validated deployment policy for operational queue order, not clinical risk."""

    kind_weights: Mapping[str, int]
    worsening_report: int = 55
    medication_uncertainty: int = 45
    due_soon: int = 20
    unresolved_over_24_hours: int = 5
    unresolved_over_48_hours: int = 10
    high_threshold: int = 100
    medium_threshold: int = 50

    def __post_init__(self) -> None:
        normalized = dict(self.kind_weights)
        if set(normalized) != set(_KIND_NAMES) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in normalized.values()
        ):
            raise ValueError(
                "kind_weights must contain every known kind with non-negative integers"
            )
        numeric_values = (
            self.worsening_report,
            self.medication_uncertainty,
            self.due_soon,
            self.unresolved_over_24_hours,
            self.unresolved_over_48_hours,
            self.medium_threshold,
            self.high_threshold,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in numeric_values
        ):
            raise ValueError("operational priority values must be non-negative integers")
        if self.medium_threshold > self.high_threshold:
            raise ValueError("medium threshold cannot exceed high threshold")
        object.__setattr__(self, "kind_weights", MappingProxyType(normalized))


def default_operational_priority_policy() -> OperationalPriorityWeights:
    """Documented safe fallback when a deployment value is absent or invalid."""
    return OperationalPriorityWeights(**_default_policy_kwargs())


def policy_from_json(value: str | None) -> OperationalPriorityWeights:
    """Load a deployment policy without accepting malformed or partially unsafe JSON."""
    if not value:
        return default_operational_priority_policy()
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError
        unknown = set(parsed) - set(_DEFAULT_POLICY_VALUES)
        if unknown:
            raise ValueError
        kwargs = _default_policy_kwargs()
        for key, configured in parsed.items():
            if key == "kind_weights":
                if not isinstance(configured, dict):
                    raise ValueError
                kwargs[key] = configured
            else:
                kwargs[key] = configured
        return OperationalPriorityWeights(**kwargs)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_operational_priority_policy()


def rank_need(
    *,
    kind: str,
    worsening: bool,
    medication_question: bool,
    age_hours: float,
    due_in_hours: float | None = None,
    weights: OperationalPriorityWeights | None = None,
) -> PriorityResult:
    """Return explicit operational order derived only from supplied need evidence."""
    policy = weights or default_operational_priority_policy()
    kind_weight = policy.kind_weights.get(kind, policy.kind_weights["other"])
    score = kind_weight
    reasons: list[str] = []
    if kind_weight:
        reasons.append(f"configured_kind_{kind if kind in policy.kind_weights else 'other'}")
    if worsening and policy.worsening_report:
        score += policy.worsening_report
        reasons.append("worsening_report")
    if medication_question and policy.medication_uncertainty:
        score += policy.medication_uncertainty
        reasons.append("medication_uncertainty")
    if due_in_hours is not None and due_in_hours <= 24 and policy.due_soon:
        score += policy.due_soon
        reasons.append("due_soon")
    if age_hours >= 48 and policy.unresolved_over_48_hours:
        score += policy.unresolved_over_48_hours
        reasons.append("unresolved_over_48_hours")
    elif age_hours >= 24 and policy.unresolved_over_24_hours:
        score += policy.unresolved_over_24_hours
        reasons.append("unresolved_over_24_hours")

    if score >= policy.high_threshold:
        level: PriorityLevel = "high"
    elif score >= policy.medium_threshold:
        level = "medium"
    else:
        level = "routine"
    return PriorityResult(level=level, score=score, reasons=reasons)


def _default_policy_kwargs() -> dict[str, Any]:
    values = dict(_DEFAULT_POLICY_VALUES)
    kind_weights = values["kind_weights"]
    assert isinstance(kind_weights, dict)
    values["kind_weights"] = dict(kind_weights)
    return values
