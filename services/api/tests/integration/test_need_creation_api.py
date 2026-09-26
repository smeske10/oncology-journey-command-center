import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.demo_sessions import get_demo_session_service
from app.auth.demo_actors import parse_demo_actors
from app.auth.dependencies import get_current_demo_session_service
from app.auth.service import DemoSessionService, SqlAlchemyActorRepository
from app.db.session import get_session
from app.main import app
from scripts.seed_demo import DEMO_IDS, demo_actor_configuration
from tests.integration.test_need_creation import creation_database, submission

__all__ = ["creation_database"]
PATH = "/v1/navigator/need-creation-proposals"


def request(runtime, method, path, body=None, role="navigator"):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            if role:
                assert (await client.post(f"/v1/demo/session/{role}")).status_code == 204
            return await client.request(method, path, json=body)

    with Session(runtime) as session:
        app.dependency_overrides[get_session] = lambda: session
        service = DemoSessionService(
            actor_repository=SqlAlchemyActorRepository(session),
            secret="need-creation-test-secret-at-least-32-characters",
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


def proposal_body(root):
    return dict(
        request_id=str(uuid4()),
        chain_root_id=str(root),
        source_submission_id=str(root),
        rationale="Review synthetic transportation support",
    )


def test_http_create_review_approve_retry_and_correction_history(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    body = proposal_body(root)
    response = request(runtime, "POST", PATH, body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    proposal = response.json()
    assert request(runtime, "POST", PATH, body).json()["id"] == proposal["id"]
    assert proposal["evidence"]["source_submission_id"] == str(root)
    assert proposal["state"] == "pending"
    decision = dict(request_id=str(uuid4()), decision="approved", reason=None)
    result = request(runtime, "POST", f"{PATH}/{proposal['id']}/decisions", decision)
    assert result.status_code == 200, result.text
    assert result.json()["outcome"] == "created"
    assert (
        request(runtime, "POST", f"{PATH}/{proposal['id']}/decisions", decision).json()
        == result.json()
    )
    submission(runtime, root, "no")
    history = request(runtime, "GET", PATH)
    row = next(p for p in history.json()["proposals"] if p["id"] == proposal["id"])
    assert row["source_changed"] is True
    assert row["current_answer_text"] == "no"
    assert row["current_source_submission_id"] != str(root)
    assert row["state"] == "created"
    assert row["decisions"][0]["reported_need_id"] == result.json()["reported_need_id"]


def test_http_stale_approval_is_durably_audited(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    proposal = request(runtime, "POST", PATH, proposal_body(root)).json()
    submission(runtime, root, "yes")
    body = dict(request_id=str(uuid4()), decision="approved")
    result = request(runtime, "POST", f"{PATH}/{proposal['id']}/decisions", body)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "stale_evidence"
    with runtime.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM need_creation_decision WHERE id=:id"),
                dict(id=body["request_id"]),
            )
            == 1
        )


@pytest.mark.parametrize(
    "role,expected", [(None, 401), ("supporting_actor", 403), ("administrator", 403)]
)
def test_http_role_boundaries(creation_database, role, expected):
    _, runtime, _ = creation_database
    assert request(runtime, "GET", PATH, role=role).status_code == expected
    assert request(runtime, "POST", PATH, proposal_body(uuid4()), role=role).status_code == expected


def test_http_rejects_client_evidence_and_unknown_proposals(creation_database):
    _, runtime, _ = creation_database
    body = proposal_body(uuid4()) | {"evidence": {"value": "yes"}}
    assert request(runtime, "POST", PATH, body).status_code == 422
    assert (
        request(
            runtime,
            "POST",
            f"{PATH}/{uuid4()}/decisions",
            dict(request_id=str(uuid4()), decision="approved"),
        ).status_code
        == 404
    )


def test_http_decline_closes_proposal_without_creating_need(creation_database):
    _, runtime, _ = creation_database
    root = submission(runtime)
    proposal = request(runtime, "POST", PATH, proposal_body(root)).json()
    result = request(
        runtime,
        "POST",
        f"{PATH}/{proposal['id']}/decisions",
        dict(request_id=str(uuid4()), decision="declined", reason="Synthetic duplicate review"),
    )
    assert result.status_code == 200
    assert result.json()["outcome"] == "declined"
    assert result.json()["reported_need_id"] is None


def test_history_correction_flag_and_current_answer_share_a_snapshot(
    creation_database, monkeypatch
):
    from app.api.need_creation import _proposals
    from tests.integration.test_need_creation import propose

    _, runtime, _ = creation_database
    root = submission(runtime)
    with runtime.begin() as connection:
        proposal = propose(connection, root)
    with Session(runtime) as session:
        execute = session.execute
        corrected = False

        def interleaved(statement, *args, **kwargs):
            nonlocal corrected
            if "SELECT p.*" in str(statement) and not corrected:
                corrected = True
                submission(runtime, root, "no")
            return execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", interleaved)
        (row,) = _proposals(session, DEMO_IDS["organization"], proposal["id"])
        assert corrected
        if row.source_changed:
            assert row.current_answer_text == "no"
            assert row.current_source_submission_id != root
        else:
            assert row.current_answer_text == "yes"
            assert row.current_source_submission_id == root
