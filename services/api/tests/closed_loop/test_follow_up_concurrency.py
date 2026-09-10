from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, Literal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_demo_session_service
from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService
from app.db.models import PatientIdentityLink, RoleAssignment
from app.domain.follow_ups import FollowUpConflict, record_follow_up_response
from app.domain.navigation_tasks import (
    claim_navigation_task,
    complete_navigation_task,
    start_navigation_task,
)
from app.domain.outcomes import record_outcome
from app.main import app

TEST_SESSION_SECRET = "closed-loop-signed-cookie-secret"


def _prepare_completed_task(engine: Engine, case: Any) -> Any:
    with Session(engine, expire_on_commit=False) as session:
        claim_navigation_task(
            session,
            organization_id=case.organization_id,
            task_id=case.navigation_task_id,
            actor_user_id=case.navigator_user_id,
            proposed_change_id=case.proposed_change_id,
            due_at=datetime.now(UTC) + timedelta(days=2),
        )
        start_navigation_task(
            session,
            organization_id=case.organization_id,
            task_id=case.navigation_task_id,
            actor_user_id=case.navigator_user_id,
        )
        result = complete_navigation_task(
            session,
            organization_id=case.organization_id,
            task_id=case.navigation_task_id,
            actor_user_id=case.navigator_user_id,
        )
        session.commit()
        return result


@pytest.mark.parametrize("first", ["response", "outcome"])
def test_response_outcome_race_has_one_truthful_final_state(
    committed_closed_loop_case: tuple[Engine, Any],
    first: Literal["response", "outcome"],
) -> None:
    engine, case = committed_closed_loop_case
    completion = _prepare_completed_task(engine, case)
    assert completion.follow_up_request_id is not None
    first_written = Event()
    contender_started = Event()
    release_first = Event()

    def respond() -> str:
        with Session(engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            if first == "outcome":
                assert first_written.wait(timeout=10)
                contender_started.set()
            try:
                record_follow_up_response(
                    session,
                    organization_id=case.organization_id,
                    actor_user_id=case.patient_user_id,
                    patient_id=case.patient_id,
                    request_id=completion.follow_up_request_id,
                    response="unresolved",
                    note="Synthetic response before closure.",
                )
                if first == "response":
                    first_written.set()
                    assert release_first.wait(timeout=10)
                session.commit()
                return "recorded"
            except FollowUpConflict as error:
                session.rollback()
                return error.code

    def close_need() -> str:
        with Session(engine) as session:
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            if first == "response":
                assert first_written.wait(timeout=10)
                contender_started.set()
            record_outcome(
                session,
                organization_id=case.organization_id,
                need_id=case.reported_need_id,
                recorded_by_user_id=case.navigator_user_id,
                disposition="closed_unresolved",
                note=None,
                idempotency_key=f"response-race-{first}-{uuid4()}",
            )
            if first == "outcome":
                first_written.set()
                assert release_first.wait(timeout=10)
            session.commit()
            return "recorded"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            response_future = executor.submit(respond)
            outcome_future = executor.submit(close_need)
            assert contender_started.wait(timeout=10)
            release_first.set()
            response_result = response_future.result(timeout=20)
            outcome_result = outcome_future.result(timeout=20)
    finally:
        release_first.set()

    assert outcome_result == "recorded"
    assert response_result == ("recorded" if first == "response" else "need_closed")
    with engine.connect() as connection:
        response_count = connection.scalar(
            text(
                "SELECT count(*) FROM follow_up_response "
                "WHERE follow_up_request_id = :request_id"
            ),
            {"request_id": completion.follow_up_request_id},
        )
        outcome_count = connection.scalar(
            text("SELECT count(*) FROM outcome WHERE reported_need_id = :need_id"),
            {"need_id": case.reported_need_id},
        )
    assert response_count == (1 if first == "response" else 0)
    assert outcome_count == 1


@pytest.mark.parametrize("revoked_kind", ["link", "role"])
def test_signed_cookie_is_rechecked_in_a_fresh_session_after_revocation(
    committed_closed_loop_case: tuple[Engine, Any],
    revoked_kind: Literal["link", "role"],
) -> None:
    engine, case = committed_closed_loop_case
    _prepare_completed_task(engine, case)
    actor = CurrentActor(
        user_id=case.patient_user_id,
        organization_id=case.organization_id,
        role=Role.SUPPORTING_ACTOR,
        patient_id=case.patient_id,
    )
    session_service = DemoSessionService(
        actor_repository=None,
        secret=TEST_SESSION_SECRET,
        ttl_minutes=30,
        organization_id=None,
    )
    token = session_service.create_token(actor)
    app.dependency_overrides[get_current_demo_session_service] = lambda: session_service

    async def request_follow_ups() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            cookies={"ojcc_session": token},
        ) as client:
            return await client.get("/v1/patient/follow-ups")

    try:
        first = asyncio.run(request_follow_ups())
        assert first.status_code == 200, first.text
        with Session(engine) as session:
            if revoked_kind == "link":
                record = session.get(PatientIdentityLink, case.patient_identity_link_id)
            else:
                record = session.get(RoleAssignment, case.patient_role_assignment_id)
            assert record is not None
            record.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        after_revocation = asyncio.run(request_follow_ups())
    finally:
        app.dependency_overrides.clear()

    assert after_revocation.status_code == 401
    assert after_revocation.json()["detail"] == "Demo session is no longer authorized"
