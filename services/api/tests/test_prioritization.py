from app.domain.prioritization import rank_need


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
