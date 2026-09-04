from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import (
    ApprovalDecision,
    ApprovalPolicy,
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    Organization,
    Outcome,
    PathwayDefinition,
    ProposedChange,
    ReportedNeed,
    RoleAssignment,
    SafetySignal,
    SafetySignalResolution,
    SignalRule,
    SyntheticPatient,
    User,
)
from app.db.repositories import SqlAlchemyFhirRepository, SqlAlchemyNavigatorRepository
from app.domain.enums import (
    ApprovalChangeType,
    ApprovalDecisionValue,
    CheckInStatus,
    NeedStatus,
    OutcomeDisposition,
    SafetySeverity,
    SafetySignalStatus,
    SignalRuleKind,
    SubmissionSource,
    UserRole,
)
from app.fhir.check_in_mapper import map_check_in_to_fhir_bundle


def _database_is_reachable(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.host:
        return False
    try:
        with socket.create_connection((url.host, url.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def db_session() -> Iterator[Session]:
    if not _database_is_reachable(settings.database_url):
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for canonical read tests")
    engine = create_engine(settings.database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _seed_submission_and_signal(
    session: Session,
) -> tuple[Organization, SyntheticPatient, CheckInSubmission, SafetySignal]:
    now = datetime.now(UTC)
    organization = Organization(name=f"Canonical repository {uuid4()}")
    patient_author = User(
        email=f"canonical-patient-{uuid4()}@example.test",
        display_name="Canonical patient author",
    )
    session.add_all([organization, patient_author])
    session.flush()
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"canonical-{uuid4()}",
        display_name="Canonical patient",
    )
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug=f"canonical-{uuid4()}",
        version=1,
        name="Canonical pathway",
    )
    session.add_all([patient, pathway])
    session.flush()
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=patient.id,
        status="active",
    )
    definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition_id=pathway.id,
        slug=f"canonical-check-in-{uuid4()}",
        version=1,
        title="Canonical check-in",
        questionnaire={},
    )
    rule = SignalRule(
        organization_id=organization.id,
        rule_code="registered-nausea-rule",
        version=3,
        rule_kind=SignalRuleKind.DETERMINISTIC,
        name="Registered nausea rule",
    )
    session.add_all([episode, definition, rule])
    session.flush()
    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers={"questionnaire_canonical": "urn:test:canonical|1", "items": []},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=patient_author.id,
        submitted_at=now,
    )
    session.add(submission)
    session.flush()
    signal = SafetySignal(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=submission.id,
        signal_rule_id=rule.id,
        signal_rule_version=rule.version,
        deterministic_level=SafetySeverity.URGENT,
        effective_level=SafetySeverity.URGENT,
        status=SafetySignalStatus.OPEN,
        evidence=[{"field": "nausea_change", "text": "worse"}],
    )
    session.add(signal)
    session.flush()
    return organization, patient, submission, signal


def test_fhir_repository_loads_registered_rule_identity_for_detected_issue(
    db_session: Session,
) -> None:
    """This fails if a public DetectedIssue substitutes the rule UUID for its identity."""
    organization, patient, submission, signal = _seed_submission_and_signal(db_session)
    db_session.expire_all()

    records = SqlAlchemyFhirRepository(db_session).list_submission_safety_signals(
        submission_id=submission.id,
        patient_id=patient.id,
        organization_id=organization.id,
    )
    bundle = map_check_in_to_fhir_bundle(
        submission,
        safety_signals=[record.signal for record in records],
        effective_signal_states={record.signal.id: record.effective_state for record in records},
    )
    issue = next(
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "DetectedIssue"
    )

    assert signal.id == records[0].signal.id
    assert issue["code"]["coding"][0]["code"] == "registered-nausea-rule"
    assert issue["code"]["coding"][0]["display"] == "Registered nausea rule"


def test_navigator_repository_reads_active_submissions_from_the_real_view(
    db_session: Session,
) -> None:
    """This fails if both immutable rows leak instead of only the correction-chain leaf."""
    organization, patient, predecessor, _ = _seed_submission_and_signal(db_session)
    correction = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=predecessor.care_episode_id,
        check_in_definition_id=predecessor.check_in_definition_id,
        status=CheckInStatus.SUBMITTED,
        answers={"questionnaire_canonical": "urn:test:canonical|1", "items": []},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=predecessor.submitted_by_user_id,
        supersedes_submission_id=predecessor.id,
        submitted_at=datetime.now(UTC),
    )
    db_session.add(correction)
    db_session.flush()

    submissions = SqlAlchemyNavigatorRepository(db_session).list_active_submissions(
        patient_id=patient.id,
        organization_id=organization.id,
    )

    assert [submission.id for submission in submissions] == [correction.id]


def test_navigator_repository_reads_open_needs_from_the_real_effective_view(
    db_session: Session,
) -> None:
    """This fails if a raw-open need remains public after its Outcome closes it."""
    organization, patient, submission, _ = _seed_submission_and_signal(db_session)
    closed_need = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=submission.care_episode_id,
        source_submission_id=submission.id,
        kind="symptom_change",
        status=NeedStatus.OPEN,
        evidence=[],
    )
    open_need = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=submission.care_episode_id,
        source_submission_id=submission.id,
        kind="transportation",
        status=NeedStatus.OPEN,
        evidence=[],
    )
    db_session.add_all([closed_need, open_need])
    db_session.flush()
    db_session.add(
        Outcome(
            organization_id=organization.id,
            patient_id=patient.id,
            reported_need_id=closed_need.id,
            recorded_by_user_id=submission.submitted_by_user_id,
            disposition=OutcomeDisposition.RESOLVED,
            note="Synthetic need closed.",
            idempotency_key=f"canonical-close-{uuid4()}",
            recorded_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    needs = SqlAlchemyNavigatorRepository(db_session).list_open_needs(
        patient_id=patient.id,
        organization_id=organization.id,
    )

    assert closed_need.status is NeedStatus.OPEN
    assert [need.id for need in needs] == [open_need.id]


def test_fhir_repository_reads_signal_state_from_the_real_effective_view(
    db_session: Session,
) -> None:
    """This fails if the raw signal state hides an immutable resolution fact."""
    organization, patient, submission, signal = _seed_submission_and_signal(db_session)
    signal.status = SafetySignalStatus.ACKNOWLEDGED
    signal.acknowledged_by_user_id = submission.submitted_by_user_id
    signal.acknowledged_at = datetime.now(UTC)
    db_session.flush()
    resolution = SafetySignalResolution(
        organization_id=organization.id,
        safety_signal_id=signal.id,
        resolved_by_user_id=submission.submitted_by_user_id,
        resolved_at=datetime.now(UTC),
        resolution_reason="Synthetic concern resolved.",
    )
    db_session.add(resolution)
    db_session.flush()

    records = SqlAlchemyFhirRepository(db_session).list_submission_safety_signals(
        submission_id=submission.id,
        patient_id=patient.id,
        organization_id=organization.id,
    )

    assert signal.status is SafetySignalStatus.ACKNOWLEDGED
    assert [(record.effective_state, record.resolution.id) for record in records] == [
        ("resolved", resolution.id)
    ]


def test_fhir_repository_returns_only_qualifying_approved_authorizers(
    db_session: Session,
) -> None:
    """This fails if declined or wrong-role decisions cross the public FHIR boundary."""
    organization, _, _, signal = _seed_submission_and_signal(db_session)
    now = datetime.now(UTC)
    proposer = User(
        email=f"canonical-proposer-{uuid4()}@example.test",
        display_name="Canonical proposer",
    )
    approved_authorizer = User(
        email=f"canonical-approved-{uuid4()}@example.test",
        display_name="Approved authorizer",
    )
    declined_authorizer = User(
        email=f"canonical-declined-{uuid4()}@example.test",
        display_name="Declined authorizer",
    )
    wrong_role_authorizer = User(
        email=f"canonical-wrong-role-{uuid4()}@example.test",
        display_name="Wrong-role authorizer",
    )
    db_session.add_all(
        [proposer, approved_authorizer, declined_authorizer, wrong_role_authorizer]
    )
    db_session.flush()
    approved_role = RoleAssignment(
        organization_id=organization.id,
        user_id=approved_authorizer.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=1),
    )
    declined_role = RoleAssignment(
        organization_id=organization.id,
        user_id=declined_authorizer.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=1),
    )
    wrong_role = RoleAssignment(
        organization_id=organization.id,
        user_id=wrong_role_authorizer.id,
        role=UserRole.ADMINISTRATOR,
        granted_at=now - timedelta(hours=1),
    )
    proposer_role = RoleAssignment(
        organization_id=organization.id,
        user_id=proposer.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=1),
    )
    policy = ApprovalPolicy(
        organization_id=organization.id,
        change_type=ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY,
        version=1,
        effective_from=now - timedelta(days=1),
        allow_self_approval=False,
        required_approval_count=2,
        required_approver_role=UserRole.NAVIGATOR,
    )
    db_session.add_all(
        [approved_role, declined_role, wrong_role, proposer_role, policy]
    )
    db_session.flush()

    def proposal(level: str) -> ProposedChange:
        return ProposedChange(
            organization_id=organization.id,
            proposed_by_user_id=proposer.id,
            proposed_at=now,
            change_type=ApprovalChangeType.OVERRIDE_SIGNAL_SEVERITY,
            proposed_value={"level": level},
            rationale=f"Set synthetic severity to {level}",
            value_schema_id="ojcc.override-signal-severity",
            value_schema_version=1,
            safety_signal_id=signal.id,
            approval_policy_id=policy.id,
            approval_policy_version=policy.version,
            allow_self_approval_snapshot=False,
            required_approval_count_snapshot=2,
            required_approver_role_snapshot=UserRole.NAVIGATOR,
        )

    approved_proposal = proposal("routine")
    declined_proposal = proposal("urgent")
    wrong_role_proposal = proposal("emergent")
    self_approval_proposal = proposal("routine")
    db_session.add_all(
        [
            approved_proposal,
            declined_proposal,
            wrong_role_proposal,
            self_approval_proposal,
        ]
    )
    db_session.flush()
    approved_decision = ApprovalDecision(
        organization_id=organization.id,
        proposed_change_id=approved_proposal.id,
        authorized_by_user_id=approved_authorizer.id,
        qualifying_role_assignment_id=approved_role.id,
        qualifying_role_snapshot=UserRole.NAVIGATOR,
        decision=ApprovalDecisionValue.APPROVED,
        authorized_at=now,
    )
    declined_decision = ApprovalDecision(
        organization_id=organization.id,
        proposed_change_id=declined_proposal.id,
        authorized_by_user_id=declined_authorizer.id,
        qualifying_role_assignment_id=declined_role.id,
        qualifying_role_snapshot=UserRole.NAVIGATOR,
        decision=ApprovalDecisionValue.DECLINED,
        authorized_at=now,
        reason="Synthetic reviewer declined the change.",
    )
    db_session.add_all([approved_decision, declined_decision])
    db_session.flush()
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    wrong_role_decision = ApprovalDecision(
        organization_id=organization.id,
        proposed_change_id=wrong_role_proposal.id,
        authorized_by_user_id=wrong_role_authorizer.id,
        qualifying_role_assignment_id=wrong_role.id,
        qualifying_role_snapshot=UserRole.ADMINISTRATOR,
        decision=ApprovalDecisionValue.APPROVED,
        authorized_at=now,
    )
    disallowed_self_approval = ApprovalDecision(
        organization_id=organization.id,
        proposed_change_id=self_approval_proposal.id,
        authorized_by_user_id=proposer.id,
        qualifying_role_assignment_id=proposer_role.id,
        qualifying_role_snapshot=UserRole.NAVIGATOR,
        decision=ApprovalDecisionValue.APPROVED,
        authorized_at=now,
    )
    db_session.add_all([wrong_role_decision, disallowed_self_approval])
    db_session.flush()
    db_session.execute(text("SET LOCAL session_replication_role = origin"))

    repository = SqlAlchemyFhirRepository(db_session)
    proposal_records = repository.list_signal_proposals(
        signal_ids=[signal.id],
        organization_id=organization.id,
    )
    decisions = repository.list_approval_decisions(
        proposal_ids=[
            approved_proposal.id,
            declined_proposal.id,
            wrong_role_proposal.id,
            self_approval_proposal.id,
        ],
        organization_id=organization.id,
    )

    assert {
        record.proposal.id: record.effective_state for record in proposal_records
    } == {
        approved_proposal.id: "pending",
        declined_proposal.id: "declined",
        wrong_role_proposal.id: "pending",
        self_approval_proposal.id: "pending",
    }
    assert decisions == {approved_proposal.id: [approved_decision]}
    ProposedChange,
    RoleAssignment,
