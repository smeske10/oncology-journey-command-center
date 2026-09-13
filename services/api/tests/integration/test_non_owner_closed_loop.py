from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.demo_sessions import get_demo_session_service
from app.auth.dependencies import get_current_demo_session_service
from app.auth.service import DemoSessionService, SqlAlchemyActorRepository
from app.db.session import get_session
from app.main import app
from scripts.seed_demo import DEMO_IDS, seed_demo
from tests.database_support import DisposableDatabase, disposable_database

SESSION_SECRET = "non-owner-closed-loop-signed-cookie-secret"


@pytest.fixture(scope="module")
def non_owner_database() -> Iterator[DisposableDatabase]:
    with disposable_database(prefix="ojcc_task7_", migrate_to="head") as database:
        owner_engine = create_engine(database.migration_url)
        try:
            with Session(owner_engine) as session, session.begin():
                seed_demo(session)
        finally:
            owner_engine.dispose()
        yield database


@contextmanager
def _runtime_dependencies(database: DisposableDatabase) -> Iterator[None]:
    runtime_engine = create_engine(database.application_url, pool_pre_ping=True)
    authentication_session = Session(runtime_engine)
    session_service = DemoSessionService(
        actor_repository=SqlAlchemyActorRepository(authentication_session),
        secret=SESSION_SECRET,
        ttl_minutes=30,
        organization_id=DEMO_IDS["organization"],
    )

    def runtime_session() -> Iterator[Session]:
        with Session(runtime_engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = runtime_session
    app.dependency_overrides[get_demo_session_service] = lambda: session_service
    app.dependency_overrides[get_current_demo_session_service] = lambda: session_service
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        authentication_session.close()
        runtime_engine.dispose()


async def _assert_success(response: httpx.Response, expected: int = 200) -> dict[str, Any]:
    assert response.status_code == expected, response.text
    if not response.content:
        return {}
    value = response.json()
    assert isinstance(value, dict)
    return value


async def _run_signed_cookie_journey(database: DisposableDatabase) -> None:
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as navigator,
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as patient,
    ):
        await _assert_success(await navigator.post("/v1/demo/session/navigator"), 204)
        await _assert_success(await patient.post("/v1/demo/session/supporting_actor"), 204)

        current_check_in = await _assert_success(
            await patient.get("/v1/patient/check-ins/current")
        )
        assert current_check_in["active_submission_id"] == str(DEMO_IDS["submission_v2"])
        correction = await _assert_success(
            await patient.post(
                f"/v1/patient/check-ins/{current_check_in['id']}/submissions",
                json={
                    "questionnaire_version": current_check_in["questionnaire_version"],
                    "answers": [
                        {"link_id": "pain_change", "value": "same"},
                        {"link_id": "transportation", "value": "no"},
                    ],
                    "free_text": "Synthetic non-owner correction.",
                    "supersedes_submission_id": current_check_in["active_submission_id"],
                },
            ),
            201,
        )
        assert correction["supersedes_submission_id"] == str(DEMO_IDS["submission_v2"])

        created_proposal = await _assert_success(
            await navigator.post(
                "/v1/navigator/proposed-changes",
                json={
                    "change_type": "override_signal_severity",
                    "safety_signal_id": str(DEMO_IDS["recovery_signal"]),
                    "navigation_task_id": None,
                    "patient_message_id": None,
                    "proposed_value": {"level": "routine"},
                    "rationale": "Synthetic non-owner severity review.",
                    "value_schema_id": "ojcc.override-signal-severity",
                    "value_schema_version": 1,
                },
            ),
            201,
        )
        assert created_proposal["state"] == "pending"
        acknowledged = await _assert_success(
            await navigator.post(
                f"/v1/navigator/safety-signals/{DEMO_IDS['recovery_signal']}"
                "/acknowledgements",
                json={},
            ),
            201,
        )
        assert acknowledged["effective_state"] == "acknowledged"
        resolved = await _assert_success(
            await navigator.post(
                f"/v1/navigator/safety-signals/{DEMO_IDS['recovery_signal']}/resolutions",
                json={"resolution_reason": "Synthetic non-owner navigator follow-up."},
            ),
            201,
        )
        assert resolved["effective_state"] == "resolved"

        queue = await _assert_success(await navigator.get("/v1/navigator/queue"))
        assert str(DEMO_IDS["transportation_need"]) in {
            item["need_id"] for item in queue["items"]
        }
        workspace = await _assert_success(
            await navigator.get(
                f"/v1/navigator/needs/{DEMO_IDS['transportation_need']}/workspace"
            )
        )
        task = next(
            item
            for item in workspace["tasks"]
            if item["id"] == str(DEMO_IDS["transportation_task"])
        )
        proposal = next(
            item
            for item in task["proposals"]
            if item["id"] == str(DEMO_IDS["transportation_task_proposal"])
        )
        assert proposal["state"] == "pending"

        decision = await _assert_success(
            await navigator.post(
                "/v1/navigator/proposed-changes/"
                f"{DEMO_IDS['transportation_task_proposal']}/decisions",
                json={"decision": "approved", "reason": None},
            ),
            201,
        )
        assert decision["proposal_state"] == "approved"

        due_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        claimed = await _assert_success(
            await navigator.post(
                f"/v1/navigator/tasks/{DEMO_IDS['transportation_task']}/claim",
                json={
                    "proposed_change_id": str(DEMO_IDS["transportation_task_proposal"]),
                    "due_at": due_at,
                },
            )
        )
        assert claimed["status"] == "assigned"
        started = await _assert_success(
            await navigator.post(
                f"/v1/navigator/tasks/{DEMO_IDS['transportation_task']}/start",
                json={},
            )
        )
        assert started["status"] == "in_progress"
        completed = await _assert_success(
            await navigator.post(
                f"/v1/navigator/tasks/{DEMO_IDS['transportation_task']}/complete",
                json={},
            )
        )
        assert completed["status"] == "completed"
        request_id = completed["follow_up_request_id"]
        assert request_id is not None

        follow_ups = await _assert_success(await patient.get("/v1/patient/follow-ups"))
        assert request_id in {item["request_id"] for item in follow_ups["items"]}
        response = await _assert_success(
            await patient.post(
                f"/v1/patient/follow-ups/{request_id}/responses",
                json={"response": "resolved", "note": "Synthetic response."},
            ),
            201,
        )
        assert response["response"] == "resolved"

        refreshed = await _assert_success(
            await navigator.get(
                f"/v1/navigator/needs/{DEMO_IDS['transportation_need']}/workspace"
            )
        )
        refreshed_follow_up = next(
            item for item in refreshed["follow_ups"] if item["request_id"] == request_id
        )
        assert refreshed_follow_up["response"] == "resolved"
        preview = await _assert_success(
            await navigator.get(
                f"/v1/navigator/needs/{DEMO_IDS['transportation_need']}/outcome-preview"
            )
        )
        assert preview["tasks"] == []
        outcome = await _assert_success(
            await navigator.post(
                f"/v1/navigator/needs/{DEMO_IDS['transportation_need']}/outcomes",
                headers={"Idempotency-Key": f"non-owner-{uuid4()}"},
                json={"disposition": "resolved", "note": "Synthetic closure."},
            ),
            201,
        )
        assert outcome["disposition"] == "resolved"

        closed_queue = await _assert_success(await navigator.get("/v1/navigator/queue"))
        assert str(DEMO_IDS["transportation_need"]) not in {
            item["need_id"] for item in closed_queue["items"]
        }
        closed_workspace = await _assert_success(
            await navigator.get(
                f"/v1/navigator/needs/{DEMO_IDS['transportation_need']}/workspace"
            )
        )
        assert closed_workspace["outcome"]["disposition"] == "resolved"
        timeline = await _assert_success(await patient.get("/v1/patient/journey-timeline"))
        assert any(item["kind"] == "outcome_recorded" for item in timeline["events"])


def test_real_signed_cookie_closed_loop_runs_as_non_owner(
    non_owner_database: DisposableDatabase,
) -> None:
    owner_engine = create_engine(non_owner_database.migration_url)
    runtime_engine = create_engine(non_owner_database.application_url)
    try:
        with owner_engine.connect() as connection:
            before = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM check_in_submission), "
                    "(SELECT count(*) FROM proposed_change), "
                    "(SELECT count(*) FROM approval_decision), "
                    "(SELECT count(*) FROM safety_signal_resolution), "
                    "(SELECT count(*) FROM follow_up_request), "
                    "(SELECT count(*) FROM follow_up_response), "
                    "(SELECT count(*) FROM outcome), "
                    "(SELECT count(*) FROM audit_event)"
                )
            ).one()
        with runtime_engine.connect() as connection:
            before_runtime_identity = connection.execute(
                text("SELECT current_user, session_user")
            ).one()
    finally:
        owner_engine.dispose()
        runtime_engine.dispose()

    with _runtime_dependencies(non_owner_database):
        asyncio.run(_run_signed_cookie_journey(non_owner_database))

    runtime_engine = create_engine(non_owner_database.application_url)
    owner_engine = create_engine(non_owner_database.migration_url)
    try:
        with runtime_engine.connect() as connection:
            runtime_identity = connection.execute(
                text("SELECT current_user, session_user")
            ).one()
            counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM check_in_submission), "
                    "(SELECT count(*) FROM proposed_change), "
                    "(SELECT count(*) FROM approval_decision), "
                    "(SELECT count(*) FROM safety_signal_resolution), "
                    "(SELECT count(*) FROM follow_up_request), "
                    "(SELECT count(*) FROM follow_up_response), "
                    "(SELECT count(*) FROM outcome), "
                    "(SELECT count(*) FROM audit_event)"
                ),
            ).one()
        with owner_engine.connect() as connection:
            database_owner = connection.scalar(
                text(
                    "SELECT pg_get_userbyid(datdba) FROM pg_database "
                    "WHERE datname = current_database()"
                )
            )
            object_owners = set(
                connection.scalars(
                    text(
                        "SELECT DISTINCT pg_get_userbyid(c.relowner) "
                        "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v')"
                    )
                )
            )
    finally:
        runtime_engine.dispose()
        owner_engine.dispose()

    assert before_runtime_identity == ("ojcc_api", "ojcc_api")
    assert runtime_identity == ("ojcc_api", "ojcc_api")
    assert database_owner == "ojcc_migrator"
    assert object_owners == {"ojcc_migrator"}
    deltas = tuple(after - prior for prior, after in zip(before, counts, strict=True))
    assert deltas[:7] == (1, 1, 1, 1, 1, 1, 1)
    assert deltas[7] >= 3
