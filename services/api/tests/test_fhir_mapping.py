from datetime import UTC, datetime
from uuid import uuid4

from app.db.models import CheckInSubmission
from app.domain.enums import CheckInStatus
from app.fhir.check_in_mapper import map_check_in_to_fhir_bundle


def test_submission_maps_to_questionnaire_response_and_observation() -> None:
    """This fails if the interoperability boundary omits patient-supplied answer resources."""
    patient_id = uuid4()
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=uuid4(),
        patient_id=patient_id,
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        answers={
            "questionnaire_version": "breast-active-v1",
            "questionnaire_canonical": "https://demo.example/Questionnaire/breast-active|1",
            "items": [
                {
                    "link_id": "nausea_change",
                    "label": "Is your nausea better, the same, or worse?",
                    "value": "worse",
                }
            ],
            "free_text": "Synthetic check-in context.",
            "provenance": {"source": "patient-supplied", "actor_id": str(patient_id)},
        },
    )

    bundle = map_check_in_to_fhir_bundle(submission)
    types = [entry["resource"]["resourceType"] for entry in bundle["entry"]]

    assert bundle["resourceType"] == "Bundle"
    assert types.count("QuestionnaireResponse") == 1
    assert "Observation" in types
    assert all(entry["resource"].get("subject") for entry in bundle["entry"])
    questionnaire_response = next(
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "QuestionnaireResponse"
    )
    assert questionnaire_response["questionnaire"] == "https://demo.example/Questionnaire/breast-active|1"
    assert questionnaire_response["authored"] == "2026-08-17T12:00:00+00:00"
    assert bundle["meta"]["tag"][0]["code"] == "synthetic-demo"
