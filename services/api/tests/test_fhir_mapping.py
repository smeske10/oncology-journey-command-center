from datetime import UTC, datetime
from uuid import uuid4

from app.db.models import (
    ApprovalDecision,
    CheckInSubmission,
    ProposedChange,
    SafetySignal,
    SafetySignalResolution,
    SignalRule,
)
from app.domain.enums import (
    ApprovalChangeType,
    ApprovalDecisionValue,
    CheckInStatus,
    SafetySeverity,
    SafetySignalStatus,
    UserRole,
)
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
    observations = [
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Observation"
    ]
    assert observations[0]["meta"]["tag"][1]["code"] == "patient-supplied"
    assert questionnaire_response["item"][-1]["text"] == "Additional patient context"
    assert (
        questionnaire_response["item"][-1]["answer"][0]["valueString"]
        == "Synthetic check-in context."
    )


def test_corrections_remain_distinct_and_export_revision_provenance() -> None:
    """This fails if correction history is overwritten or represented with non-R4 relatesTo."""
    organization_id = uuid4()
    patient_id = uuid4()
    definition_id = uuid4()
    predecessor = CheckInSubmission(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient_id,
        check_in_definition_id=definition_id,
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        answers={"questionnaire_canonical": "urn:test|1", "items": []},
    )
    correction = CheckInSubmission(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient_id,
        check_in_definition_id=definition_id,
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime(2026, 8, 17, 13, 0, tzinfo=UTC),
        answers={"questionnaire_canonical": "urn:test|1", "items": []},
        supersedes_submission_id=predecessor.id,
        submitted_by_user_id=uuid4(),
    )

    predecessor_bundle = map_check_in_to_fhir_bundle(predecessor, is_superseded=True)
    correction_bundle = map_check_in_to_fhir_bundle(
        correction,
        predecessor_submission=predecessor,
    )

    predecessor_response = predecessor_bundle["entry"][0]["resource"]
    correction_responses = [
        entry["resource"]
        for entry in correction_bundle["entry"]
        if entry["resource"]["resourceType"] == "QuestionnaireResponse"
    ]
    revision = next(
        entry["resource"]
        for entry in correction_bundle["entry"]
        if entry["resource"]["resourceType"] == "Provenance"
    )
    assert predecessor_response["id"] == str(predecessor.id)
    assert predecessor_response["status"] == "amended"
    assert [response["id"] for response in correction_responses] == [str(correction.id)]
    assert correction_responses[0]["status"] == "completed"
    assert "relatesTo" not in correction_responses[0]
    assert revision["target"] == [{"reference": f"QuestionnaireResponse/{correction.id}"}]
    assert revision["entity"] == [
        {
            "role": "revision",
            "what": {"reference": f"QuestionnaireResponse/{predecessor.id}"},
        }
    ]


def test_safety_signal_maps_to_profiled_detected_issue_with_declared_extensions() -> None:
    """This fails if safety export loses rule, dual severity, dismissal, or resolution facts."""
    organization_id = uuid4()
    patient_id = uuid4()
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient_id,
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        answers={"questionnaire_canonical": "urn:test|1", "items": []},
    )
    rule = SignalRule(
        id=uuid4(),
        organization_id=organization_id,
        rule_code="urgent-fever",
        version=3,
        rule_kind="deterministic",
        name="Urgent fever",
    )
    signal = SafetySignal(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient_id,
        care_episode_id=uuid4(),
        source_submission_id=submission.id,
        signal_rule_id=rule.id,
        signal_rule_version=rule.version,
        deterministic_level=SafetySeverity.EMERGENT,
        effective_level=SafetySeverity.URGENT,
        status=SafetySignalStatus.ACKNOWLEDGED,
        evidence=[{"field": "fever", "text": "yes"}],
        rule=rule,
    )
    resolution = SafetySignalResolution(
        id=uuid4(),
        organization_id=organization_id,
        safety_signal_id=signal.id,
        resolved_by_user_id=uuid4(),
        resolved_at=datetime(2026, 8, 17, 13, 0, tzinfo=UTC),
        resolution_reason="Navigator confirmed the synthetic concern was handled.",
    )

    bundle = map_check_in_to_fhir_bundle(
        submission,
        safety_signals=[signal],
        effective_signal_states={signal.id: "resolved"},
        signal_resolutions={signal.id: resolution},
    )

    issue = next(
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "DetectedIssue"
    )
    assert issue["meta"]["profile"] == [
        "https://oncology-journey-command-center.example/fhir/StructureDefinition/safety-signal"
    ]
    assert issue["code"]["coding"][0] == {
        "system": "https://oncology-journey-command-center.example/fhir/CodeSystem/signal-rule",
        "code": "urgent-fever",
        "version": "3",
        "display": "Urgent fever",
    }
    assert issue["severity"] == "moderate"
    extensions = {extension["url"]: extension for extension in issue["extension"]}
    assert extensions[
        "https://oncology-journey-command-center.example/fhir/StructureDefinition/deterministic-severity"
    ]["valueCode"] == "emergent"
    assert extensions[
        "https://oncology-journey-command-center.example/fhir/StructureDefinition/effective-severity"
    ]["valueCode"] == "urgent"
    assert issue["evidence"][0]["detail"] == [
        {"reference": f"QuestionnaireResponse/{submission.id}"}
    ]
    assert issue["mitigation"][0]["action"]["text"] == (
        "Navigator confirmed the synthetic concern was handled."
    )


def test_only_applied_proposals_export_complete_typed_authorization_provenance() -> None:
    """This fails if internal proposals leak or any qualifying human is omitted from lineage."""
    organization_id = uuid4()
    proposer_id = uuid4()
    authorizer_ids = [uuid4(), uuid4()]
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=uuid4(),
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        answers={"questionnaire_canonical": "urn:test|1", "items": []},
    )
    signal = SafetySignal(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=submission.patient_id,
        care_episode_id=uuid4(),
        source_submission_id=submission.id,
        signal_rule_id=uuid4(),
        signal_rule_version=1,
        deterministic_level=SafetySeverity.ROUTINE,
        effective_level=SafetySeverity.ROUTINE,
        status=SafetySignalStatus.ACKNOWLEDGED,
        evidence=[],
    )
    applied = ProposedChange(
        id=uuid4(),
        organization_id=organization_id,
        proposed_by_user_id=proposer_id,
        proposed_at=datetime(2026, 8, 17, 12, 30, tzinfo=UTC),
        change_type=ApprovalChangeType.DISMISS_SIGNAL,
        proposed_value={"category": "false_positive"},
        rationale="Synthetic dismissal rationale",
        value_schema_id="ojcc.dismiss-signal",
        value_schema_version=1,
        safety_signal_id=signal.id,
        approval_policy_id=uuid4(),
        approval_policy_version=4,
        deterministic_severity_threshold_snapshot=SafetySeverity.URGENT,
        allow_self_approval_snapshot=False,
        required_approval_count_snapshot=2,
        required_approver_role_snapshot=UserRole.NAVIGATOR,
    )
    pending = ProposedChange(
        id=uuid4(),
        organization_id=organization_id,
        proposed_by_user_id=proposer_id,
        proposed_at=datetime(2026, 8, 17, 12, 45, tzinfo=UTC),
        change_type=ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY,
        proposed_value={"level": "urgent"},
        rationale="Still awaiting review",
        value_schema_id="ojcc.override-signal-severity",
        value_schema_version=1,
        safety_signal_id=signal.id,
        approval_policy_id=uuid4(),
        approval_policy_version=1,
        allow_self_approval_snapshot=False,
        required_approval_count_snapshot=1,
        required_approver_role_snapshot=UserRole.NAVIGATOR,
    )
    decisions = [
        ApprovalDecision(
            id=uuid4(),
            organization_id=organization_id,
            proposed_change_id=applied.id,
            authorized_by_user_id=authorizer_id,
            qualifying_role_assignment_id=uuid4(),
            qualifying_role_snapshot=UserRole.NAVIGATOR,
            decision=ApprovalDecisionValue.APPROVED,
            authorized_at=datetime(2026, 8, 17, 13, index, tzinfo=UTC),
        )
        for index, authorizer_id in enumerate(authorizer_ids)
    ]
    signal.dismissal_proposed_change_id = applied.id

    bundle = map_check_in_to_fhir_bundle(
        submission,
        safety_signals=[signal],
        effective_signal_states={signal.id: "dismissed"},
        applied_proposals=[applied, pending],
        effective_proposal_states={applied.id: "approved", pending.id: "pending"},
        approval_decisions={applied.id: decisions},
    )

    provenances = [
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Provenance"
        and entry["resource"]["id"].startswith("proposal-")
    ]
    assert [item["id"] for item in provenances] == [f"proposal-{applied.id}"]
    provenance = provenances[0]
    roles = [agent["type"]["coding"][0]["code"] for agent in provenance["agent"]]
    assert roles == ["proposer", "authorizer", "authorizer"]
    assert provenance["agent"][0]["who"] == {"reference": f"Practitioner/{proposer_id}"}
    assert {agent["who"]["reference"] for agent in provenance["agent"][1:]} == {
        f"Practitioner/{authorizer_id}" for authorizer_id in authorizer_ids
    }
    assert provenance["policy"] == [
        f"urn:ojcc:approval-policy:{applied.approval_policy_id}|4"
    ]
    dismissal_extension = next(
        extension
        for extension in next(
            entry["resource"]
            for entry in bundle["entry"]
            if entry["resource"]["resourceType"] == "DetectedIssue"
        )["extension"]
        if extension["url"].endswith("/dismissal")
    )
    assert dismissal_extension["valueCode"] == "false_positive"
