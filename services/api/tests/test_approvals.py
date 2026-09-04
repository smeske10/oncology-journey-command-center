from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from test_safety_signals import _seed_signal

from app.auth.dependencies import current_actor
from app.auth.models import CurrentActor, Role
from app.config import settings
from app.db import models
from app.db.models import NavigationTask, PatientMessage, ReportedNeed, RoleAssignment, User
from app.db.session import get_session
from app.domain.enums import UserRole
from app.main import app


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
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for approval tests")
    engine = create_engine(settings.database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )()
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def _new_model(name: str) -> type[Any]:
    value = getattr(models, name, None)
    if value is None:
        pytest.fail(f"Task 4 model {name} is not implemented")
    return value


def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any],
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, json=json)

    return asyncio.run(send())


def _install_insert_failure(
    session: Session,
    *,
    table: str,
    sqlstate: str,
    message: str,
    constraint_name: str | None = None,
) -> None:
    assert table in {"approval_decision", "proposed_change", "safety_signal_resolution"}
    suffix = uuid4().hex
    function_name = f"test_raise_insert_{suffix}"
    trigger_name = f"aaa_test_raise_insert_{suffix}"
    escaped_message = message.replace("'", "''")
    constraint_option = (
        f", CONSTRAINT = '{constraint_name}'" if constraint_name is not None else ""
    )
    session.execute(
        text(
            f"""
            CREATE FUNCTION {function_name}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE = '{sqlstate}', MESSAGE = '{escaped_message}'
                    {constraint_option};
            END;
            $$
            """
        )
    )
    session.execute(
        text(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )
    )


def _actor(session: Session, *, user_id: Any, organization_id: Any) -> None:
    app.dependency_overrides[current_actor] = lambda: CurrentActor(
        user_id=user_id,
        organization_id=organization_id,
        role=Role.NAVIGATOR,
    )
    app.dependency_overrides[get_session] = lambda: session


def _add_navigator(
    session: Session,
    organization_id: Any,
    *,
    role: UserRole = UserRole.NAVIGATOR,
    granted_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> tuple[User, RoleAssignment]:
    user = User(email=f"approver-{uuid4()}@example.test", display_name="Approver")
    session.add(user)
    session.flush()
    assignment = RoleAssignment(
        organization_id=organization_id,
        user_id=user.id,
        role=role,
        granted_at=granted_at or datetime.now(UTC) - timedelta(hours=1),
        revoked_at=revoked_at,
    )
    session.add(assignment)
    session.flush()
    return user, assignment


def _policy(
    session: Session,
    organization_id: Any,
    *,
    change_type: str = "dismiss_signal",
    threshold: str | None = "urgent",
    required_count: int = 1,
    self_approval: bool = True,
    version: int = 7,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> Any:
    policy_model = _new_model("ApprovalPolicy")
    policy = policy_model(
        organization_id=organization_id,
        change_type=change_type,
        version=version,
        effective_from=effective_from or datetime.now(UTC) - timedelta(days=1),
        effective_to=effective_to,
        deterministic_severity_threshold=threshold,
        allow_self_approval=self_approval,
        required_approval_count=required_count,
        required_approver_role=UserRole.NAVIGATOR,
    )
    session.add(policy)
    session.flush()
    return policy


def _acknowledge(session: Session, context: dict[str, Any]) -> None:
    context["signal"].status = "acknowledged"
    context["signal"].acknowledged_by_user_id = context["navigator"].id
    context["signal"].acknowledged_at = datetime.now(UTC)
    session.flush()


def _proposal_payload(signal_id: Any, *, predecessor_id: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "change_type": "dismiss_signal",
        "safety_signal_id": str(signal_id),
        "navigation_task_id": None,
        "patient_message_id": None,
        "proposed_value": {"category": "false_positive"},
        "rationale": "Evidence does not support this duplicate signal.",
        "value_schema_id": "ojcc.dismiss-signal",
        "value_schema_version": 1,
    }
    if predecessor_id is not None:
        payload["supersedes_proposed_change_id"] = str(predecessor_id)
    return payload


VALUE_SCHEMA_CASES = (
    (
        "dismiss_signal",
        "ojcc.dismiss-signal",
        {"category": "false_positive"},
        {"category": "free-form"},
    ),
    (
        "override_signal_severity",
        "ojcc.override-signal-severity",
        {"level": "routine"},
        {"level": "routine", "note": "extra properties are forbidden"},
    ),
    (
        "authorize_navigation_task",
        "ojcc.authorize-navigation-task",
        {"title": "Call the transportation coordinator"},
        {},
    ),
    (
        "authorize_patient_message",
        "ojcc.authorize-patient-message",
        {"body": "Your navigator will call you today."},
        {"body": "Your navigator will call you today.", "send_now": True},
    ),
)
_UNSET = object()


def _target_payload(
    session: Session,
    context: dict[str, Any],
    change_type: str,
) -> dict[str, Any]:
    target = {
        "safety_signal_id": None,
        "navigation_task_id": None,
        "patient_message_id": None,
    }
    if change_type in {"dismiss_signal", "override_signal_severity"}:
        target["safety_signal_id"] = context["signal"].id
    elif change_type == "authorize_navigation_task":
        need = ReportedNeed(
            organization_id=context["organization"].id,
            patient_id=context["patient"].id,
            care_episode_id=context["episode"].id,
            source_submission_id=context["submission"].id,
            kind="transportation",
            status="open",
            evidence=[],
        )
        session.add(need)
        session.flush()
        task = NavigationTask(
            organization_id=context["organization"].id,
            patient_id=context["patient"].id,
            reported_need_id=need.id,
            title="Call the transportation coordinator",
            status="open",
        )
        session.add(task)
        session.flush()
        target["navigation_task_id"] = task.id
    else:
        message = PatientMessage(
            organization_id=context["organization"].id,
            patient_id=context["patient"].id,
            body="Your navigator will call you today.",
        )
        session.add(message)
        session.flush()
        target["patient_message_id"] = message.id
    return target


def _direct_proposal(
    context: dict[str, Any],
    policy: Any,
    *,
    change_type: str,
    schema_id: str,
    value: dict[str, Any],
    target: dict[str, Any],
    threshold_snapshot: Any = _UNSET,
    self_approval_snapshot: bool | None = None,
    count_snapshot: int | None = None,
    role_snapshot: UserRole | None = None,
) -> Any:
    proposal_model = _new_model("ProposedChange")
    return proposal_model(
        organization_id=context["organization"].id,
        proposed_by_user_id=context["navigator"].id,
        proposed_at=datetime.now(UTC),
        change_type=change_type,
        proposed_value=value,
        rationale="Versioned proposal value",
        value_schema_id=schema_id,
        value_schema_version=1,
        safety_signal_id=target["safety_signal_id"],
        navigation_task_id=target["navigation_task_id"],
        patient_message_id=target["patient_message_id"],
        approval_policy_id=policy.id,
        approval_policy_version=policy.version,
        deterministic_severity_threshold_snapshot=(
            policy.deterministic_severity_threshold
            if threshold_snapshot is _UNSET
            else threshold_snapshot
        ),
        allow_self_approval_snapshot=(
            policy.allow_self_approval
            if self_approval_snapshot is None
            else self_approval_snapshot
        ),
        required_approval_count_snapshot=(
            policy.required_approval_count if count_snapshot is None else count_snapshot
        ),
        required_approver_role_snapshot=(
            policy.required_approver_role if role_snapshot is None else role_snapshot
        ),
    )


def _approved_non_safety_proposal(
    session: Session,
    *,
    change_type: str,
    schema_id: str,
    value: dict[str, Any],
) -> tuple[RoleAssignment, datetime, Any]:
    context = _seed_signal(session)
    policy = _policy(
        session,
        context["organization"].id,
        change_type=change_type,
        threshold=None,
        self_approval=False,
    )
    approver, assignment = _add_navigator(session, context["organization"].id)
    proposal = _direct_proposal(
        context,
        policy,
        change_type=change_type,
        schema_id=schema_id,
        value=value,
        target=_target_payload(session, context, change_type),
    )
    session.add(proposal)
    session.flush()
    authorized_at = datetime.now(UTC)
    decision_model = _new_model("ApprovalDecision")
    session.add(
        decision_model(
            organization_id=context["organization"].id,
            proposed_change_id=proposal.id,
            authorized_by_user_id=approver.id,
            qualifying_role_assignment_id=assignment.id,
            qualifying_role_snapshot=UserRole.NAVIGATOR,
            decision="approved",
            authorized_at=authorized_at,
        )
    )
    session.flush()
    assert session.scalar(
        text("SELECT effective_state FROM effective_proposed_change_state WHERE id = :id"),
        {"id": proposal.id},
    ) == "approved"
    return assignment, authorized_at, proposal.id


def _pending_dismissal_for_decision_failure(
    session: Session,
) -> tuple[dict[str, Any], User, RoleAssignment, str]:
    context = _seed_signal(session, deterministic_level="routine")
    _policy(session, context["organization"].id, self_approval=False)
    _acknowledge(session, context)
    approver, assignment = _add_navigator(session, context["organization"].id)
    _actor(
        session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=_proposal_payload(context["signal"].id),
    )
    assert proposal.status_code == 201, proposal.text
    _actor(
        session,
        user_id=approver.id,
        organization_id=context["organization"].id,
    )
    return context, approver, assignment, proposal.json()["id"]


def _proposal_insert_failure_context(session: Session) -> dict[str, Any]:
    context = _seed_signal(session, deterministic_level="routine")
    _policy(session, context["organization"].id)
    _actor(
        session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    return context


def test_approval_domain_requires_exactly_one_matching_target() -> None:
    try:
        approvals = import_module("app.domain.approvals")
    except ModuleNotFoundError:
        pytest.fail("Task 4 approvals domain is not implemented")

    signal_id = uuid4()
    approvals.validate_target_shape(
        change_type="dismiss_signal",
        safety_signal_id=signal_id,
        navigation_task_id=None,
        patient_message_id=None,
    )
    with pytest.raises(ValueError, match="exactly one"):
        approvals.validate_target_shape(
            change_type="dismiss_signal",
            safety_signal_id=signal_id,
            navigation_task_id=uuid4(),
            patient_message_id=None,
        )
    with pytest.raises(ValueError, match="target"):
        approvals.validate_target_shape(
            change_type="authorize_navigation_task",
            safety_signal_id=signal_id,
            navigation_task_id=None,
            patient_message_id=None,
        )


def test_proposal_route_snapshots_server_policy_and_rejects_target_and_snapshot_forgery(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session, deterministic_level="emergent")
    policy = _policy(db_session, context["organization"].id)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    payload = _proposal_payload(context["signal"].id)
    payload["organization_id"] = str(uuid4())
    payload["proposed_by_user_id"] = str(uuid4())
    payload["proposed_at"] = datetime(2000, 1, 1, tzinfo=UTC).isoformat()
    payload["required_approval_count_snapshot"] = 99
    payload["deterministic_severity_threshold_snapshot"] = None
    forged = _request("POST", "/v1/navigator/proposed-changes", json=payload)
    assert forged.status_code == 422

    clean_payload = _proposal_payload(context["signal"].id)
    response = _request("POST", "/v1/navigator/proposed-changes", json=clean_payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_id"] == str(context["organization"].id)
    assert body["proposed_by_user_id"] == str(context["navigator"].id)
    assert body["approval_policy_id"] == str(policy.id)
    assert body["approval_policy_version"] == 7
    assert body["required_approval_count_snapshot"] == 1
    assert body["allow_self_approval_snapshot"] is True
    assert body["required_approver_role_snapshot"] == "navigator"
    assert body["deterministic_severity_threshold_snapshot"] == "urgent"
    assert body["state"] == "pending"

    two_targets = _proposal_payload(context["signal"].id)
    two_targets["navigation_task_id"] = str(uuid4())
    assert (
        _request("POST", "/v1/navigator/proposed-changes", json=two_targets).status_code
        == 422
    )


@pytest.mark.parametrize(
    "diagnostic",
    [
        "Proposal must reference the canonical effective policy with matching snapshots",
        "Only a pending or declined current proposal can be revised",
    ],
)
def test_proposal_route_preserves_known_database_trigger_diagnostic(
    db_session: Session,
    diagnostic: str,
) -> None:
    context = _proposal_insert_failure_context(db_session)
    _install_insert_failure(
        db_session,
        table="proposed_change",
        sqlstate="P0001",
        message=diagnostic,
    )

    response = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=_proposal_payload(context["signal"].id),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": diagnostic}


def test_proposal_route_maps_recognized_database_constraint_conflict(
    db_session: Session,
) -> None:
    context = _proposal_insert_failure_context(db_session)
    _install_insert_failure(
        db_session,
        table="proposed_change",
        sqlstate="23505",
        message="duplicate key value exposes database internals",
        constraint_name="uq_proposed_change_supersedes_proposed_change_id",
    )

    response = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=_proposal_payload(context["signal"].id),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Proposal predecessor already has a revision"}


@pytest.mark.parametrize("sqlstate", ["40001", "08006", "P0001"])
def test_proposal_route_sanitizes_unexpected_database_failures(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    sqlstate: str,
) -> None:
    context = _proposal_insert_failure_context(db_session)
    leaked_text = "database password local-synthetic-only"
    _install_insert_failure(
        db_session,
        table="proposed_change",
        sqlstate=sqlstate,
        message=leaked_text,
    )

    response = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=_proposal_payload(context["signal"].id),
        raise_app_exceptions=False,
    )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert leaked_text not in response.text
    assert "Unexpected database error while creating a proposal" in caplog.text


def test_below_threshold_dismissal_uses_snapshotted_self_approval_policy(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session, deterministic_level="routine")
    policy = _policy(db_session, context["organization"].id)
    _acknowledge(db_session, context)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal_response = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    )
    assert proposal_response.status_code == 201, proposal_response.text
    proposal_id = proposal_response.json()["id"]

    with pytest.raises(DBAPIError, match="Approval policy versions are immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "UPDATE approval_policy SET required_approval_count = 2, "
                    "allow_self_approval = false WHERE id = :policy_id"
                ),
                {"policy_id": policy.id},
            )
    decision = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(context["role"].id),
            "reason": None,
        },
    )

    assert decision.status_code == 201, decision.text
    assert decision.json()["proposal_state"] == "approved"
    assert decision.json()["applied"] is True
    db_session.refresh(context["signal"])
    assert str(context["signal"].dismissal_proposed_change_id) == proposal_id
    state = db_session.execute(
        text("SELECT effective_state FROM effective_safety_signal_state WHERE id = :id"),
        {"id": context["signal"].id},
    ).scalar_one()
    assert state == "dismissed"


def test_high_risk_dismissal_requires_two_nonproposer_humans_and_immutable_baseline(
    db_session: Session,
) -> None:
    context = _seed_signal(
        db_session,
        deterministic_level="urgent",
        effective_level="emergent",
    )
    _policy(db_session, context["organization"].id, required_count=1, self_approval=True)
    _acknowledge(db_session, context)
    first_approver, first_role = _add_navigator(db_session, context["organization"].id)
    second_approver, second_role = _add_navigator(db_session, context["organization"].id)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal_response = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    )
    proposal_id = proposal_response.json()["id"]

    proposer_decision = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(context["role"].id),
            "reason": None,
        },
    )
    assert proposer_decision.status_code == 403

    _actor(db_session, user_id=first_approver.id, organization_id=context["organization"].id)
    first = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(first_role.id),
            "reason": None,
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["proposal_state"] == "pending"
    assert first.json()["applied"] is False

    _actor(db_session, user_id=second_approver.id, organization_id=context["organization"].id)
    second = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(second_role.id),
            "reason": "Independent review complete.",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["proposal_state"] == "approved"
    assert second.json()["applied"] is True


@pytest.mark.parametrize(
    ("assignment_case", "expected_status"),
    [
        ("cross_organization", 403),
        ("revoked", 403),
        ("not_yet_granted", 403),
        ("wrong_role", 403),
    ],
)
def test_decision_revalidates_role_tenant_interval_and_name(
    db_session: Session,
    assignment_case: str,
    expected_status: int,
) -> None:
    context = _seed_signal(db_session, deterministic_level="routine")
    _policy(db_session, context["organization"].id, self_approval=False)
    _acknowledge(db_session, context)
    approver, role = _add_navigator(db_session, context["organization"].id)
    if assignment_case == "cross_organization":
        other = models.Organization(name=f"Other approval organization {uuid4()}")
        db_session.add(other)
        db_session.flush()
        role.organization_id = other.id
    elif assignment_case == "revoked":
        role.revoked_at = datetime.now(UTC) - timedelta(minutes=1)
    elif assignment_case == "not_yet_granted":
        role.granted_at = datetime.now(UTC) + timedelta(hours=1)
    else:
        role.role = UserRole.ADMINISTRATOR
    db_session.flush()

    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    ).json()
    _actor(db_session, user_id=approver.id, organization_id=context["organization"].id)
    response = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal['id']}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(role.id),
            "reason": None,
        },
    )
    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("diagnostic", "expected_status"),
    [
        ("Role assignment does not qualify for this proposal", 403),
        ("Approval decisions require a current pending proposal", 409),
    ],
)
def test_decision_route_preserves_known_database_trigger_diagnostics(
    db_session: Session,
    diagnostic: str,
    expected_status: int,
) -> None:
    _, _, assignment, proposal_id = _pending_dismissal_for_decision_failure(db_session)
    _install_insert_failure(
        db_session,
        table="approval_decision",
        sqlstate="P0001",
        message=diagnostic,
    )

    response = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(assignment.id),
            "reason": None,
        },
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": diagnostic}


@pytest.mark.parametrize("sqlstate", ["40001", "P0001"])
def test_decision_route_sanitizes_unexpected_database_failures(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    sqlstate: str,
) -> None:
    _, _, assignment, proposal_id = _pending_dismissal_for_decision_failure(db_session)
    leaked_text = "database password local-synthetic-only"
    _install_insert_failure(
        db_session,
        table="approval_decision",
        sqlstate=sqlstate,
        message=leaked_text,
    )

    response = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(assignment.id),
            "reason": None,
        },
        raise_app_exceptions=False,
    )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert leaked_text not in response.text
    assert "Unexpected database error while recording an approval decision" in caplog.text


@pytest.mark.parametrize(
    ("change_type", "schema_id", "value"),
    [
        pytest.param(
            "authorize_navigation_task",
            "ojcc.authorize-navigation-task",
            {"title": "Call the transportation coordinator"},
            id="navigation-task",
        ),
        pytest.param(
            "authorize_patient_message",
            "ojcc.authorize-patient-message",
            {"body": "Your navigator will call you today."},
            id="patient-message",
        ),
    ],
)
@pytest.mark.parametrize("mutation", ["role_update", "delete", "backdated_revocation"])
def test_approved_non_safety_proposal_rejects_invalidating_role_history_mutation(
    db_session: Session,
    change_type: str,
    schema_id: str,
    value: dict[str, Any],
    mutation: str,
) -> None:
    assignment, authorized_at, _ = _approved_non_safety_proposal(
        db_session,
        change_type=change_type,
        schema_id=schema_id,
        value=value,
    )

    with pytest.raises(
        DBAPIError,
        match="Role assignment mutation would invalidate approval history",
    ):
        with db_session.begin_nested():
            if mutation == "role_update":
                assignment.role = UserRole.ADMINISTRATOR
            elif mutation == "delete":
                db_session.delete(assignment)
            else:
                assignment.revoked_at = authorized_at
            db_session.flush()


@pytest.mark.parametrize(
    ("change_type", "schema_id", "value"),
    [
        pytest.param(
            "authorize_navigation_task",
            "ojcc.authorize-navigation-task",
            {"title": "Call the transportation coordinator"},
            id="navigation-task",
        ),
        pytest.param(
            "authorize_patient_message",
            "ojcc.authorize-patient-message",
            {"body": "Your navigator will call you today."},
            id="patient-message",
        ),
    ],
)
def test_approved_non_safety_proposal_permits_revocation_after_authorization(
    db_session: Session,
    change_type: str,
    schema_id: str,
    value: dict[str, Any],
) -> None:
    assignment, authorized_at, proposal_id = _approved_non_safety_proposal(
        db_session,
        change_type=change_type,
        schema_id=schema_id,
        value=value,
    )

    assignment.revoked_at = authorized_at + timedelta(microseconds=1)
    db_session.flush()

    assert db_session.scalar(
        text("SELECT effective_state FROM effective_proposed_change_state WHERE id = :id"),
        {"id": proposal_id},
    ) == "approved"


def test_regrant_uses_the_new_assignment_and_duplicate_human_cannot_count_twice(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session, deterministic_level="urgent")
    _policy(db_session, context["organization"].id)
    _acknowledge(db_session, context)
    approver, old_role = _add_navigator(
        db_session,
        context["organization"].id,
        revoked_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    _, new_role = approver, RoleAssignment(
        organization_id=context["organization"].id,
        user_id=approver.id,
        role=UserRole.NAVIGATOR,
        granted_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    second_approver, second_role = _add_navigator(db_session, context["organization"].id)
    db_session.add(new_role)
    db_session.flush()
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal_id = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    ).json()["id"]
    _actor(db_session, user_id=approver.id, organization_id=context["organization"].id)
    assert (
        _request(
            "POST",
            f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
            json={
                "decision": "approved",
                "qualifying_role_assignment_id": str(old_role.id),
                "reason": None,
            },
        ).status_code
        == 403
    )
    accepted = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(new_role.id),
            "reason": None,
        },
    )
    assert accepted.status_code == 201, accepted.text
    duplicate = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(new_role.id),
            "reason": "Replay",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "detail": "A decision by this authorizer already exists for this proposal"
    }
    _actor(db_session, user_id=second_approver.id, organization_id=context["organization"].id)
    assert (
        _request(
            "POST",
            f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
            json={
                "decision": "approved",
                "qualifying_role_assignment_id": str(second_role.id),
                "reason": None,
            },
        ).status_code
        == 201
    )


def test_decline_is_terminal_and_declined_proposal_can_be_revised(db_session: Session) -> None:
    context = _seed_signal(db_session, deterministic_level="routine")
    _policy(db_session, context["organization"].id, self_approval=False)
    approver, role = _add_navigator(db_session, context["organization"].id)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal_id = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    ).json()["id"]
    _actor(db_session, user_id=approver.id, organization_id=context["organization"].id)
    missing_reason = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "declined",
            "qualifying_role_assignment_id": str(role.id),
            "reason": None,
        },
    )
    assert missing_reason.status_code == 422
    declined = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "declined",
            "qualifying_role_assignment_id": str(role.id),
            "reason": "Rationale is insufficient.",
        },
    )
    assert declined.status_code == 201, declined.text
    assert declined.json()["proposal_state"] == "declined"
    assert (
        _request(
            "POST",
            f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
            json={
                "decision": "approved",
                "qualifying_role_assignment_id": str(role.id),
                "reason": None,
            },
        ).status_code
        == 409
    )

    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    successor = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=_proposal_payload(context["signal"].id, predecessor_id=proposal_id),
    )
    assert successor.status_code == 201, successor.text
    old_state = db_session.execute(
        text("SELECT effective_state FROM effective_proposed_change_state WHERE id = :id"),
        {"id": proposal_id},
    ).scalar_one()
    assert old_state == "superseded"


def test_approved_severity_override_is_the_only_path_below_deterministic_level(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session, deterministic_level="urgent")
    _policy(
        db_session,
        context["organization"].id,
        change_type="override_signal_severity",
        threshold=None,
        self_approval=True,
    )
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    payload = _proposal_payload(context["signal"].id)
    payload.update(
        {
            "change_type": "override_signal_severity",
            "proposed_value": {"level": "routine"},
            "value_schema_id": "ojcc.override-signal-severity",
        }
    )
    proposal = _request("POST", "/v1/navigator/proposed-changes", json=payload)
    assert proposal.status_code == 201, proposal.text
    decision = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal.json()['id']}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(context["role"].id),
            "reason": "Human review supports override.",
        },
    )
    assert decision.status_code == 201, decision.text
    db_session.refresh(context["signal"])
    assert context["signal"].deterministic_level.value == "urgent"
    assert context["signal"].effective_level.value == "routine"
    assert str(context["signal"].current_severity_override_proposed_change_id) == proposal.json()[
        "id"
    ]


def test_final_approval_requalifies_every_stored_decision_before_application(
    db_session: Session,
) -> None:
    """Changing an earlier role must prevent its stale approval from applying dismissal."""
    context = _seed_signal(db_session, deterministic_level="urgent")
    _policy(db_session, context["organization"].id)
    _acknowledge(db_session, context)
    first_approver, first_role = _add_navigator(db_session, context["organization"].id)
    second_approver, second_role = _add_navigator(db_session, context["organization"].id)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal_id = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    ).json()["id"]

    _actor(db_session, user_id=first_approver.id, organization_id=context["organization"].id)
    first = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(first_role.id),
            "reason": None,
        },
    )
    assert first.status_code == 201
    assert first.json()["proposal_state"] == "pending"

    db_session.execute(text("SET session_replication_role = replica"))
    db_session.execute(
        text("UPDATE role_assignment SET role = 'administrator' WHERE id = :role_id"),
        {"role_id": first_role.id},
    )
    db_session.execute(text("SET session_replication_role = origin"))
    _actor(db_session, user_id=second_approver.id, organization_id=context["organization"].id)
    second = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "approved",
            "qualifying_role_assignment_id": str(second_role.id),
            "reason": None,
        },
    )

    assert second.status_code == 201, second.text
    assert second.json()["proposal_state"] == "pending"
    assert second.json()["applied"] is False
    db_session.refresh(context["signal"])
    assert context["signal"].dismissal_proposed_change_id is None


def test_no_longer_qualifying_decline_does_not_remain_terminal(db_session: Session) -> None:
    """A decline whose exact assignment no longer qualifies must derive as pending."""
    context = _seed_signal(db_session, deterministic_level="routine")
    _policy(db_session, context["organization"].id, self_approval=False)
    approver, role = _add_navigator(db_session, context["organization"].id)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    proposal_id = _request(
        "POST", "/v1/navigator/proposed-changes", json=_proposal_payload(context["signal"].id)
    ).json()["id"]
    _actor(db_session, user_id=approver.id, organization_id=context["organization"].id)
    declined = _request(
        "POST",
        f"/v1/navigator/proposed-changes/{proposal_id}/decisions",
        json={
            "decision": "declined",
            "qualifying_role_assignment_id": str(role.id),
            "reason": "The evidence is incomplete.",
        },
    )
    assert declined.status_code == 201
    assert declined.json()["proposal_state"] == "declined"

    db_session.execute(text("SET session_replication_role = replica"))
    db_session.execute(
        text("UPDATE role_assignment SET role = 'administrator' WHERE id = :role_id"),
        {"role_id": role.id},
    )
    db_session.execute(text("SET session_replication_role = origin"))
    state = db_session.scalar(
        text(
            "SELECT effective_state FROM effective_proposed_change_state "
            "WHERE id = :proposal_id"
        ),
        {"proposal_id": proposal_id},
    )

    assert state == "pending"


def test_dismissal_policy_rejects_a_null_deterministic_threshold(db_session: Session) -> None:
    """A NULL threshold must never turn an emergent dismissal into low risk."""
    context = _seed_signal(db_session, deterministic_level="emergent")
    with pytest.raises(DBAPIError, match="dismissal.*threshold|threshold.*dismissal"):
        with db_session.begin_nested():
            _policy(db_session, context["organization"].id, threshold=None)


def test_approval_policy_effective_intervals_cannot_overlap(db_session: Session) -> None:
    context = _seed_signal(db_session)
    now = datetime.now(UTC)
    _policy(
        db_session,
        context["organization"].id,
        version=7,
        effective_from=now - timedelta(days=2),
        effective_to=now + timedelta(days=1),
    )

    with pytest.raises(DBAPIError, match="ex_approval_policy_no_overlap|conflicting key"):
        with db_session.begin_nested():
            _policy(
                db_session,
                context["organization"].id,
                version=8,
                effective_from=now - timedelta(days=1),
            )


def test_database_rejects_an_older_overlapping_policy_as_noncanonical(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session)
    now = datetime.now(UTC)
    overlap_constraint = "ex_approval_policy_no_overlap"
    if db_session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conrelid = 'approval_policy'::regclass AND conname = :name)"
        ),
        {"name": overlap_constraint},
    ):
        db_session.execute(text(f"SET CONSTRAINTS {overlap_constraint} DEFERRED"))
    older_policy = _policy(
        db_session,
        context["organization"].id,
        version=7,
        effective_from=now - timedelta(days=2),
        required_count=1,
        self_approval=True,
    )
    newer_policy = _policy(
        db_session,
        context["organization"].id,
        version=8,
        effective_from=now - timedelta(days=1),
        required_count=2,
        self_approval=False,
    )
    proposal = _direct_proposal(
        context,
        older_policy,
        change_type="dismiss_signal",
        schema_id="ojcc.dismiss-signal",
        value={"category": "false_positive"},
        target=_target_payload(db_session, context, "dismiss_signal"),
    )

    with pytest.raises(DBAPIError, match="canonical.*policy|policy.*canonical"):
        with db_session.begin_nested():
            db_session.add(proposal)
            db_session.flush()

    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    canonical = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=_proposal_payload(context["signal"].id),
    )
    assert canonical.status_code == 201, canonical.text
    assert canonical.json()["approval_policy_id"] == str(newer_policy.id)
    assert canonical.json()["approval_policy_version"] == 8
    assert canonical.json()["required_approval_count_snapshot"] == 2
    assert canonical.json()["allow_self_approval_snapshot"] is False


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_approval_policy_version_cannot_be_mutated_or_deleted(
    db_session: Session,
    mutation: str,
) -> None:
    """A policy version referenced by historical snapshots must stay stable."""
    context = _seed_signal(db_session)
    policy = _policy(db_session, context["organization"].id)

    with pytest.raises(DBAPIError, match="Approval policy versions are immutable"):
        with db_session.begin_nested():
            if mutation == "update":
                db_session.execute(
                    text(
                        "UPDATE approval_policy SET required_approval_count = 9 "
                        "WHERE id = :policy_id"
                    ),
                    {"policy_id": policy.id},
                )
            else:
                db_session.execute(
                    text("DELETE FROM approval_policy WHERE id = :policy_id"),
                    {"policy_id": policy.id},
                )


@pytest.mark.parametrize(
    "forgery",
    [
        "policy_change_type",
        "effective_interval",
        "threshold_snapshot",
        "null_threshold_snapshot",
        "self_approval_snapshot",
        "approval_count_snapshot",
        "approver_role_snapshot",
    ],
)
def test_database_binds_every_proposal_snapshot_to_the_exact_effective_policy(
    db_session: Session,
    forgery: str,
) -> None:
    """A proposal must not become an authorization unit with forged policy facts."""
    context = _seed_signal(db_session, deterministic_level="urgent")
    now = datetime.now(UTC)
    policy_model = _new_model("ApprovalPolicy")
    policy_change_type = (
        "override_signal_severity" if forgery == "policy_change_type" else "dismiss_signal"
    )
    policy = policy_model(
        organization_id=context["organization"].id,
        change_type=policy_change_type,
        version=11,
        effective_from=now - timedelta(days=2),
        effective_to=(now - timedelta(days=1) if forgery == "effective_interval" else None),
        deterministic_severity_threshold=(
            None if policy_change_type != "dismiss_signal" else "urgent"
        ),
        allow_self_approval=True,
        required_approval_count=1,
        required_approver_role=UserRole.NAVIGATOR,
    )
    db_session.add(policy)
    db_session.flush()
    target = _target_payload(db_session, context, "dismiss_signal")
    proposal = _direct_proposal(
        context,
        policy,
        change_type="dismiss_signal",
        schema_id="ojcc.dismiss-signal",
        value={"category": "false_positive"},
        target=target,
        threshold_snapshot=(
            "emergent"
            if forgery == "threshold_snapshot"
            else None
            if forgery in {"null_threshold_snapshot", "policy_change_type"}
            else _UNSET
        ),
        self_approval_snapshot=(False if forgery == "self_approval_snapshot" else None),
        count_snapshot=(2 if forgery == "approval_count_snapshot" else None),
        role_snapshot=(
            UserRole.ADMINISTRATOR if forgery == "approver_role_snapshot" else None
        ),
    )

    with pytest.raises(DBAPIError, match="policy|snapshot|effective"):
        with db_session.begin_nested():
            db_session.add(proposal)
            db_session.flush()


@pytest.mark.parametrize(
    ("change_type", "schema_id", "valid_value", "invalid_value"),
    VALUE_SCHEMA_CASES,
)
def test_route_validates_complete_value_against_the_registered_schema(
    db_session: Session,
    change_type: str,
    schema_id: str,
    valid_value: dict[str, Any],
    invalid_value: dict[str, Any],
) -> None:
    """Removing required fields or adding forbidden values must fail at the HTTP boundary."""
    context = _seed_signal(db_session, deterministic_level="urgent")
    _policy(
        db_session,
        context["organization"].id,
        change_type=change_type,
        threshold="urgent" if change_type == "dismiss_signal" else None,
    )
    target = _target_payload(db_session, context, change_type)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    base_payload = {
        "change_type": change_type,
        **{key: str(value) if value is not None else None for key, value in target.items()},
        "rationale": "Validate the versioned value",
        "value_schema_id": schema_id,
        "value_schema_version": 1,
    }

    valid = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=base_payload | {"proposed_value": valid_value},
    )
    invalid = _request(
        "POST",
        "/v1/navigator/proposed-changes",
        json=base_payload | {"proposed_value": invalid_value},
    )

    assert valid.status_code == 201, valid.text
    assert invalid.status_code == 422, invalid.text


@pytest.mark.parametrize(
    ("change_type", "schema_id", "valid_value", "invalid_value"),
    VALUE_SCHEMA_CASES,
)
def test_database_validates_complete_value_against_the_registered_schema(
    db_session: Session,
    change_type: str,
    schema_id: str,
    valid_value: dict[str, Any],
    invalid_value: dict[str, Any],
) -> None:
    """Direct writers must not bypass versioned proposal-value validation."""
    context = _seed_signal(db_session, deterministic_level="urgent")
    policy = _policy(
        db_session,
        context["organization"].id,
        change_type=change_type,
        threshold="urgent" if change_type == "dismiss_signal" else None,
    )
    target = _target_payload(db_session, context, change_type)
    valid = _direct_proposal(
        context,
        policy,
        change_type=change_type,
        schema_id=schema_id,
        value=valid_value,
        target=target,
    )
    db_session.add(valid)
    db_session.flush()

    with pytest.raises(DBAPIError, match="schema|value"):
        with db_session.begin_nested():
            db_session.add(
                _direct_proposal(
                    context,
                    policy,
                    change_type=change_type,
                    schema_id=schema_id,
                    value=invalid_value,
                    target=target,
                )
            )
            db_session.flush()


@pytest.mark.parametrize(
    ("schema_id", "schema_version"),
    [("ojcc.unknown", 1), ("ojcc.override-signal-severity", 1), ("ojcc.dismiss-signal", 99)],
)
def test_route_rejects_unknown_mismatched_or_unregistered_schema_identity(
    db_session: Session,
    schema_id: str,
    schema_version: int,
) -> None:
    """Changing only the claimed schema identity must not reinterpret the same JSON value."""
    context = _seed_signal(db_session)
    _policy(db_session, context["organization"].id)
    _actor(
        db_session,
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
    )
    payload = _proposal_payload(context["signal"].id)
    payload["value_schema_id"] = schema_id
    payload["value_schema_version"] = schema_version

    response = _request("POST", "/v1/navigator/proposed-changes", json=payload)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    ("schema_id", "schema_version"),
    [("ojcc.unknown", 1), ("ojcc.override-signal-severity", 1), ("ojcc.dismiss-signal", 99)],
)
def test_database_rejects_unknown_mismatched_or_unregistered_schema_identity(
    db_session: Session,
    schema_id: str,
    schema_version: int,
) -> None:
    """The database registry key must bind change type, schema identity, and version."""
    context = _seed_signal(db_session)
    policy = _policy(db_session, context["organization"].id)
    target = _target_payload(db_session, context, "dismiss_signal")
    proposal = _direct_proposal(
        context,
        policy,
        change_type="dismiss_signal",
        schema_id=schema_id,
        value={"category": "false_positive"},
        target=target,
    )
    proposal.value_schema_version = schema_version

    with pytest.raises(DBAPIError, match="schema|foreign key"):
        with db_session.begin_nested():
            db_session.add(proposal)
            db_session.flush()


def test_database_rejects_nonpositive_value_schema_version(db_session: Session) -> None:
    """Direct proposal inserts must preserve positive version semantics from the route."""
    context = _seed_signal(db_session)
    policy = _policy(db_session, context["organization"].id)
    proposal = _direct_proposal(
        context,
        policy,
        change_type="dismiss_signal",
        schema_id="ojcc.dismiss-signal",
        value={"category": "false_positive"},
        target=_target_payload(db_session, context, "dismiss_signal"),
    )
    proposal.value_schema_version = 0

    with pytest.raises(DBAPIError, match="value_schema_version|check constraint"):
        with db_session.begin_nested():
            db_session.add(proposal)
            db_session.flush()


def test_database_rejects_agent_dismissal_proposal(db_session: Session) -> None:
    context = _seed_signal(db_session)
    policy = _policy(db_session, context["organization"].id)
    proposed_change_model = _new_model("ProposedChange")
    agent = models.AgentRun(
        organization_id=context["organization"].id,
        patient_id=context["patient"].id,
        source_submission_id=context["submission"].id,
        trace_id=f"trace-{uuid4()}",
        agent_name="triage",
        status="succeeded",
    )
    db_session.add(agent)
    db_session.flush()
    with pytest.raises(DBAPIError, match="Agents cannot propose dismissal"):
        with db_session.begin_nested():
            db_session.add(
                proposed_change_model(
                    organization_id=context["organization"].id,
                    proposed_by_agent_run_id=agent.id,
                    change_type="dismiss_signal",
                    proposed_value={"category": "false_positive"},
                    rationale="Agent dismissal is prohibited.",
                    value_schema_id="ojcc.dismiss-signal",
                    value_schema_version=1,
                    safety_signal_id=context["signal"].id,
                    approval_policy_id=policy.id,
                    approval_policy_version=7,
                    deterministic_severity_threshold_snapshot="urgent",
                    allow_self_approval_snapshot=False,
                    required_approval_count_snapshot=2,
                    required_approver_role_snapshot=UserRole.NAVIGATOR,
                )
            )
            db_session.flush()
