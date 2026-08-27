from uuid import uuid4

from app.db.models import CheckInSubmission
from app.domain.needs import NeedFactory


def test_mixed_submission_gives_each_need_only_its_supporting_evidence() -> None:
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=uuid4(),
        patient_id=uuid4(),
        answers={
            "items": [
                {"link_id": "nausea_change", "value": "worse"},
                {"link_id": "medication_question", "value": "yes"},
                {"link_id": "transportation", "value": "yes"},
            ],
            "free_text": "Nausea now interferes with meals.",
        },
    )

    needs = {need.kind: need for need in NeedFactory.from_submission(submission)}

    assert [evidence.field for evidence in needs["symptom_change"].evidence] == ["nausea_change"]
    assert [evidence.field for evidence in needs["medication_question"].evidence] == [
        "medication_question"
    ]
    assert [evidence.field for evidence in needs["transportation"].evidence] == [
        "transportation"
    ]


def test_need_evidence_is_deeply_immutable_and_hashed_canonically() -> None:
    mutable_value = ["yes", {"details": ["bus", "weekday"]}]
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=uuid4(),
        patient_id=uuid4(),
        answers={"items": [{"link_id": "transportation", "value": mutable_value}]},
    )

    first = NeedFactory.from_submission(submission)[0]
    mutable_value[1]["details"].append("changed")
    second = NeedFactory.from_submission(
        CheckInSubmission(
            id=submission.id,
            organization_id=submission.organization_id,
            patient_id=submission.patient_id,
            answers={
                "items": [
                    {
                        "link_id": "transportation",
                        "value": ["yes", {"details": ["bus", "weekday"]}],
                    }
                ]
            },
        )
    )[0]

    assert first.evidence[0].value == ("yes", (("details", ("bus", "weekday")),))
    assert first.evidence_hash == second.evidence_hash
    assert first.idempotency_key == f"{submission.id}:transportation:{first.evidence_hash}"


def test_canonical_evidence_distinguishes_empty_and_nested_json_container_types() -> None:
    organization_id = uuid4()
    patient_id = uuid4()
    submission_id = uuid4()

    def extracted(value: object):
        return NeedFactory.from_submission(
            CheckInSubmission(
                id=submission_id,
                organization_id=organization_id,
                patient_id=patient_id,
                answers={"items": [{"link_id": "transportation", "value": value}]},
            )
        )[0]

    empty_list = extracted(["yes", []])
    empty_object = extracted(["yes", {}])
    nested = extracted(["yes", {"b": [], "a": {"nested": [{}]}}])
    reordered_nested = extracted(["yes", {"a": {"nested": [{}]}, "b": []}])

    assert empty_list.evidence_hash != empty_object.evidence_hash
    assert empty_list.idempotency_key != empty_object.idempotency_key
    assert nested.evidence_hash == reordered_nested.evidence_hash
    assert nested.evidence[0].value == (
        "yes",
        (("a", (("nested", ((),)),)), ("b", ())),
    )
