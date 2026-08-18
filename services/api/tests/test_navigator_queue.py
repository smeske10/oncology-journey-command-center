import asyncio
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.auth.dependencies import current_actor, get_current_demo_session_service
from app.auth.models import CurrentActor, Role
from app.auth.service import DemoSessionService
from app.db.models import (
    CheckInSubmission,
    NavigationTask,
    SafetySignal,
    SyntheticPatient,
)
from app.db.models import (
    ReportedNeed as PersistedNeed,
)
from app.db.session import get_session
from app.domain.enums import (
    CheckInStatus,
    NavigationTaskStatus,
    NeedStatus,
    SafetySeverity,
    SafetySignalStatus,
)
from app.main import app


@dataclass
class ScalarRows:
    values: list[Any]

    def all(self) -> list[Any]:
        return self.values

    def first(self) -> Any | None:
        return self.values[0] if self.values else None


@dataclass
class NavigatorSession:
    organization_id: UUID
    patients: list[SyntheticPatient]
    submissions: list[CheckInSubmission]
    needs: list[PersistedNeed]
    signals: list[SafetySignal]
    tasks: list[NavigationTask]

    def scalars(self, statement: Any) -> ScalarRows:
        entity = statement.column_descriptions[0]["entity"]
        raw_params = statement.compile().params.values()
        params = {
            item
            for value in raw_params
            for item in (value if isinstance(value, list) else [value])
        }
        assert self.organization_id in params, "navigator reads must include organization scope"
        values: list[Any]
        if entity is SyntheticPatient:
            values = self.patients
        elif entity is CheckInSubmission:
            values = self.submissions
        elif entity is PersistedNeed:
            values = self.needs
        elif entity is SafetySignal:
            values = self.signals
        elif entity is NavigationTask:
            values = self.tasks
        else:
            raise AssertionError(f"Unexpected entity query: {entity}")
        patient_ids = {
            value for value in params if isinstance(value, UUID) and value != self.organization_id
        }
        return ScalarRows(
            [
                value
                for value in values
                if value.organization_id == self.organization_id
                and (
                    not patient_ids
                    or getattr(value, "patient_id", value.id) in patient_ids
                    or value.id in patient_ids
                )
            ]
        )


@pytest.fixture
def navigator_context(
) -> Generator[tuple[NavigatorSession, CurrentActor, SyntheticPatient], None, None]:
    organization_id = uuid4()
    actor = CurrentActor(user_id=uuid4(), organization_id=organization_id, role=Role.NAVIGATOR)
    patient = SyntheticPatient(
        id=uuid4(),
        organization_id=organization_id,
        external_ref="SYN-001",
        display_name="Maya Chen",
        demographics={
            "diagnosis": "Synthetic active-treatment breast cancer pathway",
            "consent_status": "synthetic demo consented",
            "upcoming_appointment": {
                "starts_at": "2026-08-20T09:00:00Z",
                "label": "Synthetic infusion visit",
            },
        },
    )
    submission = CheckInSubmission(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient.id,
        status=CheckInStatus.SUBMITTED,
        submitted_at=datetime.now(UTC) - timedelta(hours=2),
        answers={
            "items": [
                {
                    "link_id": "nausea_change",
                    "label": "Nausea change",
                    "value": "worse",
                },
                {
                    "link_id": "medication_question",
                    "label": "Medication question",
                    "value": "yes",
                },
            ],
            "free_text": "Nausea now interferes with meals.",
            "provenance": {"source": "patient-supplied"},
        },
    )
    need = PersistedNeed(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient.id,
        source_submission_id=submission.id,
        kind="symptom_change",
        status=NeedStatus.OPEN,
        created_at=datetime.now(UTC) - timedelta(hours=2),
        evidence=[
            {"field": "nausea_change", "text": "worse"},
            {"field": "free_text", "text": "Nausea now interferes with meals."},
        ],
    )
    task = NavigationTask(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient.id,
        reported_need_id=need.id,
        assignee_user_id=actor.user_id,
        title="Review reported nausea and medication question",
        status=NavigationTaskStatus.OPEN,
        due_at=datetime.now(UTC) + timedelta(hours=4),
    )
    signal = SafetySignal(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient.id,
        source_submission_id=submission.id,
        rule_code="demo-review-required",
        severity=SafetySeverity.ROUTINE,
        status=SafetySignalStatus.ACTIVE,
        evidence=[{"field": "nausea_change", "text": "worse"}],
    )
    session = NavigatorSession(
        organization_id=organization_id,
        patients=[patient],
        submissions=[submission],
        needs=[need],
        signals=[signal],
        tasks=[task],
    )
    app.dependency_overrides[current_actor] = lambda: actor
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield session, actor, patient
    finally:
        app.dependency_overrides.clear()


def get(path: str, cookies: dict[str, str] | None = None) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", cookies=cookies
        ) as client:
            return await client.get(path)

    return asyncio.run(request())


def test_navigator_queue_returns_exact_evidence_and_explainable_order(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """Navigator queue items retain exact patient evidence and every ordering reason."""
    _, _, patient = navigator_context

    response = get("/v1/navigator/queue")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["patient_id"] == str(patient.id)
    assert item["patient_display_name"] == "Maya Chen"
    assert item["evidence"] == [
        {"field": "nausea_change", "text": "worse"},
        {"field": "free_text", "text": "Nausea now interferes with meals."},
    ]
    assert item["priority"]["reasons"] == ["worsening_report", "medication_uncertainty", "due_soon"]


def test_navigator_case_contains_each_workspace_section_and_priority_reason(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """The selected case keeps all review context inside a tenant-scoped response."""
    _, _, patient = navigator_context

    response = get(f"/v1/navigator/patients/{patient.id}/case")

    assert response.status_code == 200, response.text
    case = response.json()
    assert case["patient"]["display_name"] == "Maya Chen"
    assert case["longitudinal_submissions"][0]["free_text"] == "Nausea now interferes with meals."
    assert case["open_needs"][0]["priority"]["reasons"] == [
        "worsening_report",
        "medication_uncertainty",
        "due_soon",
    ]
    assert case["safety_signals"][0]["rule_code"] == "demo-review-required"
    assert case["navigation_tasks"][0]["title"] == "Review reported nausea and medication question"
    assert case["upcoming_synthetic_appointment"]["label"] == "Synthetic infusion visit"


@pytest.mark.parametrize("role", [Role.SUPPORTING_ACTOR, Role.ADMINISTRATOR])
def test_non_navigator_roles_cannot_open_navigator_routes(role: Role) -> None:
    """Every navigator route rejects non-navigator actors at the route boundary."""
    app.dependency_overrides[current_actor] = lambda: CurrentActor(
        user_id=uuid4(), organization_id=uuid4(), role=role
    )
    try:
        response = get("/v1/navigator/queue")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


@pytest.mark.parametrize("cookie", [None, {"ojcc_session": "invalid"}])
def test_missing_or_invalid_session_cannot_open_navigator_queue(
    cookie: dict[str, str] | None,
) -> None:
    """Authentication failure happens before navigator data access."""
    service = DemoSessionService(
        actor_repository=None,
        secret="test-session-secret",
        ttl_minutes=30,
        organization_id=None,
    )
    app.dependency_overrides[get_current_demo_session_service] = lambda: service
    try:
        response = get("/v1/navigator/queue", cookie)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_navigator_cannot_read_a_patient_outside_their_organization(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """Patient case lookup returns not found across tenant boundaries."""
    session, _, _ = navigator_context
    other_patient = SyntheticPatient(
        id=uuid4(),
        organization_id=uuid4(),
        external_ref="OTHER-001",
        display_name="Other tenant patient",
        demographics={},
    )
    session.patients.append(other_patient)

    response = get(f"/v1/navigator/patients/{other_patient.id}/case")

    assert response.status_code == 404


def test_need_factory_preserves_immutable_evidence_and_has_a_stable_idempotency_key(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """Repeating source processing returns the same deterministic navigation inputs."""
    from app.domain.needs import NeedFactory

    session, _, _ = navigator_context
    submission = session.submissions[0]

    first = NeedFactory.from_submission(submission)
    second = NeedFactory.from_submission(submission)

    assert first == second
    assert first[0].evidence[-1].text == "Nausea now interferes with meals."
    assert first[0].idempotency_key == f"{submission.id}:symptom_change"
