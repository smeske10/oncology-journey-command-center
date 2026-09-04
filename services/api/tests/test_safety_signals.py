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

from app.auth.dependencies import current_actor, get_current_demo_session_service
from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService
from app.config import settings
from app.db import models
from app.db.models import (
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    Organization,
    PathwayDefinition,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from app.db.session import get_session
from app.domain.enums import CheckInStatus, SubmissionSource, UserRole
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
        pytest.skip("PostgreSQL DATABASE_URL is not reachable for safety-signal tests")
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


def _seed_signal(
    session: Session,
    *,
    deterministic_level: str = "urgent",
    effective_level: str | None = None,
) -> dict[str, Any]:
    signal_rule_model = _new_model("SignalRule")
    safety_signal_model = _new_model("SafetySignal")
    now = datetime.now(UTC)
    organization = Organization(name=f"Safety organization {uuid4()}")
    navigator = User(email=f"navigator-{uuid4()}@example.test", display_name="Navigator")
    patient_author = User(email=f"patient-{uuid4()}@example.test", display_name="Patient")
    session.add_all([organization, navigator, patient_author])
    session.flush()
    role = RoleAssignment(
        organization_id=organization.id,
        user_id=navigator.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=1),
    )
    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"safety-{uuid4()}",
        display_name="Synthetic safety patient",
    )
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug=f"safety-{uuid4()}",
        version=1,
        name="Safety pathway",
    )
    session.add_all([role, patient, pathway])
    session.flush()
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=patient.id,
        status="active",
    )
    definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition_id=pathway.id,
        slug=f"safety-check-in-{uuid4()}",
        version=1,
        title="Safety check-in",
    )
    session.add_all([episode, definition])
    session.flush()
    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers={"urgent_language": True},
        submission_source=SubmissionSource.PATIENT,
        submitted_by_user_id=patient_author.id,
        submitted_at=now,
    )
    rule = signal_rule_model(
        organization_id=organization.id,
        rule_code=f"urgent-language-{uuid4()}",
        version=3,
        rule_kind="deterministic",
        name="Urgent language",
    )
    session.add_all([submission, rule])
    session.flush()
    signal = safety_signal_model(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=submission.id,
        signal_rule_id=rule.id,
        signal_rule_version=rule.version,
        deterministic_level=deterministic_level,
        effective_level=effective_level or deterministic_level,
        status="open",
        evidence=[{"field": "urgent_language", "text": "help now"}],
    )
    session.add(signal)
    session.flush()
    return {
        "organization": organization,
        "navigator": navigator,
        "role": role,
        "patient": patient,
        "episode": episode,
        "submission": submission,
        "rule": rule,
        "signal": signal,
    }


def _request(method: str, path: str, *, json: dict[str, Any] | None = None, **kwargs: Any):
    cookies = kwargs.pop("cookies", None)
    raise_app_exceptions = kwargs.pop("raise_app_exceptions", True)

    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies=cookies,
        ) as client:
            return await client.request(method, path, json=json, **kwargs)

    return asyncio.run(send())


def _install_resolution_insert_failure(
    session: Session,
    *,
    sqlstate: str,
    message: str,
) -> None:
    suffix = uuid4().hex
    function_name = f"test_raise_resolution_insert_{suffix}"
    trigger_name = f"aaa_test_raise_resolution_insert_{suffix}"
    escaped_message = message.replace("'", "''")
    session.execute(
        text(
            f"""
            CREATE FUNCTION {function_name}() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE = '{sqlstate}', MESSAGE = '{escaped_message}';
            END;
            $$
            """
        )
    )
    session.execute(
        text(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON safety_signal_resolution "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )
    )


def _override_actor(session: Session, context: dict[str, Any]) -> None:
    app.dependency_overrides[current_actor] = lambda: CurrentActor(
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
        role=Role.NAVIGATOR,
    )
    app.dependency_overrides[get_session] = lambda: session


def test_severity_helpers_preserve_the_deterministic_floor() -> None:
    try:
        safety = import_module("app.domain.safety")
    except ModuleNotFoundError:
        pytest.fail("Task 4 safety domain is not implemented")

    assert safety.severity_rank("routine") < safety.severity_rank("urgent")
    assert safety.severity_rank("urgent") < safety.severity_rank("emergent")
    assert safety.validate_automated_severity("urgent", "emergent") == "emergent"
    with pytest.raises(ValueError, match="deterministic"):
        safety.validate_automated_severity("urgent", "routine")


def test_acknowledgement_route_uses_authenticated_actor_and_server_time_and_rejects_replay(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session)
    _override_actor(db_session, context)
    signal_id = context["signal"].id
    before = datetime.now(UTC)

    response = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
        json={},
    )
    after = datetime.now(UTC)
    forged = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
        json={"acknowledged_by_user_id": str(uuid4()), "acknowledged_at": before.isoformat()},
    )
    replay = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
        json={},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["acknowledged_by_user_id"] == str(context["navigator"].id)
    assert before <= datetime.fromisoformat(body["acknowledged_at"]) <= after
    assert body["effective_state"] == "acknowledged"
    assert replay.status_code == 409
    assert forged.status_code == 422


def test_resolution_requires_acknowledgement_and_is_terminal(db_session: Session) -> None:
    context = _seed_signal(db_session)
    db_session.commit()
    _override_actor(db_session, context)
    signal_id = context["signal"].id
    path = f"/v1/navigator/safety-signals/{signal_id}/resolutions"

    before_ack = _request("POST", path, json={"resolution_reason": "Navigator followed up."})
    assert before_ack.status_code == 409

    acknowledged = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
        json={},
    )
    assert acknowledged.status_code == 201, acknowledged.text
    resolved = _request("POST", path, json={"resolution_reason": "Navigator followed up."})
    replay = _request("POST", path, json={"resolution_reason": "Duplicate resolution."})

    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["resolved_by_user_id"] == str(context["navigator"].id)
    assert resolved.json()["effective_state"] == "resolved"
    assert replay.status_code == 409
    row = db_session.execute(
        text(
            "SELECT effective_state FROM effective_safety_signal_state "
            "WHERE id = :signal_id"
        ),
        {"signal_id": signal_id},
    ).one()
    assert row.effective_state == "resolved"


def test_resolution_route_preserves_known_database_trigger_diagnostic(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session)
    _override_actor(db_session, context)
    signal_id = context["signal"].id
    acknowledged = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
        json={},
    )
    assert acknowledged.status_code == 201, acknowledged.text
    diagnostic = "Dismissed safety signal cannot be resolved"
    _install_resolution_insert_failure(
        db_session,
        sqlstate="P0001",
        message=diagnostic,
    )

    response = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/resolutions",
        json={"resolution_reason": "Navigator followed up."},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": diagnostic}


@pytest.mark.parametrize("sqlstate", ["08006", "P0001"])
def test_resolution_route_sanitizes_unexpected_database_failures(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    sqlstate: str,
) -> None:
    context = _seed_signal(db_session)
    _override_actor(db_session, context)
    signal_id = context["signal"].id
    acknowledged = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
        json={},
    )
    assert acknowledged.status_code == 201, acknowledged.text
    leaked_text = "database password local-synthetic-only"
    _install_resolution_insert_failure(
        db_session,
        sqlstate=sqlstate,
        message=leaked_text,
    )

    response = _request(
        "POST",
        f"/v1/navigator/safety-signals/{signal_id}/resolutions",
        json={"resolution_reason": "Navigator followed up."},
        raise_app_exceptions=False,
    )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert leaked_text not in response.text
    assert "Unexpected database error while resolving a safety signal" in caplog.text


def test_signal_origin_and_severity_are_tenant_patient_episode_safe_and_immutable(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session)
    signal = context["signal"]
    second_episode = CareEpisode(
        organization_id=context["organization"].id,
        patient_id=context["patient"].id,
        status="active",
    )
    db_session.add(second_episode)
    db_session.flush()
    safety_signal_model = _new_model("SafetySignal")

    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                safety_signal_model(
                    organization_id=context["organization"].id,
                    patient_id=context["patient"].id,
                    care_episode_id=second_episode.id,
                    source_submission_id=context["submission"].id,
                    signal_rule_id=context["rule"].id,
                    signal_rule_version=context["rule"].version,
                    deterministic_level="urgent",
                    effective_level="urgent",
                    status="open",
                    evidence=[],
                )
            )
            db_session.flush()

    for statement in (
        "UPDATE safety_signal SET deterministic_level = 'routine' WHERE id = :signal_id",
        "UPDATE safety_signal SET effective_level = 'routine' WHERE id = :signal_id",
        "UPDATE safety_signal SET source_submission_id = NULL, "
        "escalated_from_signal_id = :other_id WHERE id = :signal_id",
    ):
        with pytest.raises(DBAPIError):
            with db_session.begin_nested():
                db_session.execute(
                    text(statement),
                    {"signal_id": signal.id, "other_id": uuid4()},
                )


def test_human_escalation_origin_is_unique_and_uses_a_versioned_human_rule(
    db_session: Session,
) -> None:
    context = _seed_signal(db_session)
    rule_model = _new_model("SignalRule")
    signal_model = _new_model("SafetySignal")
    human_rule = rule_model(
        organization_id=context["organization"].id,
        rule_code="human-escalation",
        version=1,
        rule_kind="human_escalation",
        name="Human escalation",
    )
    db_session.add(human_rule)
    db_session.flush()
    successor = signal_model(
        organization_id=context["organization"].id,
        patient_id=context["patient"].id,
        care_episode_id=context["episode"].id,
        escalated_from_signal_id=context["signal"].id,
        signal_rule_id=human_rule.id,
        signal_rule_version=human_rule.version,
        deterministic_level="urgent",
        effective_level="urgent",
        status="open",
        evidence=[],
    )
    db_session.add(successor)
    db_session.flush()

    with pytest.raises(DBAPIError):
        with db_session.begin_nested():
            db_session.add(
                signal_model(
                    organization_id=context["organization"].id,
                    patient_id=context["patient"].id,
                    care_episode_id=context["episode"].id,
                    escalated_from_signal_id=context["signal"].id,
                    signal_rule_id=human_rule.id,
                    signal_rule_version=human_rule.version,
                    deterministic_level="urgent",
                    effective_level="urgent",
                    status="open",
                    evidence=[],
                )
            )
            db_session.flush()


def test_human_escalation_cannot_name_itself_as_its_predecessor(db_session: Session) -> None:
    """A self-referential row is a cycle, not a valid human-escalation successor."""
    context = _seed_signal(db_session)
    rule_model = _new_model("SignalRule")
    signal_model = _new_model("SafetySignal")
    human_rule = rule_model(
        organization_id=context["organization"].id,
        rule_code="human-escalation",
        version=1,
        rule_kind="human_escalation",
        name="Human escalation",
    )
    db_session.add(human_rule)
    db_session.flush()
    self_id = uuid4()

    with pytest.raises(DBAPIError, match="predecessor|self|check constraint"):
        with db_session.begin_nested():
            db_session.add(
                signal_model(
                    id=self_id,
                    organization_id=context["organization"].id,
                    patient_id=context["patient"].id,
                    care_episode_id=context["episode"].id,
                    escalated_from_signal_id=self_id,
                    signal_rule_id=human_rule.id,
                    signal_rule_version=human_rule.version,
                    deterministic_level="urgent",
                    effective_level="urgent",
                    status="open",
                    evidence=[],
                )
            )
            db_session.flush()


@pytest.mark.parametrize(
    "assignment",
    [
        "rule_code = 'rewritten-rule'",
        "rule_kind = 'human_escalation'",
        "name = 'Rewritten historical rule'",
        "version = 99",
    ],
)
def test_referenced_signal_rule_version_is_immutable(
    db_session: Session,
    assignment: str,
) -> None:
    """Changing a referenced rule version must create a new row, never rewrite history."""
    context = _seed_signal(db_session)

    with pytest.raises(DBAPIError, match="Signal rule versions are immutable"):
        with db_session.begin_nested():
            db_session.execute(
                text(f"UPDATE signal_rule SET {assignment} WHERE id = :rule_id"),
                {"rule_id": context["rule"].id},
            )


def test_safety_routes_are_navigator_only_and_organization_scoped(db_session: Session) -> None:
    context = _seed_signal(db_session)
    signal_id = context["signal"].id
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[current_actor] = lambda: CurrentActor(
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
        role=Role.ADMINISTRATOR,
    )
    assert (
        _request(
            "POST",
            f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
            json={},
        ).status_code
        == 403
    )

    app.dependency_overrides[current_actor] = lambda: CurrentActor(
        user_id=context["navigator"].id,
        organization_id=uuid4(),
        role=Role.NAVIGATOR,
    )
    assert (
        _request(
            "POST",
            f"/v1/navigator/safety-signals/{signal_id}/acknowledgements",
            json={},
        ).status_code
        == 404
    )


def test_revoked_navigator_session_cannot_acknowledge(db_session: Session) -> None:
    context = _seed_signal(db_session)
    actor = CurrentActor(
        user_id=context["navigator"].id,
        organization_id=context["organization"].id,
        role=Role.NAVIGATOR,
    )
    service = DemoSessionService(
        actor_repository=None,
        secret="safety-route-test-secret",
        ttl_minutes=30,
        organization_id=None,
    )
    token = service.create_token(actor)
    context["role"].revoked_at = datetime.now(UTC)
    db_session.flush()
    app.dependency_overrides[get_current_demo_session_service] = lambda: service
    app.dependency_overrides[get_session] = lambda: db_session

    response = _request(
        "POST",
        f"/v1/navigator/safety-signals/{context['signal'].id}/acknowledgements",
        json={},
        cookies={"ojcc_session": token},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Demo session is no longer authorized"}
