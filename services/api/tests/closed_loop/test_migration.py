from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import settings
from tests.database_support import (
    DisposableDatabase,
    disposable_database,
    run_alembic,
    upgrade_database,
)

DISPOSABLE_MIGRATION_DATABASE_PREFIX = "ojcc_migration_test_"


@contextmanager
def _disposable_migration_database() -> Iterator[str]:
    with disposable_database(
        prefix=DISPOSABLE_MIGRATION_DATABASE_PREFIX,
        migrate_to=None,
    ) as database:
        yield database.migration_url


def _database_target(database_url: str) -> DisposableDatabase:
    migration_url = make_url(database_url)
    application_url = make_url(settings.database_url).set(
        host=migration_url.host,
        port=migration_url.port,
        database=migration_url.database,
    )
    assert migration_url.database is not None
    return DisposableDatabase(
        name=migration_url.database,
        migration_url=database_url,
        application_url=application_url.render_as_string(hide_password=False),
    )


def _run_alembic(
    database_url: str,
    command: str,
    revision: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    database = _database_target(database_url)
    if command == "upgrade":
        return upgrade_database(database, revision, check=check)
    return run_alembic(database, [command, revision], check=check)


def test_0006_upgrades_an_empty_database_and_empty_extension_downgrades_cleanly() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "upgrade", "0006_navigator_closed_loop")
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert {"follow_up_request", "follow_up_response"} <= set(
                inspector.get_table_names()
            )
            assert "authorized_proposed_change_id" in {
                column["name"] for column in inspector.get_columns("navigation_task")
            }
        finally:
            engine.dispose()

        _run_alembic(
            database_url,
            "downgrade",
            "0005_workflow_knowledge_audit",
        )
        engine = create_engine(database_url)
        try:
            inspector = inspect(engine)
            assert "follow_up_request" not in inspector.get_table_names()
            assert "follow_up_response" not in inspector.get_table_names()
            assert "authorized_proposed_change_id" not in {
                column["name"] for column in inspector.get_columns("navigation_task")
            }
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "0005_workflow_knowledge_audit"
                guard = connection.execute(
                    text(
                        "SELECT pg_get_functiondef("
                        "'guard_navigation_task_lifecycle()'::regprocedure)"
                    )
                ).scalar_one()
                assert "authorized_proposed_change_id" not in guard
                assert "terminal state is irreversible" in guard
                assert connection.execute(
                    text(
                        "SELECT prosecdef FROM pg_proc "
                        "WHERE oid = 'guard_navigation_task_lifecycle()'::regprocedure"
                    )
                ).scalar_one() is False
        finally:
            engine.dispose()


def _seed_representative_0005_history(database_url: str) -> dict[str, Any]:
    ids = {
        name: uuid4()
        for name in (
            "organization",
            "proposer",
            "approver",
            "patient_user",
            "proposer_role",
            "approver_role",
            "patient_role",
            "patient",
            "patient_link",
            "pathway",
            "episode",
            "assignment",
            "definition",
            "submission",
            "need",
            "task",
            "assigned_task",
            "in_progress_task",
            "completed_task",
            "cancelled_task",
            "policy",
            "resource",
            "proposal",
            "resource_match",
            "decision",
        )
    }
    proposed_at = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    approved_at = proposed_at + timedelta(minutes=5)
    resource_snapshot = {
        "resource_id": str(ids["resource"]),
        "name": "Synthetic transportation directory",
        "category": "transportation",
        "url": "https://example.test/synthetic-transportation",
        "metadata": {"audience": "synthetic"},
        "match_rationale": "Synthetic route support matches the test need.",
    }

    engine = create_engine(database_url)
    metadata = MetaData()
    try:
        with engine.begin() as connection:
            def insert(table_name: str, **values: object) -> None:
                table = Table(table_name, metadata, autoload_with=connection)
                connection.execute(table.insert().values(**values))

            insert(
                "organization",
                id=ids["organization"],
                name=f"Populated 0005 fixture {ids['organization']}",
            )
            for actor in ("proposer", "approver", "patient_user"):
                insert(
                    "user_account",
                    id=ids[actor],
                    primary_organization_id=ids["organization"],
                    email=f"{actor}-{ids[actor]}@example.test",
                    display_name=f"Synthetic {actor}",
                    is_active=True,
                )
            for actor, role_name in (
                ("proposer", "navigator"),
                ("approver", "navigator"),
                ("patient_user", "supporting_actor"),
            ):
                insert(
                    "role_assignment",
                    id=ids[f"{actor.removesuffix('_user')}_role"],
                    organization_id=ids["organization"],
                    user_id=ids[actor],
                    role=role_name,
                    granted_at=proposed_at - timedelta(days=1),
                )
            insert(
                "synthetic_patient",
                id=ids["patient"],
                organization_id=ids["organization"],
                external_ref=f"migration-{ids['patient']}",
                display_name="Synthetic migration patient",
                demographics={},
            )
            insert(
                "patient_identity_link",
                id=ids["patient_link"],
                organization_id=ids["organization"],
                user_id=ids["patient_user"],
                patient_id=ids["patient"],
                linked_at=proposed_at - timedelta(days=1),
            )
            insert(
                "pathway_definition",
                id=ids["pathway"],
                organization_id=ids["organization"],
                slug=f"migration-{ids['pathway']}",
                version=1,
                name="Synthetic migration pathway",
                configuration={},
                is_active=True,
            )
            insert(
                "care_episode",
                id=ids["episode"],
                organization_id=ids["organization"],
                patient_id=ids["patient"],
                status="active",
                started_at=proposed_at - timedelta(days=7),
            )
            insert(
                "episode_pathway_assignment",
                id=ids["assignment"],
                organization_id=ids["organization"],
                care_episode_id=ids["episode"],
                pathway_definition_id=ids["pathway"],
                effective_from=proposed_at - timedelta(days=7),
                migration_reason="Synthetic baseline assignment.",
                authored_by_user_id=ids["proposer"],
            )
            insert(
                "check_in_definition",
                id=ids["definition"],
                organization_id=ids["organization"],
                pathway_definition_id=ids["pathway"],
                slug=f"migration-check-in-{ids['definition']}",
                version=1,
                title="Synthetic migration check-in",
                questionnaire={"questions": []},
            )
            insert(
                "check_in_submission",
                id=ids["submission"],
                organization_id=ids["organization"],
                patient_id=ids["patient"],
                care_episode_id=ids["episode"],
                check_in_definition_id=ids["definition"],
                status="submitted",
                answers={},
                submission_source="patient",
                submitted_by_user_id=ids["patient_user"],
                submitted_at=proposed_at - timedelta(hours=1),
            )
            insert(
                "reported_need",
                id=ids["need"],
                organization_id=ids["organization"],
                patient_id=ids["patient"],
                care_episode_id=ids["episode"],
                source_submission_id=ids["submission"],
                kind="transportation",
                status="open",
                evidence=[{"field": "transportation", "text": "yes"}],
            )
            connection.execute(text("ALTER TABLE navigation_task DISABLE TRIGGER USER"))
            insert(
                "navigation_task",
                id=ids["task"],
                organization_id=ids["organization"],
                patient_id=ids["patient"],
                reported_need_id=ids["need"],
                title="Legacy unbound transportation task",
                status="open",
            )
            for status in ("assigned", "in_progress", "completed", "cancelled"):
                task_values: dict[str, object] = {
                    "id": ids[f"{status}_task"],
                    "organization_id": ids["organization"],
                    "patient_id": ids["patient"],
                    "reported_need_id": ids["need"],
                    "assignee_user_id": ids["approver"],
                    "due_at": proposed_at + timedelta(days=1),
                    "title": f"Legacy unbound {status} task",
                    "status": status,
                }
                if status == "completed":
                    task_values["completed_at"] = approved_at
                if status == "cancelled":
                    task_values.update(
                        {
                            "cancelled_by_user_id": ids["approver"],
                            "cancelled_at": approved_at,
                            "cancellation_reason": "need_closed",
                        }
                    )
                insert("navigation_task", **task_values)
            connection.execute(text("ALTER TABLE navigation_task ENABLE TRIGGER USER"))
            insert(
                "approval_policy",
                id=ids["policy"],
                organization_id=ids["organization"],
                change_type="authorize_navigation_task",
                version=1,
                effective_from=proposed_at - timedelta(days=1),
                allow_self_approval=False,
                required_approval_count=1,
                required_approver_role="navigator",
            )
            insert(
                "resource",
                id=ids["resource"],
                organization_id=ids["organization"],
                name=resource_snapshot["name"],
                category=resource_snapshot["category"],
                url=resource_snapshot["url"],
                is_active=True,
                metadata=resource_snapshot["metadata"],
            )
            insert(
                "proposed_change",
                id=ids["proposal"],
                organization_id=ids["organization"],
                proposed_by_user_id=ids["proposer"],
                proposed_at=proposed_at,
                change_type="authorize_navigation_task",
                proposed_value={
                    "title": "Approved synthetic transportation task",
                    "resources": [resource_snapshot],
                },
                rationale="Synthetic v2 proposal preserved across migration.",
                value_schema_id="ojcc.authorize-navigation-task",
                value_schema_version=2,
                navigation_task_id=ids["task"],
                approval_policy_id=ids["policy"],
                approval_policy_version=1,
                allow_self_approval_snapshot=False,
                required_approval_count_snapshot=1,
                required_approver_role_snapshot="navigator",
            )
            insert(
                "navigation_task_resource",
                id=ids["resource_match"],
                organization_id=ids["organization"],
                navigation_task_id=ids["task"],
                resource_id=ids["resource"],
                proposed_change_id=ids["proposal"],
                resource_name_snapshot=resource_snapshot["name"],
                resource_category_snapshot=resource_snapshot["category"],
                resource_url_snapshot=resource_snapshot["url"],
                resource_metadata_snapshot=resource_snapshot["metadata"],
                match_rationale_snapshot=resource_snapshot["match_rationale"],
                proposed_at=proposed_at,
            )
            insert(
                "approval_decision",
                id=ids["decision"],
                organization_id=ids["organization"],
                proposed_change_id=ids["proposal"],
                authorized_by_user_id=ids["approver"],
                qualifying_role_assignment_id=ids["approver_role"],
                qualifying_role_snapshot="navigator",
                decision="approved",
                authorized_at=approved_at,
            )
            assert connection.execute(
                text(
                    "SELECT effective_state FROM effective_proposed_change_state "
                    "WHERE id = :proposal_id"
                ),
                {"proposal_id": ids["proposal"]},
            ).scalar_one() == "approved"
    finally:
        engine.dispose()
    return ids | {
        "proposed_at": proposed_at,
        "approved_at": approved_at,
        "resource_snapshot": resource_snapshot,
    }


def test_0006_preserves_populated_0005_unbound_task_and_approved_v2_resources() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "upgrade", "0005_workflow_knowledge_audit")
        expected = _seed_representative_0005_history(database_url)
        _run_alembic(database_url, "upgrade", "0006_navigator_closed_loop")

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                task = connection.execute(
                    text(
                        "SELECT title, status, authorized_proposed_change_id "
                        "FROM navigation_task WHERE id = :task_id"
                    ),
                    {"task_id": expected["task"]},
                ).mappings().one()
                assert task.title == "Legacy unbound transportation task"
                assert task.status == "open"
                assert task.authorized_proposed_change_id is None

                proposal_value = connection.execute(
                    text("SELECT proposed_value FROM proposed_change WHERE id = :id"),
                    {"id": expected["proposal"]},
                ).scalar_one()
                assert proposal_value == {
                    "title": "Approved synthetic transportation task",
                    "resources": [expected["resource_snapshot"]],
                }
                resource = connection.execute(
                    text(
                        "SELECT resource_name_snapshot, resource_category_snapshot, "
                        "resource_url_snapshot, resource_metadata_snapshot, "
                        "match_rationale_snapshot, proposed_at, approved_at "
                        "FROM navigation_task_resource WHERE id = :id"
                    ),
                    {"id": expected["resource_match"]},
                ).mappings().one()
                snapshot = expected["resource_snapshot"]
                assert resource.resource_name_snapshot == snapshot["name"]
                assert resource.resource_category_snapshot == snapshot["category"]
                assert resource.resource_url_snapshot == snapshot["url"]
                assert resource.resource_metadata_snapshot == snapshot["metadata"]
                assert resource.match_rationale_snapshot == snapshot["match_rationale"]
                assert resource.proposed_at == expected["proposed_at"]
                assert resource.approved_at == expected["approved_at"]
        finally:
            engine.dispose()


def test_0007_preserves_unbound_execution_history_for_every_task_state() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "upgrade", "0005_workflow_knowledge_audit")
        expected = _seed_representative_0005_history(database_url)
        _run_alembic(database_url, "upgrade", "0006_navigator_closed_loop")

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                before = tuple(
                    tuple(row)
                    for row in connection.execute(
                        text(
                            "SELECT id, title, status, assignee_user_id, due_at, "
                            "authorized_proposed_change_id, completed_at, "
                            "cancelled_by_user_id, cancelled_at, cancellation_reason "
                            "FROM navigation_task WHERE reported_need_id = :need_id "
                            "ORDER BY id"
                        ),
                        {"need_id": expected["need"]},
                    )
                )
                history_counts_before = connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM proposed_change), "
                        "(SELECT count(*) FROM approval_decision), "
                        "(SELECT count(*) FROM audit_event)"
                    )
                ).one()
        finally:
            engine.dispose()

        _run_alembic(database_url, "upgrade", "head")

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                after = tuple(
                    tuple(row)
                    for row in connection.execute(
                        text(
                            "SELECT id, title, status, assignee_user_id, due_at, "
                            "authorized_proposed_change_id, completed_at, "
                            "cancelled_by_user_id, cancelled_at, cancellation_reason "
                            "FROM navigation_task WHERE reported_need_id = :need_id "
                            "ORDER BY id"
                        ),
                        {"need_id": expected["need"]},
                    )
                )
                history_counts_after = connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM proposed_change), "
                        "(SELECT count(*) FROM approval_decision), "
                        "(SELECT count(*) FROM audit_event)"
                    )
                ).one()
                version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        finally:
            engine.dispose()

        assert after == before
        assert history_counts_after == history_counts_before
        assert {str(row[2]) for row in after} == {
            "open",
            "assigned",
            "in_progress",
            "completed",
            "cancelled",
        }
        assert all(row[5] is None for row in after)
        assert version == "0007_database_least_privilege"


def test_0006_downgrade_refuses_to_discard_approved_execution_history() -> None:
    with _disposable_migration_database() as database_url:
        _run_alembic(database_url, "upgrade", "0005_workflow_knowledge_audit")
        expected = _seed_representative_0005_history(database_url)
        _run_alembic(database_url, "upgrade", "0006_navigator_closed_loop")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE navigation_task "
                        "SET authorized_proposed_change_id = :proposal_id, "
                        "title = 'Approved synthetic transportation task', "
                        "assignee_user_id = :approver_id, due_at = :due_at, "
                        "status = 'assigned' WHERE id = :task_id"
                    ),
                    {
                        "proposal_id": expected["proposal"],
                        "approver_id": expected["approver"],
                        "due_at": datetime.now(UTC) + timedelta(days=1),
                        "task_id": expected["task"],
                    },
                )
        finally:
            engine.dispose()

        result = _run_alembic(
            database_url,
            "downgrade",
            "0005_workflow_knowledge_audit",
            check=False,
        )
        assert result.returncode != 0
        assert "Refusing to downgrade 0006" in result.stderr
        assert "approved task bindings or follow-up history exist" in result.stderr

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "0006_navigator_closed_loop"
                assert connection.execute(
                    text(
                        "SELECT authorized_proposed_change_id FROM navigation_task "
                        "WHERE id = :task_id"
                    ),
                    {"task_id": expected["task"]},
                ).scalar_one() == expected["proposal"]
        finally:
            engine.dispose()
