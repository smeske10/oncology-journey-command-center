import asyncio
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.auth.dependencies import current_actor
from app.auth.models import CurrentActor, Role
from app.db.models import CheckInDefinition, CheckInSubmission
from app.db.session import get_session
from app.domain.enums import CheckInStatus
from app.main import app


@dataclass
class FakeSession:
    definition: CheckInDefinition
    submissions: dict[UUID, CheckInSubmission] = field(default_factory=dict)
    committed: bool = False

    def scalar(self, statement: Any) -> CheckInDefinition | CheckInSubmission | None:
        entity = statement.column_descriptions[0]["entity"]
        if entity is CheckInDefinition:
            return self.definition
        if entity is CheckInSubmission:
            return next(iter(self.submissions.values()), None)
        return None

    def add(self, entity: CheckInSubmission) -> None:
        self.submissions[entity.id] = entity

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def patient_cookie() -> dict[str, str]:
    return {"ojcc_session": "synthetic-test-session"}


@pytest.fixture
def check_in_definition() -> CheckInDefinition:
    organization_id = uuid4()
    return CheckInDefinition(
        id=uuid4(),
        organization_id=organization_id,
        pathway_definition_id=uuid4(),
        slug="weekly-check-in",
        version=1,
        title="Weekly check-in",
        questionnaire={
            "canonical": "https://demo.example/Questionnaire/breast-active|1",
            "questions": [
                {
                    "link_id": "nausea_change",
                    "label": "Since your last check-in, is your nausea better, the same, or worse?",
                }
            ],
        },
    )


@pytest.fixture
def client_context(
    check_in_definition: CheckInDefinition,
) -> Generator[tuple[FakeSession, CurrentActor], None, None]:
    actor = CurrentActor(
        user_id=uuid4(),
        organization_id=check_in_definition.organization_id,
        role=Role.SUPPORTING_ACTOR,
    )
    session = FakeSession(definition=check_in_definition)
    app.dependency_overrides[current_actor] = lambda: actor
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield session, actor
    finally:
        app.dependency_overrides.clear()


def test_submit_check_in_is_atomic(
    patient_cookie: dict[str, str],
    check_in_definition: CheckInDefinition,
    client_context: tuple[FakeSession, CurrentActor],
) -> None:
    """This fails if the route responds before persisting a patient submission."""
    session, _ = client_context
    payload = {
        "questionnaire_version": "breast-active-v1",
        "answers": [{"link_id": "nausea_change", "value": "worse"}],
        "free_text": "Nausea now interferes with meals.",
    }

    async def submit() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", cookies=patient_cookie
        ) as client:
            return await client.post(
                f"/v1/patient/check-ins/{check_in_definition.id}/submissions",
                json=payload,
            )

    response = asyncio.run(submit())

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "submitted"
    assert response.json()["questionnaire_version"] == "breast-active-v1"
    assert session.committed is True
    assert len(session.submissions) == 1


def test_patient_can_export_only_own_synthetic_fhir_submission(
    patient_cookie: dict[str, str],
    check_in_definition: CheckInDefinition,
    client_context: tuple[FakeSession, CurrentActor],
) -> None:
    """This fails if the patient-scoped FHIR export route is absent."""
    session, actor = client_context
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=actor.organization_id,
        patient_id=actor.user_id,
        check_in_definition_id=check_in_definition.id,
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        answers={
            "questionnaire_version": "breast-active-v1",
            "questionnaire_canonical": "https://demo.example/Questionnaire/breast-active|1",
            "items": [{"link_id": "nausea_change", "label": "Nausea change", "value": "worse"}],
            "free_text": None,
            "provenance": {"source": "patient-supplied", "actor_id": str(actor.user_id)},
        },
    )
    session.submissions[submission.id] = submission

    async def export() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", cookies=patient_cookie
        ) as client:
            return await client.get(f"/v1/patient/check-ins/{submission.id}/fhir")

    response = asyncio.run(export())

    assert response.status_code == 200
    assert response.json()["resourceType"] == "Bundle"


def test_submission_rejects_explicit_contact_fields_with_a_public_demo_warning(
    patient_cookie: dict[str, str],
    check_in_definition: CheckInDefinition,
    client_context: tuple[FakeSession, CurrentActor],
) -> None:
    """This fails if a contact field lacks the public-demo warning."""
    payload = {
        "questionnaire_version": "breast-active-v1",
        "answers": [{"link_id": "nausea_change", "value": "worse"}],
        "mobile": "synthetic contact placeholder",
    }

    async def submit() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", cookies=patient_cookie
        ) as client:
            return await client.post(
                f"/v1/patient/check-ins/{check_in_definition.id}/submissions",
                json=payload,
            )

    response = asyncio.run(submit())

    assert response.status_code == 422
    assert "must not receive real health information" in response.text


def test_patient_check_in_router_is_registered_without_enabling_docs() -> None:
    """This fails if the patient router stops being included in the application."""
    from app.api.patient_check_ins import router

    router_paths = {getattr(route, "path", "") for route in router.routes}
    assert "/v1/patient/check-ins/current" in router_paths
    assert "/v1/patient/check-ins/{definition_id}/submissions" in router_paths
    assert "/v1/patient/check-ins/{submission_id}/fhir" in router_paths
    assert any(getattr(route, "original_router", None) is router for route in app.routes)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
