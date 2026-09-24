import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.demo_sessions import get_demo_session_service
from app.auth.demo_actors import parse_demo_actors
from app.auth.dependencies import current_actor, get_current_demo_session_service
from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService, SqlAlchemyActorRepository
from app.db.session import get_session
from app.main import app
from scripts.seed_demo import DEMO_IDS, demo_actor_configuration, seed_demo
from tests.database_support import disposable_database
from tests.test_demo_seed import _database_digest


@pytest.fixture(scope="module")
def preview_database():
    with disposable_database(prefix="ojcc_task7_", migrate_to="head") as database:
        owner = create_engine(database.migration_url)
        runtime = create_engine(database.application_url)
        try:
            with Session(owner) as session:
                seed_demo(session)
                session.commit()
            yield runtime
        finally:
            runtime.dispose()
            owner.dispose()


def request_preview(engine, *, role="navigator", foreign=False):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            if role:
                response = await client.post(f"/v1/demo/session/{role}")
                assert response.status_code == 204
            if foreign:
                app.dependency_overrides[current_actor] = lambda: CurrentActor(
                    user_id=uuid4(),
                    organization_id=uuid4(),
                    role=Role.NAVIGATOR,
                )
            return await client.get("/v1/navigator/need-candidates")

    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        app.dependency_overrides[get_session] = lambda: session
        service = DemoSessionService(
            actor_repository=SqlAlchemyActorRepository(session),
            secret="candidate-preview-test-secret-at-least-32-characters",
            ttl_minutes=30,
            organization_id=DEMO_IDS["organization"],
            demo_actors=parse_demo_actors(json.dumps(demo_actor_configuration())),
        )
        app.dependency_overrides[get_current_demo_session_service] = lambda: service
        app.dependency_overrides[get_demo_session_service] = lambda: service
        try:
            return asyncio.run(send())
        finally:
            app.dependency_overrides.clear()


def test_preview_real_restricted_login_is_read_only_and_reports_canonical_needs(preview_database):
    with Session(preview_database) as session:
        assert session.scalar(text("SELECT current_user")) == "ojcc_api"
        assert not session.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"))
        before = _database_digest(session)
    for _ in range(3):
        response = request_preview(preview_database)
        assert response.status_code == 200
        body = response.json()
        (candidate,) = body["candidates"]
        assert candidate["source_submission_id"] == str(DEMO_IDS["submission_v2"])
        assert candidate["chain_root_id"] == str(DEMO_IDS["submission_v1"])
        assert candidate["evidence"]["value"] == "yes"
        assert candidate["is_correction"] is True
        related = candidate["linked_needs"] + candidate["other_transportation_needs"]
        assert {row["id"] for row in related} == {
            str(DEMO_IDS[key]) for key in ("closed_need", "open_need", "transportation_need")
        }
        assert (
            next(row for row in related if row["id"] == str(DEMO_IDS["closed_need"]))[
                "effective_state"
            ]
            == "closed"
        )
        assert body["unavailable"] == []
        assert response.headers["cache-control"] == "no-store"
    with Session(preview_database) as session:
        assert _database_digest(session) == before


@pytest.mark.parametrize(
    "role, expected", [(None, 401), ("supporting_actor", 403), ("administrator", 403)]
)
def test_unauthorized_preview_discloses_no_patient_data(preview_database, role, expected):
    response = request_preview(preview_database, role=role)
    assert response.status_code == expected
    assert str(DEMO_IDS["patient"]) not in response.text
    assert "candidates" not in response.json()


def test_foreign_organization_cannot_see_seeded_candidates(preview_database):
    response = request_preview(preview_database, foreign=True)
    assert response.status_code == 200
    assert response.json() == {"candidates": [], "unavailable": []}
