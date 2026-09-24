from datetime import UTC, datetime
from importlib import import_module
from uuid import uuid4

import pytest

from app.db.models import CheckInDefinition, CheckInSubmission, ReportedNeed, SyntheticPatient


@pytest.fixture
def case():
    org, patient, episode, definition = (uuid4() for _ in range(4))
    return {
        "organization_id": org,
        "patients": [SyntheticPatient(id=patient, organization_id=org, display_name="Patient A")],
        "definitions": [
            CheckInDefinition(
                id=definition,
                organization_id=org,
                slug="weekly-synthetic-check-in",
                version=2,
                questionnaire={
                    "version": "weekly-synthetic-check-in-v2",
                    "questions": [
                        {
                            "link_id": "transportation",
                            "label": "Need synthetic transportation support?",
                            "options": [
                                {"value": "yes", "label": "Yes"},
                                {"value": "no", "label": "No"},
                            ],
                        }
                    ],
                },
            )
        ],
        "submissions": [
            CheckInSubmission(
                id=uuid4(),
                organization_id=org,
                patient_id=patient,
                care_episode_id=episode,
                check_in_definition_id=definition,
                submitted_at=datetime(2026, 9, 24, tzinfo=UTC),
                answers={
                    "questionnaire_version": "weekly-synthetic-check-in-v2",
                    "items": [
                        {"link_id": "transportation", "value": "yes"},
                    ],
                },
            )
        ],
        "needs": [],
    }


def project(case):
    # The first red run fails on the absent projection, before production code exists.
    return import_module("app.domain.need_candidates").build_need_candidates(**case)


def copy_submission(case, *, predecessor=None, value="yes"):
    source = case["submissions"][0]
    row = CheckInSubmission(
        id=uuid4(),
        organization_id=source.organization_id,
        patient_id=source.patient_id,
        care_episode_id=source.care_episode_id,
        check_in_definition_id=source.check_in_definition_id,
        submitted_at=source.submitted_at,
        supersedes_submission_id=predecessor,
        answers={
            "questionnaire_version": "weekly-synthetic-check-in-v2",
            "items": [
                {"link_id": "transportation", "value": value},
            ],
        },
    )
    case["submissions"].append(row)
    return row


def test_explicit_yes_has_exact_source_evidence(case):
    result = project(case)
    assert not result.unavailable
    (candidate,) = result.candidates
    assert candidate.patient_display_name == "Patient A"
    assert candidate.source_submission_id == case["submissions"][0].id
    assert candidate.chain_root_id == candidate.source_submission_id
    assert candidate.evidence.model_dump() == {
        "field": "transportation",
        "question": "Need synthetic transportation support?",
        "value": "yes",
        "text": "yes",
    }
    assert candidate.is_correction is False


@pytest.mark.parametrize("value", ["no", "Yes", "true", True, ["yes"], None, "need a ride"])
def test_only_supported_exact_yes_produces_candidate(case, value):
    case["submissions"][0].answers["items"][0]["value"] = value
    assert project(case).candidates == []


def test_missing_answer_and_free_text_do_not_produce_candidate(case):
    case["submissions"][0].answers["items"] = []
    case["submissions"][0].answers["free_text"] = "yes, need transportation"
    assert project(case).candidates == []


@pytest.mark.parametrize("change", ["version", "definition", "question", "duplicate_answer"])
def test_unavailable_questionnaire_is_explicit(case, change):
    if change == "version":
        case["submissions"][0].answers["questionnaire_version"] = "unknown-v9"
    elif change == "definition":
        case["definitions"][0].version = 99
    elif change == "question":
        case["definitions"][0].questionnaire["questions"] = []
    else:
        case["submissions"][0].answers["items"] *= 2
    result = project(case)
    assert not result.candidates
    assert len(result.unavailable) == 1


def test_corrections_replace_chain_and_independent_reports_remain_distinct(case):
    root = case["submissions"][0]
    correction = copy_submission(case, predecessor=root.id)
    independent = copy_submission(case)
    result = project(case)
    assert {(c.chain_root_id, c.source_submission_id) for c in result.candidates} == {
        (root.id, correction.id),
        (independent.id, independent.id),
    }
    assert next(c for c in result.candidates if c.chain_root_id == root.id).is_correction
    correction.answers["items"][0]["value"] = "no"
    assert [c.source_submission_id for c in project(case).candidates] == [independent.id]


@pytest.mark.parametrize("damage", ["branch", "cycle", "orphan", "cross_patient", "cross_episode"])
def test_ambiguous_lineage_is_unavailable_without_positive_fallback(case, damage):
    root = case["submissions"][0]
    correction = copy_submission(case, predecessor=root.id)
    if damage == "branch":
        copy_submission(case, predecessor=root.id)
    elif damage == "cycle":
        root.supersedes_submission_id = correction.id
    elif damage == "orphan":
        root.supersedes_submission_id = uuid4()
    elif damage == "cross_patient":
        correction.patient_id = uuid4()
    else:
        correction.care_episode_id = uuid4()
    result = project(case)
    assert not result.candidates
    assert result.unavailable
    assert all(u.reason == "ambiguous_lineage" for u in result.unavailable)


def test_all_chain_needs_and_other_episode_transportation_are_distinct(case):
    root = case["submissions"][0]
    correction = copy_submission(case, predecessor=root.id)
    rows = []
    for source, kind, state in [
        (root.id, "transportation", "closed"),
        (correction.id, "transportation", "open"),
        (root.id, "symptom_change", "open"),
        (None, "transportation", "in_progress"),
    ]:
        rows.append(
            (
                ReportedNeed(
                    id=uuid4(),
                    organization_id=root.organization_id,
                    patient_id=root.patient_id,
                    care_episode_id=root.care_episode_id,
                    source_submission_id=source,
                    kind=kind,
                    reopened_from_need_id=None,
                ),
                state,
            )
        )
    rows[-1][0].reopened_from_need_id = rows[0][0].id
    case["needs"] = rows
    (candidate,) = project(case).candidates
    assert {n.id for n in candidate.linked_needs} == {row[0].id for row in rows[:3]}
    (other,) = candidate.other_transportation_needs
    assert other.id == rows[3][0].id
    assert other.effective_state == "in_progress"
    assert other.reopened_from_need_id == rows[0][0].id
    assert {n.effective_state for n in candidate.linked_needs} == {"closed", "open"}


def test_foreign_organization_records_never_disclose(case):
    foreign = uuid4()
    case["submissions"][0].organization_id = foreign
    result = project(case)
    assert result.candidates == []
    assert result.unavailable == []
