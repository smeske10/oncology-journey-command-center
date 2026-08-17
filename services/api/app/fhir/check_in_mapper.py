from __future__ import annotations

from typing import Any

from app.db.models import CheckInSubmission

_DEMO_TAG = {
    "system": "https://oncology-journey-command-center.example/tags",
    "code": "synthetic-demo",
    "display": "Synthetic demo data only",
}


def map_check_in_to_fhir_bundle(submission: CheckInSubmission) -> dict[str, Any]:
    """Return a FHIR R4-shaped, synthetic-only export; this is not a conformance claim."""
    subject = {"reference": f"Patient/{submission.patient_id}"}
    source_data = submission.answers
    items = source_data.get("items", [])
    questionnaire_response = {
        "resourceType": "QuestionnaireResponse",
        "id": str(submission.id),
        "status": "completed",
        "subject": subject,
        "questionnaire": source_data.get("questionnaire_canonical", ""),
        "authored": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "item": [_questionnaire_item(item) for item in items if isinstance(item, dict)],
        "meta": {"tag": [_DEMO_TAG]},
    }
    entries: list[dict[str, Any]] = [{"resource": questionnaire_response}]
    entries.extend(
        {"resource": _observation(item, subject, submission)}
        for item in items
        if isinstance(item, dict)
    )
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "meta": {"tag": [_DEMO_TAG]},
        "entry": entries,
    }


def _questionnaire_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "linkId": str(item.get("link_id", "")),
        "text": str(item.get("label", "")),
        "answer": [_answer_value(item.get("value"))],
    }


def _observation(
    item: dict[str, Any], subject: dict[str, str], submission: CheckInSubmission
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "resourceType": "Observation",
        "status": "final",
        "subject": subject,
        "effectiveDateTime": (
            submission.submitted_at.isoformat() if submission.submitted_at else None
        ),
        "code": {"text": str(item.get("label", item.get("link_id", "")))},
        "method": {"text": "patient-supplied synthetic demo response"},
        "meta": {"tag": [_DEMO_TAG]},
    }
    observation.update(_observation_value(item.get("value")))
    return observation


def _answer_value(value: Any) -> dict[str, Any]:
    return _typed_value("value", value)


def _observation_value(value: Any) -> dict[str, Any]:
    return _typed_value("value", value)


def _typed_value(prefix: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {f"{prefix}Boolean": value}
    if isinstance(value, int):
        return {f"{prefix}Integer": value}
    if isinstance(value, list):
        return {f"{prefix}String": ", ".join(str(item) for item in value)}
    return {f"{prefix}String": str(value)}
