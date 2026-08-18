import json

from app.config import Settings
from app.domain.prioritization import OperationalPriorityWeights, rank_need


def test_worsening_symptom_and_medication_question_rank_above_barrier() -> None:
    """Operational order exposes exactly which configured rules matched."""
    clinical = rank_need(
        kind="symptom_change",
        worsening=True,
        medication_question=True,
        age_hours=1,
    )
    barrier = rank_need(
        kind="transportation",
        worsening=False,
        medication_question=False,
        age_hours=1,
    )

    assert clinical.level == "high"
    assert clinical.score > barrier.score
    assert clinical.reasons == ["worsening_report", "medication_uncertainty"]


def test_due_time_then_unresolved_age_are_explicit_operational_tiebreakers() -> None:
    result = rank_need(
        kind="transportation",
        worsening=False,
        medication_question=False,
        age_hours=49,
        due_in_hours=2,
    )

    assert result.reasons == ["due_soon", "unresolved_over_48_hours"]
    assert result.score > 0


def test_configured_base_weight_has_an_explicit_reason_and_changes_ordering() -> None:
    policy = OperationalPriorityWeights(
        kind_weights={
            "symptom_change": 0,
            "medication_question": 0,
            "transportation": 40,
            "financial_support": 0,
            "other": 0,
        },
        worsening_report=10,
        medication_uncertainty=10,
        due_soon=0,
        unresolved_over_24_hours=0,
        unresolved_over_48_hours=0,
        high_threshold=100,
        medium_threshold=25,
    )

    barrier = rank_need(
        kind="transportation",
        worsening=False,
        medication_question=False,
        age_hours=0,
        weights=policy,
    )

    assert barrier.score == 40
    assert barrier.level == "medium"
    assert barrier.reasons == ["configured_kind_transportation"]


def test_settings_loads_validated_priority_configuration_and_falls_back_safely() -> None:
    valid = Settings(
        navigator_priority_weights_json=json.dumps(
            {
                "kind_weights": {
                    "symptom_change": 0,
                    "medication_question": 0,
                    "transportation": 35,
                    "financial_support": 0,
                    "other": 0,
                },
                "high_threshold": 80,
                "medium_threshold": 20,
            }
        )
    )
    invalid = Settings(navigator_priority_weights_json="{not-json")

    assert valid.navigator_priority_policy.kind_weights["transportation"] == 35
    assert valid.navigator_priority_policy.medium_threshold == 20
    assert invalid.navigator_priority_policy == Settings().navigator_priority_policy
