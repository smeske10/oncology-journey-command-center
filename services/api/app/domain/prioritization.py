"""Deterministic, operational work ordering for the navigator queue.

This module deliberately describes queue order, not clinical risk.  The values are
configuration-friendly constants so a deployment can review and change them without
introducing model behaviour or opaque scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PriorityLevel = Literal["high", "medium", "routine"]


@dataclass(frozen=True)
class PriorityResult:
    level: PriorityLevel
    score: int
    reasons: list[str]


@dataclass(frozen=True)
class OperationalPriorityWeights:
    """Reviewed deterministic weights used only to order navigator work."""

    kind_weights: dict[str, int]
    worsening_report: int = 50
    medication_uncertainty: int = 45
    due_soon: int = 20
    unresolved_over_24_hours: int = 5
    unresolved_over_48_hours: int = 10
    high_threshold: int = 100
    medium_threshold: int = 50


DEFAULT_WEIGHTS = OperationalPriorityWeights(
    kind_weights={
        "symptom_change": 60,
        "medication_question": 55,
        "transportation": 20,
        "financial_support": 20,
        "other": 10,
    }
)


def rank_need(
    *,
    kind: str,
    worsening: bool,
    medication_question: bool,
    age_hours: float,
    due_in_hours: float | None = None,
    weights: OperationalPriorityWeights = DEFAULT_WEIGHTS,
) -> PriorityResult:
    """Return explainable operational ordering fields from explicit rule inputs."""
    score = weights.kind_weights.get(kind, weights.kind_weights["other"])
    reasons: list[str] = []
    if worsening:
        score += weights.worsening_report
        reasons.append("worsening_report")
    if medication_question:
        score += weights.medication_uncertainty
        reasons.append("medication_uncertainty")
    if due_in_hours is not None and due_in_hours <= 24:
        score += weights.due_soon
        reasons.append("due_soon")
    if age_hours >= 48:
        score += weights.unresolved_over_48_hours
        reasons.append("unresolved_over_48_hours")
    elif age_hours >= 24:
        score += weights.unresolved_over_24_hours
        reasons.append("unresolved_over_24_hours")

    if score >= weights.high_threshold:
        level: PriorityLevel = "high"
    elif score >= weights.medium_threshold:
        level = "medium"
    else:
        level = "routine"
    return PriorityResult(level=level, score=score, reasons=reasons)
