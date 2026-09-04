import asyncio
from collections.abc import Generator
from dataclasses import dataclass, field
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
    SignalRule,
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
    closed_need_ids: set[UUID] = field(default_factory=set)

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
            if "effective_need_state" in str(statement):
                values = [value for value in values if value.id not in self.closed_need_ids]
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
    rule = SignalRule(
        id=uuid4(),
        organization_id=organization_id,
        rule_code="demo-review-required",
        version=1,
        rule_kind="deterministic",
        name="Demo review required",
    )
    signal = SafetySignal(
        id=uuid4(),
        organization_id=organization_id,
        patient_id=patient.id,
        care_episode_id=uuid4(),
        source_submission_id=submission.id,
        signal_rule_id=rule.id,
        signal_rule_version=rule.version,
        deterministic_level=SafetySeverity.ROUTINE,
        effective_level=SafetySeverity.ROUTINE,
        status=SafetySignalStatus.OPEN,
        evidence=[{"field": "nausea_change", "text": "worse"}],
        rule=rule,
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
    assert item["priority"]["reasons"] == ["worsening_report", "due_soon"]


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
    assert first[0].evidence[0].text == "worse"
    assert first[0].idempotency_key == (
        f"{submission.id}:symptom_change:{first[0].evidence_hash}"
    )


def test_queue_priority_uses_only_evidence_for_each_need(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """A practical barrier cannot inherit clinical ordering reasons from the same submission."""
    session, actor, patient = navigator_context
    source_submission = session.submissions[0]
    transport_need = PersistedNeed(
        id=uuid4(),
        organization_id=actor.organization_id,
        patient_id=patient.id,
        source_submission_id=source_submission.id,
        kind="transportation",
        status=NeedStatus.OPEN,
        created_at=session.needs[0].created_at,
        evidence=[{"field": "transportation", "text": "yes"}],
    )
    session.needs.append(transport_need)
    session.needs.append(
        PersistedNeed(
            id=uuid4(),
            organization_id=actor.organization_id,
            patient_id=patient.id,
            source_submission_id=source_submission.id,
            kind="medication_question",
            status=NeedStatus.OPEN,
            created_at=session.needs[0].created_at,
            evidence=[{"field": "medication_question", "text": "yes"}],
        )
    )

    response = get("/v1/navigator/queue")

    assert response.status_code == 200, response.text
    items_by_kind = {item["kind"]: item for item in response.json()["items"]}
    transport_score = items_by_kind["transportation"]["priority"]["score"]
    assert items_by_kind["symptom_change"]["priority"]["score"] > transport_score
    assert items_by_kind["medication_question"]["priority"]["score"] > transport_score
    assert "worsening_report" in items_by_kind["symptom_change"]["priority"]["reasons"]
    assert "medication_uncertainty" in items_by_kind["medication_question"]["priority"]["reasons"]
    assert "worsening_report" not in items_by_kind["transportation"]["priority"]["reasons"]
    assert "medication_uncertainty" not in items_by_kind["transportation"]["priority"]["reasons"]


def test_queue_uses_injected_deployment_priority_policy(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """Route ordering is driven by an injectable deployment policy, never implicit defaults."""
    from app.api.navigator_queue import get_navigator_priority_policy
    from app.domain.prioritization import OperationalPriorityWeights

    session, actor, patient = navigator_context
    source_submission = session.submissions[0]
    session.needs.append(
        PersistedNeed(
            id=uuid4(),
            organization_id=actor.organization_id,
            patient_id=patient.id,
            source_submission_id=source_submission.id,
            kind="transportation",
            status=NeedStatus.OPEN,
            created_at=session.needs[0].created_at,
            evidence=[{"field": "transportation", "text": "yes"}],
        )
    )
    policy = OperationalPriorityWeights(
        kind_weights={
            "symptom_change": 0,
            "medication_question": 0,
            "transportation": 200,
            "financial_support": 0,
            "other": 0,
        },
        worsening_report=1,
        medication_uncertainty=1,
        due_soon=0,
        unresolved_over_24_hours=0,
        unresolved_over_48_hours=0,
        high_threshold=100,
        medium_threshold=50,
    )
    app.dependency_overrides[get_navigator_priority_policy] = lambda: policy
    try:
        response = get("/v1/navigator/queue")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    first = response.json()["items"][0]
    assert first["kind"] == "transportation"
    assert first["priority"]["reasons"] == ["configured_kind_transportation"]


def test_queue_sort_uses_uuid_as_a_stable_final_tiebreaker() -> None:
    """Equal operational fields cannot reorder across requests or Python implementations."""
    from app.api.navigator_queue import QueueItemRead, _queue_sort_key

    created_at = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    common = {
        "patient_id": uuid4(),
        "patient_display_name": "Synthetic patient",
        "kind": "transportation",
        "priority": {"level": "routine", "score": 0, "reasons": []},
        "evidence": [],
        "created_at": created_at,
        "due_at": None,
        "owner_id": None,
    }
    later = QueueItemRead(need_id=UUID(int=2), **common)
    earlier = QueueItemRead(need_id=UUID(int=1), **common)

    assert [item.need_id for item in sorted([later, earlier], key=_queue_sort_key)] == [
        earlier.need_id,
        later.need_id,
    ]


def test_queue_and_case_use_effective_need_state_to_hide_outcome_closed_needs(
    navigator_context: tuple[NavigatorSession, CurrentActor, SyntheticPatient],
) -> None:
    """A raw active state cannot keep a need visible after its Outcome exists."""
    session, actor, patient = navigator_context
    closed_need = PersistedNeed(
        id=uuid4(),
        organization_id=actor.organization_id,
        patient_id=patient.id,
        source_submission_id=session.submissions[0].id,
        kind="transportation",
        status=NeedStatus.OPEN,
        created_at=datetime.now(UTC),
        evidence=[{"field": "transportation", "text": "yes"}],
    )
    session.needs.append(closed_need)
    session.closed_need_ids.add(closed_need.id)

    queue_response = get("/v1/navigator/queue")
    case_response = get(f"/v1/navigator/patients/{patient.id}/case")

    assert queue_response.status_code == 200, queue_response.text
    assert case_response.status_code == 200, case_response.text
    assert str(closed_need.id) not in {
        item["need_id"] for item in queue_response.json()["items"]
    }
    assert str(closed_need.id) not in {
        item["need_id"] for item in case_response.json()["open_needs"]
    }
