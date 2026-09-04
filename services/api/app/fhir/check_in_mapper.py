from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from app.db.models import (
    ApprovalDecision,
    CheckInSubmission,
    ProposedChange,
    SafetySignal,
    SafetySignalResolution,
)
from app.domain.enums import SubmissionSource

_DEMO_TAG = {
    "system": "https://oncology-journey-command-center.example/tags",
    "code": "synthetic-demo",
    "display": "Synthetic demo data only",
}
_PATIENT_SUPPLIED_TAG = {
    "system": "https://oncology-journey-command-center.example/provenance",
    "code": "patient-supplied",
    "display": "Patient-supplied synthetic response",
}
_FHIR_ROOT = "https://oncology-journey-command-center.example/fhir"
_SAFETY_SIGNAL_PROFILE = f"{_FHIR_ROOT}/StructureDefinition/safety-signal"
_DETERMINISTIC_SEVERITY_EXTENSION = (
    f"{_FHIR_ROOT}/StructureDefinition/deterministic-severity"
)
_EFFECTIVE_SEVERITY_EXTENSION = f"{_FHIR_ROOT}/StructureDefinition/effective-severity"
_DISMISSAL_EXTENSION = f"{_FHIR_ROOT}/StructureDefinition/dismissal"
_SIGNAL_RULE_SYSTEM = f"{_FHIR_ROOT}/CodeSystem/signal-rule"
_PROVENANCE_AGENT_ROLE_SYSTEM = f"{_FHIR_ROOT}/CodeSystem/provenance-agent-role"
_PROPOSAL_IDENTIFIER_SYSTEM = f"{_FHIR_ROOT}/NamingSystem/proposed-change"


def map_check_in_to_fhir_bundle(
    submission: CheckInSubmission,
    *,
    is_superseded: bool = False,
    predecessor_submission: CheckInSubmission | None = None,
    safety_signals: Iterable[SafetySignal] = (),
    effective_signal_states: Mapping[UUID, str] | None = None,
    signal_resolutions: Mapping[UUID, SafetySignalResolution] | None = None,
    applied_proposals: Iterable[ProposedChange] = (),
    effective_proposal_states: Mapping[UUID, str] | None = None,
    approval_decisions: Mapping[UUID, Iterable[ApprovalDecision]] | None = None,
) -> dict[str, Any]:
    """Return a FHIR R4-shaped, synthetic-only export; this is not a conformance claim."""
    subject = {"reference": f"Patient/{submission.patient_id}"}
    source_data = submission.answers
    items = source_data.get("items", [])
    questionnaire_response = {
        "resourceType": "QuestionnaireResponse",
        "id": str(submission.id),
        "status": "amended" if is_superseded else "completed",
        "subject": subject,
        "questionnaire": source_data.get("questionnaire_canonical", ""),
        "authored": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "item": _questionnaire_items(items, source_data.get("free_text")),
        "meta": {"tag": [_DEMO_TAG]},
    }
    entries: list[dict[str, Any]] = [{"resource": questionnaire_response}]
    if _is_patient_supplied(submission, source_data):
        entries.extend(
            {"resource": _observation(item, subject, submission)}
            for item in items
            if isinstance(item, dict)
        )
    if predecessor_submission is not None:
        entries.append(
            {"resource": _revision_provenance(submission, predecessor_submission)}
        )

    signal_states = effective_signal_states or {}
    resolutions = signal_resolutions or {}
    proposal_states = effective_proposal_states or {}
    decisions_by_proposal = approval_decisions or {}
    signals = list(safety_signals)
    proposals = [
        proposal
        for proposal in applied_proposals
        if proposal_states.get(proposal.id) == "approved"
        and _proposal_is_applied(proposal, signals)
    ]
    dismissal_proposals = {
        proposal.safety_signal_id: proposal
        for proposal in proposals
        if _enum_value(proposal.change_type) == "dismiss_signal"
        and proposal.safety_signal_id is not None
    }
    for signal in signals:
        entries.append(
            {
                "resource": _detected_issue(
                    signal,
                    effective_state=signal_states.get(signal.id, _enum_value(signal.status)),
                    resolution=resolutions.get(signal.id),
                    dismissal_proposal=dismissal_proposals.get(signal.id),
                )
            }
        )
    entries.extend(
        {
            "resource": _proposal_provenance(
                proposal,
                decisions_by_proposal.get(proposal.id, ()),
            )
        }
        for proposal in proposals
    )
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "meta": {"tag": [_DEMO_TAG]},
        "entry": entries,
    }


def _is_patient_supplied(
    submission: CheckInSubmission, source_data: Mapping[str, Any]
) -> bool:
    source = submission.submission_source
    if source is not None and _enum_value(source) == SubmissionSource.PATIENT.value:
        return True
    provenance = source_data.get("provenance")
    return isinstance(provenance, Mapping) and provenance.get("source") == "patient-supplied"


def _revision_provenance(
    submission: CheckInSubmission, predecessor: CheckInSubmission
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "Provenance",
        "id": f"submission-revision-{submission.id}",
        "target": [{"reference": f"QuestionnaireResponse/{submission.id}"}],
        "recorded": submission.submitted_at.isoformat() if submission.submitted_at else None,
        "entity": [
            {
                "role": "revision",
                "what": {"reference": f"QuestionnaireResponse/{predecessor.id}"},
            }
        ],
        "meta": {"tag": [_DEMO_TAG]},
    }
    if submission.submitted_by_user_id is not None:
        resource["agent"] = [
            _provenance_agent(
                role="proposer",
                reference=f"Practitioner/{submission.submitted_by_user_id}",
            )
        ]
    return resource


def _detected_issue(
    signal: SafetySignal,
    *,
    effective_state: str,
    resolution: SafetySignalResolution | None,
    dismissal_proposal: ProposedChange | None,
) -> dict[str, Any]:
    deterministic_level = _enum_value(signal.deterministic_level)
    effective_level = _enum_value(signal.effective_level)
    rule = signal.__dict__.get("rule")
    rule_code = getattr(rule, "rule_code", str(signal.signal_rule_id))
    rule_name = getattr(rule, "name", rule_code)
    extensions: list[dict[str, Any]] = [
        {"url": _DETERMINISTIC_SEVERITY_EXTENSION, "valueCode": deterministic_level},
        {"url": _EFFECTIVE_SEVERITY_EXTENSION, "valueCode": effective_level},
    ]
    if effective_state == "dismissed" and dismissal_proposal is not None:
        category = dismissal_proposal.proposed_value.get("category")
        if isinstance(category, str):
            extensions.append({"url": _DISMISSAL_EXTENSION, "valueCode": category})
    issue: dict[str, Any] = {
        "resourceType": "DetectedIssue",
        "id": str(signal.id),
        "meta": {"profile": [_SAFETY_SIGNAL_PROFILE], "tag": [_DEMO_TAG]},
        "status": "final" if effective_state in {"resolved", "dismissed"} else "preliminary",
        "severity": _fhir_severity(effective_level),
        "patient": {"reference": f"Patient/{signal.patient_id}"},
        "identifiedDateTime": signal.created_at.isoformat() if signal.created_at else None,
        "code": {
            "coding": [
                {
                    "system": _SIGNAL_RULE_SYSTEM,
                    "code": str(rule_code),
                    "version": str(signal.signal_rule_version),
                    "display": str(rule_name),
                }
            ]
        },
        "evidence": [
            {
                "code": [{"text": str(item.get("text", item.get("field", "")))}],
                "detail": [
                    {"reference": f"QuestionnaireResponse/{signal.source_submission_id}"}
                ],
            }
            for item in signal.evidence
            if isinstance(item, dict) and signal.source_submission_id is not None
        ],
        "extension": extensions,
    }
    if resolution is not None:
        issue["mitigation"] = [
            {
                "action": {"text": resolution.resolution_reason},
                "date": resolution.resolved_at.isoformat(),
                "author": {"reference": f"Practitioner/{resolution.resolved_by_user_id}"},
            }
        ]
    return issue


def _proposal_is_applied(
    proposal: ProposedChange, signals: Iterable[SafetySignal]
) -> bool:
    if proposal.safety_signal_id is None:
        return True
    return any(
        signal.id == proposal.safety_signal_id
        and proposal.id
        in {
            signal.dismissal_proposed_change_id,
            signal.current_severity_override_proposed_change_id,
        }
        for signal in signals
    )


def _proposal_provenance(
    proposal: ProposedChange, decisions: Iterable[ApprovalDecision]
) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    if proposal.proposed_by_user_id is not None:
        agents.append(
            _provenance_agent(
                role="proposer",
                reference=f"Practitioner/{proposal.proposed_by_user_id}",
            )
        )
    elif proposal.proposed_by_agent_run_id is not None:
        agents.append(
            _provenance_agent(
                role="proposer",
                reference=f"Device/{proposal.proposed_by_agent_run_id}",
            )
        )
    ordered_decisions = sorted(
        decisions,
        key=lambda decision: (decision.authorized_at, str(decision.id)),
    )
    agents.extend(
        _provenance_agent(
            role="authorizer",
            reference=f"Practitioner/{decision.authorized_by_user_id}",
        )
        for decision in ordered_decisions
    )
    return {
        "resourceType": "Provenance",
        "id": f"proposal-{proposal.id}",
        "target": [_proposal_target(proposal)],
        "recorded": (
            ordered_decisions[-1].authorized_at.isoformat()
            if ordered_decisions
            else proposal.proposed_at.isoformat()
        ),
        "policy": [
            f"urn:ojcc:approval-policy:{proposal.approval_policy_id}"
            f"|{proposal.approval_policy_version}"
        ],
        "agent": agents,
        "entity": [
            {
                "role": "source",
                "what": {
                    "identifier": {
                        "system": _PROPOSAL_IDENTIFIER_SYSTEM,
                        "value": str(proposal.id),
                    }
                },
            }
        ],
        "reason": [{"text": proposal.rationale}],
        "meta": {"tag": [_DEMO_TAG]},
    }


def _proposal_agent_type(role: str) -> dict[str, Any]:
    return {"coding": [{"system": _PROVENANCE_AGENT_ROLE_SYSTEM, "code": role}]}


def _provenance_agent(*, role: str, reference: str) -> dict[str, Any]:
    return {"type": _proposal_agent_type(role), "who": {"reference": reference}}


def _proposal_target(proposal: ProposedChange) -> dict[str, str]:
    if proposal.safety_signal_id is not None:
        return {"reference": f"DetectedIssue/{proposal.safety_signal_id}"}
    if proposal.navigation_task_id is not None:
        return {"reference": f"Task/{proposal.navigation_task_id}"}
    return {"reference": f"Communication/{proposal.patient_message_id}"}


def _fhir_severity(level: str) -> str:
    return {"routine": "low", "urgent": "moderate", "emergent": "high"}.get(
        level, "low"
    )


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _questionnaire_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "linkId": str(item.get("link_id", "")),
        "text": str(item.get("label", "")),
        "answer": [_answer_value(item.get("value"))],
    }


def _questionnaire_items(items: Any, free_text: Any) -> list[dict[str, Any]]:
    questionnaire_items = [_questionnaire_item(item) for item in items if isinstance(item, dict)]
    if isinstance(free_text, str) and free_text:
        questionnaire_items.append(
            {
                "linkId": "additional_context",
                "text": "Additional patient context",
                "answer": [{"valueString": free_text}],
            }
        )
    return questionnaire_items


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
        "meta": {"tag": [_DEMO_TAG, _PATIENT_SUPPLIED_TAG]},
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
