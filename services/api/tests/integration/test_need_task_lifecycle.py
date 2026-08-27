from __future__ import annotations

import socket
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db.models import (
    AuditEvent,
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    NavigationTask,
    Organization,
    Outcome,
    PathwayDefinition,
    ReportedNeed,
    SyntheticPatient,
    User,
)
from app.domain.enums import (
    CheckInStatus,
    NavigationTaskStatus,
    NeedStatus,
    SubmissionSource,
)


@dataclass(frozen=True)
class AggregateIds:
    organization_id: UUID
    recorder_id: UUID
    patient_id: UUID
    episode_id: UUID
    need_id: UUID
    task_id: UUID


def _database_is_reachable(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.host:
        return False
    try:
        with socket.create_connection((url.host, url.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _database_is_reachable(settings.database_url):
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for need lifecycle tests")
    return settings.database_url


@pytest.fixture
def db_session(database_url: str) -> Iterator[Session]:
    engine = create_engine(database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def _seed_aggregate(
    session: Session,
    *,
    task: bool = True,
    task_assignee: bool = False,
) -> tuple[Organization, User, SyntheticPatient, CareEpisode, ReportedNeed, NavigationTask | None]:
    now = datetime.now(UTC)
    organization = Organization(name=f"Need lifecycle organization {uuid4()}")
    recorder = User(email=f"recorder-{uuid4()}@example.test", display_name="Recorder")
    patient_author = User(email=f"author-{uuid4()}@example.test", display_name="Author")
    session.add_all([organization, recorder, patient_author])
    session.flush()
    recorder.primary_organization_id = organization.id
    patient_author.primary_organization_id = organization.id
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"lifecycle-{uuid4()}",
        display_name="Lifecycle patient",
    )
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug=f"lifecycle-{uuid4()}",
        version=1,
        name="Lifecycle pathway",
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
        slug=f"lifecycle-check-in-{uuid4()}",
        version=1,
        title="Lifecycle check-in",
    )
    session.add_all([episode, definition])
    session.flush()
    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers={},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=patient_author.id,
        submitted_at=now,
    )
    session.add(submission)
    session.flush()
    values = {
        "organization_id": organization.id,
        "patient_id": patient.id,
        "source_submission_id": submission.id,
        "kind": "symptom_change",
        "status": NeedStatus.OPEN,
        "evidence": [{"field": "nausea_change", "text": "worse"}],
    }
    if hasattr(ReportedNeed, "care_episode_id"):
        values["care_episode_id"] = episode.id
    need = ReportedNeed(**values)
    session.add(need)
    session.flush()
    navigation_task = None
    if task:
        navigation_task = NavigationTask(
            organization_id=organization.id,
            patient_id=patient.id,
            reported_need_id=need.id,
            assignee_user_id=recorder.id if task_assignee else None,
            title="Review reported concern",
            status=(
                NavigationTaskStatus.ASSIGNED
                if task_assignee and hasattr(NavigationTaskStatus, "ASSIGNED")
                else NavigationTaskStatus.OPEN
            ),
        )
        session.add(navigation_task)
        session.flush()
    return organization, recorder, patient, episode, need, navigation_task


def _record_outcome(
    session: Session,
    *,
    organization_id: UUID,
    need_id: UUID,
    recorder_id: UUID,
    key: str,
    disposition: str = "resolved",
    note: str | None = "Handled by synthetic navigator.",
):
    from app.domain.outcomes import record_outcome

    return record_outcome(
        session,
        organization_id=organization_id,
        need_id=need_id,
        recorded_by_user_id=recorder_id,
        disposition=disposition,
        note=note,
        idempotency_key=key,
    )


def test_outcome_is_authoritative_closure_and_cancels_each_nonterminal_task(
    db_session: Session,
) -> None:
    organization, recorder, _, _, need, open_task = _seed_aggregate(db_session)
    assert open_task is not None
    assigned_task = NavigationTask(
        organization_id=organization.id,
        patient_id=need.patient_id,
        reported_need_id=need.id,
        assignee_user_id=recorder.id,
        title="Assigned concern",
        status="assigned",
    )
    completed_task = NavigationTask(
        organization_id=organization.id,
        patient_id=need.patient_id,
        reported_need_id=need.id,
        title="Already completed",
        status=NavigationTaskStatus.OPEN,
    )
    db_session.add_all([assigned_task, completed_task])
    db_session.flush()
    completed_task.status = NavigationTaskStatus.COMPLETED
    completed_task.completed_at = datetime.now(UTC)
    db_session.flush()

    result = _record_outcome(
        db_session,
        organization_id=organization.id,
        need_id=need.id,
        recorder_id=recorder.id,
        key="authoritative-closure",
    )
    db_session.flush()
    db_session.expire_all()

    closed_need = db_session.execute(
        text(
            "SELECT * FROM effective_need_state "
            "WHERE organization_id = :organization_id AND id = :need_id"
        ),
        {"organization_id": organization.id, "need_id": need.id},
    ).mappings().one()
    open_tasks = db_session.scalars(
        select(NavigationTask).where(NavigationTask.id.in_([open_task.id, assigned_task.id]))
    ).all()
    persisted_completed = db_session.get(NavigationTask, completed_task.id)

    assert closed_need.effective_state == "closed"
    assert all(task.status.value == "cancelled" for task in open_tasks)
    assert all(task.cancellation_reason.value == "need_closed" for task in open_tasks)
    assert all(task.cancelled_by_user_id == recorder.id for task in open_tasks)
    assert all(task.cancelled_at == result.recorded_at for task in open_tasks)
    assert persisted_completed is not None
    assert persisted_completed.status.value == "completed"

    events = db_session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == organization.id,
            AuditEvent.event_type == "task_cancelled_by_closure",
        )
        .order_by(AuditEvent.entity_id)
    ).all()
    assert [event.entity_id for event in events] == sorted(
        [open_task.id, assigned_task.id], key=str
    )
    assert all(event.entity_type == "navigation_task" for event in events)
    assert all(event.actor_user_id == recorder.id for event in events)
    assert all(event.created_at == result.recorded_at for event in events)
    assert all(event.payload["outcome_id"] == str(result.outcome_id) for event in events)
    assert result.cancelled_task_ids == tuple(sorted([open_task.id, assigned_task.id], key=str))


def test_task_creation_assignment_start_and_restart_are_rejected_after_closure(
    db_session: Session,
) -> None:
    organization, recorder, _, _, need, cancelled_task = _seed_aggregate(db_session)
    assert cancelled_task is not None
    completed_task = NavigationTask(
        organization_id=organization.id,
        patient_id=need.patient_id,
        reported_need_id=need.id,
        title="Completed before closure",
        status=NavigationTaskStatus.OPEN,
    )
    db_session.add(completed_task)
    db_session.flush()
    completed_task.status = NavigationTaskStatus.COMPLETED
    completed_task.completed_at = datetime.now(UTC)
    db_session.flush()
    _record_outcome(
        db_session,
        organization_id=organization.id,
        need_id=need.id,
        recorder_id=recorder.id,
        key="closed-task-guards",
    )
    db_session.flush()

    with pytest.raises(DBAPIError, match="closed"):
        with db_session.begin_nested():
            db_session.add(
                NavigationTask(
                    organization_id=organization.id,
                    patient_id=need.patient_id,
                    reported_need_id=need.id,
                    title="Illegal post-closure task",
                    status=NavigationTaskStatus.OPEN,
                )
            )
            db_session.flush()

    for task, state in (
        (cancelled_task, "assigned"),
        (cancelled_task, "in_progress"),
        (completed_task, "in_progress"),
    ):
        db_session.refresh(task)
        with pytest.raises(DBAPIError):
            with db_session.begin_nested():
                task.status = state
                task.assignee_user_id = recorder.id
                db_session.flush()
        db_session.expire(task)


def test_reopening_requires_a_closed_predecessor_and_inherits_no_tasks(
    db_session: Session,
) -> None:
    from app.domain.needs import NeedLifecycleConflict, reopen_need

    organization, recorder, _, _, need, _ = _seed_aggregate(db_session)
    with pytest.raises(NeedLifecycleConflict, match="active"):
        reopen_need(
            db_session,
            organization_id=organization.id,
            predecessor_need_id=need.id,
        )

    _record_outcome(
        db_session,
        organization_id=organization.id,
        need_id=need.id,
        recorder_id=recorder.id,
        key="reopen-closed-predecessor",
    )
    reopened = reopen_need(
        db_session,
        organization_id=organization.id,
        predecessor_need_id=need.id,
    )
    db_session.flush()

    assert reopened.id != need.id
    assert reopened.reopened_from_need_id == need.id
    assert reopened.source_submission_id is None
    assert reopened.organization_id == need.organization_id
    assert reopened.patient_id == need.patient_id
    assert reopened.care_episode_id == need.care_episode_id
    assert db_session.scalar(
        select(func.count())
        .select_from(NavigationTask)
        .where(NavigationTask.reported_need_id == reopened.id)
    ) == 0


def test_unassigned_task_keeps_need_open_and_first_assignment_or_start_advances_it(
    db_session: Session,
) -> None:
    organization, recorder, patient, episode, first_need, unassigned_task = _seed_aggregate(
        db_session
    )
    assert unassigned_task is not None
    db_session.refresh(first_need)
    assert first_need.status.value == "open"

    unassigned_task.assignee_user_id = recorder.id
    unassigned_task.status = "assigned"
    db_session.flush()
    db_session.refresh(first_need)
    assert first_need.status.value == "in_progress"

    source_submission_id = first_need.source_submission_id
    second_need = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=source_submission_id,
        kind="medication_question",
        status=NeedStatus.OPEN,
        evidence=[],
    )
    db_session.add(second_need)
    db_session.flush()
    started_task = NavigationTask(
        organization_id=organization.id,
        patient_id=patient.id,
        reported_need_id=second_need.id,
        title="Start immediately",
        status=NavigationTaskStatus.OPEN,
    )
    db_session.add(started_task)
    db_session.flush()
    assert second_need.status.value == "open"

    started_task.status = NavigationTaskStatus.IN_PROGRESS
    db_session.flush()
    db_session.refresh(second_need)
    assert second_need.status.value == "in_progress"


def test_completed_and_cancelled_tasks_are_irreversible(db_session: Session) -> None:
    organization, recorder, _, _, need, task = _seed_aggregate(db_session)
    assert task is not None
    task.status = NavigationTaskStatus.COMPLETED
    task.completed_at = datetime.now(UTC)
    db_session.flush()
    with pytest.raises(DBAPIError, match="terminal"):
        with db_session.begin_nested():
            task.status = NavigationTaskStatus.IN_PROGRESS
            db_session.flush()
    db_session.expire(task)

    other_task = NavigationTask(
        organization_id=organization.id,
        patient_id=need.patient_id,
        reported_need_id=need.id,
        title="Cancelled by closure",
        status=NavigationTaskStatus.OPEN,
    )
    db_session.add(other_task)
    db_session.flush()
    _record_outcome(
        db_session,
        organization_id=organization.id,
        need_id=need.id,
        recorder_id=recorder.id,
        key="cancel-terminal-task",
    )
    db_session.refresh(other_task)
    with pytest.raises(DBAPIError, match="terminal"):
        with db_session.begin_nested():
            other_task.status = NavigationTaskStatus.OPEN
            db_session.flush()


def test_need_origins_and_lifecycle_edges_are_tenant_patient_and_episode_safe(
    db_session: Session,
) -> None:
    first_org, first_user, first_patient, first_episode, first_need, _ = _seed_aggregate(
        db_session, task=False
    )
    second_org, _, second_patient, second_episode, second_need, _ = _seed_aggregate(
        db_session, task=False
    )

    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                NavigationTask(
                    organization_id=first_org.id,
                    patient_id=first_patient.id,
                    reported_need_id=second_need.id,
                    title="Cross-organization task",
                    status=NavigationTaskStatus.OPEN,
                )
            )
            db_session.flush()

    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                ReportedNeed(
                    organization_id=first_org.id,
                    patient_id=first_patient.id,
                    care_episode_id=first_episode.id,
                    source_submission_id=second_need.source_submission_id,
                    kind="other",
                    status=NeedStatus.OPEN,
                    evidence=[],
                )
            )
            db_session.flush()

    first_submission = db_session.get(CheckInSubmission, first_need.source_submission_id)
    assert first_submission is not None
    other_patient = SyntheticPatient(
        organization_id=first_org.id,
        external_ref=f"same-org-other-patient-{uuid4()}",
        display_name="Other patient",
    )
    db_session.add(other_patient)
    db_session.flush()
    other_patient_episode = CareEpisode(
        organization_id=first_org.id,
        patient_id=other_patient.id,
        status="active",
    )
    db_session.add(other_patient_episode)
    db_session.flush()
    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                ReportedNeed(
                    organization_id=first_org.id,
                    patient_id=other_patient.id,
                    care_episode_id=other_patient_episode.id,
                    source_submission_id=first_submission.id,
                    kind="other",
                    status=NeedStatus.OPEN,
                    evidence=[],
                )
            )
            db_session.flush()

    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                Outcome(
                    organization_id=first_org.id,
                    patient_id=other_patient.id,
                    reported_need_id=first_need.id,
                    recorded_by_user_id=first_user.id,
                    disposition="resolved",
                    note=None,
                    idempotency_key="cross-patient-outcome",
                    recorded_at=datetime.now(UTC),
                )
            )
            db_session.flush()

    other_episode = CareEpisode(
        organization_id=first_org.id,
        patient_id=first_patient.id,
        status="active",
    )
    db_session.add(other_episode)
    db_session.flush()
    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                ReportedNeed(
                    organization_id=first_org.id,
                    patient_id=first_patient.id,
                    care_episode_id=other_episode.id,
                    source_submission_id=first_submission.id,
                    kind="other",
                    status=NeedStatus.OPEN,
                    evidence=[],
                )
            )
            db_session.flush()

    _record_outcome(
        db_session,
        organization_id=first_org.id,
        need_id=first_need.id,
        recorder_id=first_user.id,
        key="scope-closed-predecessor",
    )
    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                ReportedNeed(
                    organization_id=second_org.id,
                    patient_id=second_patient.id,
                    care_episode_id=second_episode.id,
                    reopened_from_need_id=first_need.id,
                    kind="other",
                    status=NeedStatus.OPEN,
                    evidence=[],
                )
            )
            db_session.flush()


def _additional_submission(
    session: Session,
    *,
    organization_id: UUID,
    patient_id: UUID,
    episode_id: UUID,
    template: CheckInSubmission,
) -> CheckInSubmission:
    submission = CheckInSubmission(
        organization_id=organization_id,
        patient_id=patient_id,
        care_episode_id=episode_id,
        check_in_definition_id=template.check_in_definition_id,
        status=CheckInStatus.SUBMITTED,
        answers={},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=template.submitted_by_user_id,
        submitted_at=datetime.now(UTC),
    )
    session.add(submission)
    session.flush()
    return submission


@pytest.mark.parametrize("rewrite_scope", ["source", "episode", "patient", "organization"])
def test_reported_need_identity_and_source_origin_are_immutable_after_insert(
    db_session: Session,
    rewrite_scope: Literal["source", "episode", "patient", "organization"],
) -> None:
    organization, _, patient, episode, target, _ = _seed_aggregate(db_session, task=False)
    original_submission = db_session.get(CheckInSubmission, target.source_submission_id)
    assert original_submission is not None
    replacement_organization_id = organization.id
    replacement_patient_id = patient.id
    replacement_episode_id = episode.id

    if rewrite_scope == "source":
        replacement_submission = _additional_submission(
            db_session,
            organization_id=organization.id,
            patient_id=patient.id,
            episode_id=episode.id,
            template=original_submission,
        )
    elif rewrite_scope == "episode":
        replacement_episode = CareEpisode(
            organization_id=organization.id,
            patient_id=patient.id,
            status="active",
        )
        db_session.add(replacement_episode)
        db_session.flush()
        replacement_episode_id = replacement_episode.id
        replacement_submission = _additional_submission(
            db_session,
            organization_id=organization.id,
            patient_id=patient.id,
            episode_id=replacement_episode.id,
            template=original_submission,
        )
    elif rewrite_scope == "patient":
        replacement_patient = SyntheticPatient(
            organization_id=organization.id,
            external_ref=f"identity-rewrite-{uuid4()}",
            display_name="Identity rewrite patient",
        )
        db_session.add(replacement_patient)
        db_session.flush()
        replacement_episode = CareEpisode(
            organization_id=organization.id,
            patient_id=replacement_patient.id,
            status="active",
        )
        db_session.add(replacement_episode)
        db_session.flush()
        replacement_patient_id = replacement_patient.id
        replacement_episode_id = replacement_episode.id
        replacement_submission = _additional_submission(
            db_session,
            organization_id=organization.id,
            patient_id=replacement_patient.id,
            episode_id=replacement_episode.id,
            template=original_submission,
        )
    else:
        (
            replacement_organization,
            _,
            replacement_patient,
            replacement_episode,
            replacement_need,
            _,
        ) = _seed_aggregate(db_session, task=False)
        replacement_submission = db_session.get(
            CheckInSubmission, replacement_need.source_submission_id
        )
        assert replacement_submission is not None
        replacement_organization_id = replacement_organization.id
        replacement_patient_id = replacement_patient.id
        replacement_episode_id = replacement_episode.id

    with pytest.raises(DBAPIError, match="identity and origin are immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE reported_need "
                    "SET organization_id = :organization_id, patient_id = :patient_id, "
                    "care_episode_id = :episode_id, source_submission_id = :submission_id "
                    "WHERE id = :need_id"
                ),
                {
                    "organization_id": replacement_organization_id,
                    "patient_id": replacement_patient_id,
                    "episode_id": replacement_episode_id,
                    "submission_id": replacement_submission.id,
                    "need_id": target.id,
                },
            )


@pytest.mark.parametrize(
    "column_name",
    [
        "organization_id",
        "patient_id",
        "care_episode_id",
        "source_submission_id",
        "reopened_from_need_id",
    ],
)
def test_each_reported_need_identity_and_origin_column_is_update_guarded(
    db_session: Session,
    column_name: str,
) -> None:
    _, _, _, _, target, _ = _seed_aggregate(db_session, task=False)

    with pytest.raises(DBAPIError, match="identity and origin are immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(f"UPDATE reported_need SET {column_name} = :value WHERE id = :need_id"),
                {"value": uuid4(), "need_id": target.id},
            )


def test_source_need_cannot_be_rewritten_to_an_active_predecessor(db_session: Session) -> None:
    organization, _, patient, episode, target, _ = _seed_aggregate(db_session, task=False)
    predecessor = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=target.source_submission_id,
        kind="transportation",
        status=NeedStatus.OPEN,
        evidence=[],
    )
    db_session.add(predecessor)
    db_session.flush()

    with pytest.raises(DBAPIError, match="identity and origin are immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE reported_need SET source_submission_id = NULL, "
                    "reopened_from_need_id = :predecessor_id WHERE id = :target_id"
                ),
                {"predecessor_id": predecessor.id, "target_id": target.id},
            )


def test_source_need_with_an_attached_task_cannot_be_rewritten_to_a_closed_predecessor(
    db_session: Session,
) -> None:
    organization, recorder, patient, episode, target, retained_task = _seed_aggregate(db_session)
    assert retained_task is not None
    original_submission_id = target.source_submission_id
    predecessor = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=target.source_submission_id,
        kind="transportation",
        status=NeedStatus.OPEN,
        evidence=[],
    )
    db_session.add(predecessor)
    db_session.flush()
    _record_outcome(
        db_session,
        organization_id=organization.id,
        need_id=predecessor.id,
        recorder_id=recorder.id,
        key="closed-predecessor-update-bypass",
    )
    db_session.flush()

    with pytest.raises(DBAPIError, match="identity and origin are immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE reported_need SET source_submission_id = NULL, "
                    "reopened_from_need_id = :predecessor_id WHERE id = :target_id"
                ),
                {"predecessor_id": predecessor.id, "target_id": target.id},
            )

    db_session.refresh(target)
    db_session.refresh(retained_task)
    assert target.source_submission_id == original_submission_id
    assert target.reopened_from_need_id is None
    assert retained_task.reported_need_id == target.id
    assert retained_task.status == NavigationTaskStatus.OPEN


def _create_committed_aggregate(engine: Engine) -> AggregateIds:
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        organization, recorder, patient, episode, need, task = _seed_aggregate(session)
        assert task is not None
        ids = AggregateIds(
            organization_id=organization.id,
            recorder_id=recorder.id,
            patient_id=patient.id,
            episode_id=episode.id,
            need_id=need.id,
            task_id=task.id,
        )
        session.commit()
    return ids


def _delete_committed_aggregate(engine: Engine, ids: AggregateIds) -> None:
    with engine.begin() as connection:
        parameters = {"organization_id": ids.organization_id}
        for statement in (
            "DELETE FROM audit_event WHERE organization_id = :organization_id",
            "DELETE FROM outcome WHERE organization_id = :organization_id",
            "DELETE FROM navigation_task WHERE organization_id = :organization_id",
            "DELETE FROM agent_run WHERE organization_id = :organization_id",
            "DELETE FROM safety_signal WHERE organization_id = :organization_id",
            "DELETE FROM reported_need WHERE organization_id = :organization_id",
            "DELETE FROM check_in_submission WHERE organization_id = :organization_id",
            "DELETE FROM episode_pathway_assignment WHERE organization_id = :organization_id",
            "DELETE FROM check_in_definition WHERE organization_id = :organization_id",
            "DELETE FROM care_episode WHERE organization_id = :organization_id",
            "DELETE FROM pathway_definition WHERE organization_id = :organization_id",
            "DELETE FROM patient_identity_link WHERE organization_id = :organization_id",
            "DELETE FROM role_assignment WHERE organization_id = :organization_id",
            "DELETE FROM synthetic_patient WHERE organization_id = :organization_id",
            "DELETE FROM user_account WHERE primary_organization_id = :organization_id",
            "DELETE FROM organization WHERE id = :organization_id",
        ):
            connection.execute(text(statement), parameters)


@pytest.mark.parametrize("task_action", ["create", "assign", "start"])
def test_outcome_race_with_task_mutation_never_commits_closed_need_with_nonterminal_task(
    database_url: str,
    task_action: Literal["create", "assign", "start"],
) -> None:
    engine = create_engine(database_url)
    ids = _create_committed_aggregate(engine)
    barrier = Barrier(2)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def close_need() -> str:
        with session_factory() as session:
            barrier.wait(timeout=10)
            _record_outcome(
                session,
                organization_id=ids.organization_id,
                need_id=ids.need_id,
                recorder_id=ids.recorder_id,
                key=f"race-{task_action}",
            )
            session.commit()
            return "closed"

    def mutate_task() -> str:
        with session_factory() as session:
            barrier.wait(timeout=10)
            try:
                if task_action == "create":
                    session.add(
                        NavigationTask(
                            organization_id=ids.organization_id,
                            patient_id=ids.patient_id,
                            reported_need_id=ids.need_id,
                            title="Racing task creation",
                            status=NavigationTaskStatus.OPEN,
                        )
                    )
                else:
                    task = session.get(NavigationTask, ids.task_id)
                    assert task is not None
                    task.status = (
                        "assigned" if task_action == "assign" else NavigationTaskStatus.IN_PROGRESS
                    )
                    if task_action == "assign":
                        task.assignee_user_id = ids.recorder_id
                session.commit()
                return "committed"
            except SQLAlchemyError:
                session.rollback()
                return "rejected"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            close_future = executor.submit(close_need)
            task_future = executor.submit(mutate_task)
            assert close_future.result(timeout=20) == "closed"
            assert task_future.result(timeout=20) in {"committed", "rejected"}

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT effective_state, "
                    "(SELECT count(*) FROM navigation_task task "
                    " WHERE task.reported_need_id = state.id "
                    " AND task.status IN ('open', 'assigned', 'in_progress')) AS nonterminal_count "
                    "FROM effective_need_state state WHERE state.id = :need_id"
                ),
                {"need_id": ids.need_id},
            ).mappings().one()
        assert row.effective_state == "closed"
        assert row.nonterminal_count == 0
    finally:
        _delete_committed_aggregate(engine, ids)
        engine.dispose()


def test_two_different_outcomes_for_one_need_allow_exactly_one_command(database_url: str) -> None:
    from app.domain.outcomes import OutcomeConflict

    engine = create_engine(database_url)
    ids = _create_committed_aggregate(engine)
    barrier = Barrier(2)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def record(disposition: str, key: str) -> str:
        with session_factory() as session:
            barrier.wait(timeout=10)
            try:
                _record_outcome(
                    session,
                    organization_id=ids.organization_id,
                    need_id=ids.need_id,
                    recorder_id=ids.recorder_id,
                    key=key,
                    disposition=disposition,
                )
                session.commit()
                return "created"
            except OutcomeConflict:
                session.rollback()
                return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(record, "resolved", "different-outcome-one")
            second = executor.submit(record, "closed_unresolved", "different-outcome-two")
            assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
                "conflict",
                "created",
            ]
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM outcome WHERE reported_need_id = :need_id"),
                {"need_id": ids.need_id},
            ) == 1
    finally:
        _delete_committed_aggregate(engine, ids)
        engine.dispose()


def test_concurrent_identical_idempotency_keys_return_one_deterministic_outcome(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    ids = _create_committed_aggregate(engine)
    barrier = Barrier(2)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def record():
        with session_factory() as session:
            barrier.wait(timeout=10)
            result = _record_outcome(
                session,
                organization_id=ids.organization_id,
                need_id=ids.need_id,
                recorder_id=ids.recorder_id,
                key="identical-concurrent-key",
                disposition="resolved",
                note="Same command payload.",
            )
            session.commit()
            return result

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(record)
            second = executor.submit(record)
            first_result = first.result(timeout=20)
            second_result = second.result(timeout=20)
        assert first_result == second_result
        with engine.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM outcome "
                    "WHERE organization_id = :organization_id AND idempotency_key = :key"
                ),
                {"organization_id": ids.organization_id, "key": "identical-concurrent-key"},
            ) == 1
    finally:
        _delete_committed_aggregate(engine, ids)
        engine.dispose()
