from __future__ import annotations

import asyncio
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth.dependencies import current_actor
from app.auth.models import CurrentActor, Role
from app.db.integrity import inspect_integrity
from app.db.models import CheckInSubmission
from app.db.session import get_session
from app.main import app
from scripts.seed_demo import DEMO_IDS, seed_demo
from tests.database_support import disposable_database

DISPOSABLE_PREFIX = "ojcc_task7_"


@contextmanager
def _seeded_disposable_database() -> Iterator[str]:
    with disposable_database(prefix=DISPOSABLE_PREFIX, migrate_to="head") as database:
        yield database.migration_url


def test_seeded_demo_works_through_patient_navigator_and_fhir_application_paths() -> None:
    """Production break: seeded check-ins do not satisfy the application's canonical contract."""
    with _seeded_disposable_database() as database_url:
        engine = create_engine(database_url, pool_pre_ping=True)
        with Session(engine) as session:
            first_summary = seed_demo(session)
            session.commit()
            first_digest = first_summary.as_dict()
            second_digest = seed_demo(session).as_dict()
            session.commit()
            assert second_digest == first_digest
            seeded = session.scalars(
                select(CheckInSubmission)
                .where(CheckInSubmission.organization_id == DEMO_IDS["organization"])
                .order_by(CheckInSubmission.submitted_at)
            ).all()
            assert [submission.id for submission in seeded] == [
                DEMO_IDS["submission_earlier"],
                DEMO_IDS["submission_v1"],
                DEMO_IDS["submission_v2"],
            ]
            assert seeded[0].supersedes_submission_id is None
            assert seeded[1].supersedes_submission_id is None
            assert seeded[2].supersedes_submission_id == seeded[1].id
            assert all(
                submission.check_in_definition_id == DEMO_IDS["definition_v2"]
                for submission in seeded
            )
            seeded_history = {
                submission.id: (
                    submission.answers,
                    submission.submitted_at,
                    submission.supersedes_submission_id,
                )
                for submission in seeded
            }
            assert all(submission.answers["items"] for submission in seeded)
            assert all(submission.answers["questionnaire_version"] for submission in seeded)
            assert all(submission.answers["questionnaire_canonical"] for submission in seeded)
            assert all(
                submission.answers["provenance"]["source"] == "patient-supplied"
                for submission in seeded
            )
            transportation_story = session.execute(
                text(
                    "SELECT n.source_submission_id, t.reported_need_id, t.status, "
                    "p.navigation_task_id, p.value_schema_id, p.value_schema_version, "
                    "tr.resource_id, tr.proposed_change_id "
                    "FROM reported_need n "
                    "JOIN navigation_task t ON t.id = :task "
                    "JOIN proposed_change p ON p.id = :proposal "
                    "JOIN navigation_task_resource tr ON tr.id = :task_resource "
                    "WHERE n.id = :need"
                ),
                {
                    "need": DEMO_IDS["transportation_need"],
                    "task": DEMO_IDS["transportation_task"],
                    "proposal": DEMO_IDS["transportation_task_proposal"],
                    "task_resource": DEMO_IDS["transportation_task_resource"],
                },
            ).one()
            assert transportation_story == (
                DEMO_IDS["submission_v2"],
                DEMO_IDS["transportation_need"],
                "open",
                DEMO_IDS["transportation_task"],
                "ojcc.authorize-navigation-task",
                2,
                DEMO_IDS["transportation_resource"],
                DEMO_IDS["transportation_task_proposal"],
            )
            assert inspect_integrity(session) == []

            journey_ids = {
                name: uuid4()
                for name in ("user", "role", "patient", "identity_link", "episode", "assignment")
            }
            session.execute(
                text(
                    "INSERT INTO user_account "
                    "(id, primary_organization_id, email, display_name, is_active, created_at) "
                    "VALUES (:user, :organization, 'synthetic.contract@example.test', "
                    "'Synthetic Contract Patient', true, CURRENT_TIMESTAMP)"
                ),
                journey_ids | {"organization": DEMO_IDS["organization"]},
            )
            session.execute(
                text(
                    "INSERT INTO role_assignment "
                    "(id, organization_id, user_id, role, granted_at, created_at) VALUES "
                    "(:role, :organization, :user, 'supporting_actor', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                ),
                journey_ids | {"organization": DEMO_IDS["organization"]},
            )
            session.execute(
                text(
                    "INSERT INTO synthetic_patient "
                    "(id, organization_id, external_ref, display_name, demographics, created_at) "
                    "VALUES (:patient, :organization, 'SYNTHETIC-CONTRACT-001', "
                    "'Synthetic Contract Patient', "
                    "'{\"diagnosis\": \"Synthetic journey\"}'::jsonb, "
                    "CURRENT_TIMESTAMP)"
                ),
                journey_ids | {"organization": DEMO_IDS["organization"]},
            )
            session.execute(
                text(
                    "INSERT INTO patient_identity_link "
                    "(id, organization_id, user_id, patient_id, linked_at, created_at) VALUES "
                    "(:identity_link, :organization, :user, :patient, CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                ),
                journey_ids | {"organization": DEMO_IDS["organization"]},
            )
            session.execute(
                text(
                    "INSERT INTO care_episode "
                    "(id, organization_id, patient_id, status, started_at) "
                    "VALUES (:episode, :organization, :patient, 'active', CURRENT_TIMESTAMP)"
                ),
                journey_ids | {"organization": DEMO_IDS["organization"]},
            )
            session.execute(
                text(
                    "INSERT INTO episode_pathway_assignment "
                    "(id, organization_id, care_episode_id, pathway_definition_id, effective_from, "
                    "migration_reason, authored_by_user_id, created_at) VALUES "
                    "(:assignment, :organization, :episode, :pathway, CURRENT_TIMESTAMP, "
                    "'Synthetic contract test', :navigator, CURRENT_TIMESTAMP)"
                ),
                journey_ids
                | {
                    "organization": DEMO_IDS["organization"],
                    "pathway": DEMO_IDS["pathway_v2"],
                    "navigator": DEMO_IDS["navigator_user"],
                },
            )
            session.commit()

        actors = {
            "current": CurrentActor(
                user_id=journey_ids["user"],
                organization_id=DEMO_IDS["organization"],
                role=Role.SUPPORTING_ACTOR,
                patient_id=journey_ids["patient"],
            )
        }

        real_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

        def real_session() -> Generator[Session, None, None]:
            with real_session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = real_session
        app.dependency_overrides[current_actor] = lambda: actors["current"]

        async def exercise_application() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                current = await client.get("/v1/patient/check-ins/current")
                assert current.status_code == 200, current.text
                definition = current.json()
                assert [question["link_id"] for question in definition["questions"]] == [
                    "pain_change",
                    "transportation",
                ]
                assert all(
                    question["required"] is True
                    and question["options"]
                    and all(isinstance(option["value"], str) for option in question["options"])
                    for question in definition["questions"]
                )
                assert definition["active_submission_id"] is None

                first_submission = await client.post(
                    f"/v1/patient/check-ins/{definition['id']}/submissions",
                    json={
                        "questionnaire_version": definition["questionnaire_version"],
                        "answers": [
                            {"link_id": "pain_change", "value": "better"},
                            {"link_id": "transportation", "value": "no"},
                        ],
                        "free_text": "Synthetic first submission through the real patient API.",
                    },
                )
                assert first_submission.status_code == 201, first_submission.text
                first_body = first_submission.json()
                assert first_body["supersedes_submission_id"] is None

                independent = await client.post(
                    f"/v1/patient/check-ins/{definition['id']}/submissions",
                    json={
                        "questionnaire_version": definition["questionnaire_version"],
                        "answers": [
                            {"link_id": "pain_change", "value": "worse"},
                            {"link_id": "transportation", "value": "yes"},
                        ],
                        "free_text": "Synthetic independent check-in through the patient API.",
                    },
                )
                assert independent.status_code == 201, independent.text
                independent_body = independent.json()
                assert independent_body["supersedes_submission_id"] is None
                assert independent_body["id"] != first_body["id"]

                correction = await client.post(
                    f"/v1/patient/check-ins/{definition['id']}/submissions",
                    json={
                        "questionnaire_version": definition["questionnaire_version"],
                        "answers": [
                            {"link_id": "pain_change", "value": "same"},
                            {"link_id": "transportation", "value": "yes"},
                        ],
                        "free_text": "Synthetic correction through the real patient API.",
                        "supersedes_submission_id": independent_body["id"],
                    },
                )
                assert correction.status_code == 201, correction.text
                correction_body = correction.json()
                assert correction_body["supersedes_submission_id"] == independent_body["id"]

                timeline = await client.get("/v1/patient/journey-timeline")
                assert timeline.status_code == 200, timeline.text
                assert [event["kind"] for event in timeline.json()["events"]] == [
                    "check_in_submitted", "check_in_submitted", "check_in_corrected"
                ]
                assert [event["source_id"] for event in timeline.json()["events"]] == [
                    first_body["id"], independent_body["id"], correction_body["id"]
                ]
                assert timeline.json()["events"][2]["detail"]["correction_of_submission_id"] == (
                    independent_body["id"]
                )
                assert str(DEMO_IDS["submission_v1"]) not in timeline.text

                actors["current"] = CurrentActor(
                    user_id=DEMO_IDS["navigator_user"],
                    organization_id=DEMO_IDS["organization"],
                    role=Role.NAVIGATOR,
                )
                patient_case = await client.get(
                    f"/v1/navigator/patients/{journey_ids['patient']}/case"
                )
                assert patient_case.status_code == 200, patient_case.text
                case_body = patient_case.json()
                assert case_body["patient"]["id"] == str(journey_ids["patient"])
                assert {
                    submission["id"] for submission in case_body["longitudinal_submissions"]
                } == {
                    first_body["id"], correction_body["id"]
                }
                assert case_body["longitudinal_submissions"][0]["items"]
                assert case_body["longitudinal_submissions"][0]["provenance"]["source"] == (
                    "patient-supplied"
                )

                workspace = await client.get(
                    f"/v1/navigator/needs/{DEMO_IDS['transportation_need']}/workspace"
                )
                assert workspace.status_code == 200, workspace.text
                workspace_body = workspace.json()
                between = workspace_body["comparisons"]["between_check_ins"]
                assert between["status"] == "available"
                assert between["previous_submission_id"] == str(DEMO_IDS["submission_earlier"])
                assert between["current_submission_id"] == str(DEMO_IDS["submission_v2"])
                assert between["deltas"] == [
                    {
                        "field_identifier": "pain_change",
                        "previous_present": True,
                        "current_present": True,
                        "previous_value": "better",
                        "current_value": "same",
                    },
                    {
                        "field_identifier": "transportation",
                        "previous_present": True,
                        "current_present": True,
                        "previous_value": "no",
                        "current_value": "yes",
                    },
                ]
                assert workspace_body["evidence"][0]["source_submission_id"] == (
                    str(DEMO_IDS["submission_v2"])
                )
                assert first_body["id"] not in workspace.text

                actors["current"] = CurrentActor(
                    user_id=journey_ids["user"],
                    organization_id=DEMO_IDS["organization"],
                    role=Role.SUPPORTING_ACTOR,
                    patient_id=journey_ids["patient"],
                )
                exported = await client.get(
                    f"/v1/patient/check-ins/{correction_body['id']}/fhir"
                )
                assert exported.status_code == 200, exported.text
                resources = [entry["resource"] for entry in exported.json()["entry"]]
                questionnaire_response = next(
                    resource
                    for resource in resources
                    if resource["resourceType"] == "QuestionnaireResponse"
                )
                observations = [
                    resource for resource in resources if resource["resourceType"] == "Observation"
                ]
                revision = next(
                    resource for resource in resources if resource["resourceType"] == "Provenance"
                )
                assert questionnaire_response["item"]
                assert observations
                assert revision["entity"][0]["what"]["reference"] == (
                    f"QuestionnaireResponse/{independent_body['id']}"
                )

                foreign_export = await client.get(
                    f"/v1/patient/check-ins/{DEMO_IDS['submission_v2']}/fhir"
                )
                assert foreign_export.status_code == 404, foreign_export.text

                with Session(engine) as session:
                    own_history = session.scalars(
                        select(CheckInSubmission).where(
                            CheckInSubmission.patient_id == journey_ids["patient"]
                        )
                    ).all()
                    by_id = {str(submission.id): submission for submission in own_history}
                    assert len(by_id) == 3
                    assert by_id[first_body["id"]].supersedes_submission_id is None
                    assert by_id[independent_body["id"]].supersedes_submission_id is None
                    assert by_id[first_body["id"]].answers["items"][0]["value"] == "better"
                    assert by_id[independent_body["id"]].answers["items"][0]["value"] == "worse"
                    assert session.scalar(
                        text("SELECT count(*) FROM reported_need WHERE patient_id = :patient"),
                        {"patient": journey_ids["patient"]},
                    ) == 0
                    active_ids = session.execute(
                        text(
                            "SELECT id FROM active_check_in_submission WHERE patient_id = :patient"
                        ),
                        {"patient": journey_ids["patient"]},
                    ).scalars().all()
                    assert set(active_ids) == {
                        by_id[first_body["id"]].id, by_id[correction_body["id"]].id
                    }

        try:
            asyncio.run(exercise_application())
            with Session(engine) as session:
                persisted_seed = session.scalars(
                    select(CheckInSubmission).where(
                        CheckInSubmission.organization_id == DEMO_IDS["organization"],
                        CheckInSubmission.patient_id == DEMO_IDS["patient"],
                    )
                ).all()
                assert {
                    submission.id: (
                        submission.answers,
                        submission.submitted_at,
                        submission.supersedes_submission_id,
                    )
                    for submission in persisted_seed
                } == seeded_history
                assert inspect_integrity(session) == []
        finally:
            app.dependency_overrides.clear()
            engine.dispose()
