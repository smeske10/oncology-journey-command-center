from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.db.models import Base
from app.domain import enums

APPEND_ONLY_TABLES = (
    "check_in_submission",
    "proposed_change",
    "approval_decision",
    "outcome",
    "safety_signal_resolution",
    "audit_event",
    "workflow_transition_event",
)
TASK5_TABLES = {
    "workflow_run",
    "workflow_transition_event",
    "manual_review_task",
    "navigation_task_resource",
    "organization_knowledge_approval",
    "agent_run_citation",
}
PRIVILEGED_TRIGGER_FUNCTIONS = (
    "append_workflow_transition_event",
    "guard_approval_decision",
    "apply_final_approval_decision",
    "apply_navigation_resource_approval",
    "guard_proposed_change_revision",
    "guard_navigation_task_resource_proposal",
    "guard_safety_signal_resolution",
    "close_reported_need_from_outcome",
)
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DISPOSABLE_PREFIX = "ojcc_task5_migration_"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine(settings.database_url)
    with engine.connect() as value:
        transaction = value.begin()
        try:
            yield value
        finally:
            transaction.rollback()
    engine.dispose()


def _require_task5_schema(connection: Connection) -> None:
    """Production break: migration 0005 is absent from the live PostgreSQL schema."""
    assert TASK5_TABLES <= set(inspect(connection).get_table_names())
    assert "actor_type" in {
        column["name"] for column in inspect(connection).get_columns("audit_event")
    }


def _seed_actor_context(connection: Connection) -> dict[str, UUID]:
    ids = {"organization": uuid4(), "user": uuid4(), "agent_run": uuid4()}
    connection.execute(
        text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
        {"id": ids["organization"], "name": f"Audit {uuid4()}"},
    )
    connection.execute(
        text(
            "INSERT INTO user_account (id, email, display_name, is_active) "
            "VALUES (:id, :email, 'Auditor', true)"
        ),
        {"id": ids["user"], "email": f"{uuid4()}@example.test"},
    )
    connection.execute(
        text(
            "INSERT INTO agent_run "
            "(id, organization_id, trace_id, agent_name, status, input_payload, "
            "output_payload, validation) VALUES (:id, :organization_id, :trace_id, "
            "'quality-validator', 'succeeded', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb)"
        ),
        {
            "id": ids["agent_run"],
            "organization_id": ids["organization"],
            "trace_id": str(uuid4()),
        },
    )
    return ids


def test_audit_metadata_has_the_complete_four_form_actor_contract() -> None:
    """Production break: ORM metadata accepts a mixed, blank, or incomplete audit actor."""
    assert hasattr(enums, "AuditActorType")
    assert [member.value for member in enums.AuditActorType] == [
        "user",
        "agent",
        "policy",
        "system",
    ]
    audit = Base.metadata.tables["audit_event"]
    assert {
        "actor_type",
        "actor_user_id",
        "actor_agent_run_id",
        "actor_policy_component",
        "actor_policy_version",
        "actor_system_component",
        "actor_system_version",
    } <= set(audit.c.keys())
    actor_constraint = next(
        constraint
        for constraint in audit.constraints
        if constraint.name == "ck_audit_event_ck_audit_event_actor_shape"
    )
    sql = str(actor_constraint.sqltext)
    assert "CASE actor_type" in sql
    assert "NULLIF(trim(actor_policy_component), '') IS NOT NULL" in sql
    assert "NULLIF(trim(actor_system_component), '') IS NOT NULL" in sql


@pytest.mark.parametrize(
    ("actor_type", "actor_values"),
    [
        ("user", {"actor_user_id": "user"}),
        ("agent", {"actor_agent_run_id": "agent_run"}),
        (
            "policy",
            {"actor_policy_component": "citation-policy", "actor_policy_version": "2026.08"},
        ),
        (
            "system",
            {"actor_system_component": "workflow-coordinator", "actor_system_version": "1"},
        ),
    ],
)
def test_audit_event_accepts_each_complete_actor_form(
    connection: Connection,
    actor_type: str,
    actor_values: dict[str, str],
) -> None:
    """Production break: one canonical actor form cannot be recorded in PostgreSQL."""
    _require_task5_schema(connection)
    ids = _seed_actor_context(connection)
    columns = [
        "id",
        "organization_id",
        "actor_type",
        "entity_type",
        "entity_id",
        "event_type",
        "payload",
    ]
    values = [
        ":id",
        ":organization_id",
        ":actor_type",
        "'workflow_run'",
        ":entity_id",
        "'test'",
        "'{}'::jsonb",
    ]
    parameters: dict[str, object] = {
        "id": uuid4(),
        "organization_id": ids["organization"],
        "actor_type": actor_type,
        "entity_id": uuid4(),
    }
    for column, raw_value in actor_values.items():
        columns.append(column)
        values.append(f":{column}")
        parameters[column] = ids[raw_value] if raw_value in ids else raw_value
    connection.execute(
        text(f"INSERT INTO audit_event ({', '.join(columns)}) VALUES ({', '.join(values)})"),
        parameters,
    )


@pytest.mark.parametrize(
    ("actor_type", "actor_values"),
    [
        ("user", {}),
        ("user", {"actor_user_id": "user", "actor_system_component": "mixed"}),
        ("agent", {}),
        ("agent", {"actor_agent_run_id": "agent_run", "actor_user_id": "user"}),
        ("policy", {"actor_policy_component": "policy"}),
        (
            "policy",
            {"actor_policy_component": " ", "actor_policy_version": "1"},
        ),
        (
            "policy",
            {
                "actor_policy_component": "policy",
                "actor_policy_version": "1",
                "actor_user_id": "user",
            },
        ),
        ("system", {"actor_system_version": "1"}),
        (
            "system",
            {"actor_system_component": "system", "actor_system_version": " "},
        ),
        (
            "system",
            {
                "actor_system_component": "system",
                "actor_system_version": "1",
                "actor_agent_run_id": "agent_run",
            },
        ),
        ("unknown", {}),
    ],
)
def test_audit_event_rejects_mixed_blank_or_incomplete_actor_forms(
    connection: Connection,
    actor_type: str,
    actor_values: dict[str, str],
) -> None:
    """Production break: the CASE invariant admits an ambiguous audit actor."""
    _require_task5_schema(connection)
    ids = _seed_actor_context(connection)
    columns = [
        "id",
        "organization_id",
        "actor_type",
        "entity_type",
        "entity_id",
        "event_type",
        "payload",
    ]
    values = [
        ":id",
        ":organization_id",
        ":actor_type",
        "'workflow_run'",
        ":entity_id",
        "'test'",
        "'{}'::jsonb",
    ]
    parameters: dict[str, object] = {
        "id": uuid4(),
        "organization_id": ids["organization"],
        "actor_type": actor_type,
        "entity_id": uuid4(),
    }
    for column, raw_value in actor_values.items():
        columns.append(column)
        values.append(f":{column}")
        parameters[column] = ids[raw_value] if raw_value in ids else raw_value
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(
                text(
                    f"INSERT INTO audit_event ({', '.join(columns)}) "
                    f"VALUES ({', '.join(values)})"
                ),
                parameters,
            )


def _seed_append_only_rows(connection: Connection) -> dict[str, UUID]:
    ids = _seed_actor_context(connection)
    ids.update({table: uuid4() for table in APPEND_ONLY_TABLES})
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            "INSERT INTO check_in_submission "
            "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, "
            "status, answers, submission_source, submitted_by_user_id, submitted_at) "
            "VALUES (:id, :organization_id, :patient_id, :episode_id, :definition_id, "
            "'submitted', '{}'::jsonb, 'patient', :user_id, :at)"
        ),
        {
            "id": ids["check_in_submission"],
            "organization_id": ids["organization"],
            "patient_id": uuid4(),
            "episode_id": uuid4(),
            "definition_id": uuid4(),
            "user_id": ids["user"],
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    connection.execute(
        text(
            "INSERT INTO proposed_change "
            "(id, organization_id, proposed_by_user_id, proposed_at, change_type, "
            "proposed_value, rationale, value_schema_id, value_schema_version, "
            "navigation_task_id, approval_policy_id, approval_policy_version, "
            "allow_self_approval_snapshot, required_approval_count_snapshot, "
            "required_approver_role_snapshot) VALUES (:id, :organization_id, :user_id, :at, "
            "'authorize_navigation_task', '{\"title\":\"Test\"}'::jsonb, 'Test', "
            "'ojcc.authorize-navigation-task', 1, :task_id, :policy_id, 1, true, 1, 'navigator')"
        ),
        {
            "id": ids["proposed_change"],
            "organization_id": ids["organization"],
            "user_id": ids["user"],
            "at": datetime(2026, 8, 18, tzinfo=UTC),
            "task_id": uuid4(),
            "policy_id": uuid4(),
        },
    )
    connection.execute(
        text(
            "INSERT INTO approval_decision "
            "(id, organization_id, proposed_change_id, authorized_by_user_id, "
            "qualifying_role_assignment_id, qualifying_role_snapshot, decision, authorized_at) "
            "VALUES (:id, :organization_id, :proposal_id, :user_id, :assignment_id, "
            "'navigator', 'approved', :at)"
        ),
        {
            "id": ids["approval_decision"],
            "organization_id": ids["organization"],
            "proposal_id": ids["proposed_change"],
            "user_id": ids["user"],
            "assignment_id": uuid4(),
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    connection.execute(
        text(
            "INSERT INTO outcome "
            "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, "
            "disposition, idempotency_key, recorded_at) VALUES (:id, :organization_id, "
            ":patient_id, :need_id, :user_id, 'resolved', :key, :at)"
        ),
        {
            "id": ids["outcome"],
            "organization_id": ids["organization"],
            "patient_id": uuid4(),
            "need_id": uuid4(),
            "user_id": ids["user"],
            "key": str(uuid4()),
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    connection.execute(
        text(
            "INSERT INTO safety_signal_resolution "
            "(id, organization_id, safety_signal_id, resolved_by_user_id, resolved_at, "
            "resolution_reason) VALUES (:id, :organization_id, :signal_id, :user_id, :at, "
            "'Test resolution')"
        ),
        {
            "id": ids["safety_signal_resolution"],
            "organization_id": ids["organization"],
            "signal_id": uuid4(),
            "user_id": ids["user"],
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    connection.execute(
        text(
            "INSERT INTO audit_event "
            "(id, organization_id, actor_type, actor_system_component, actor_system_version, "
            "entity_type, entity_id, event_type, payload) VALUES (:id, :organization_id, "
            "'system', 'test-seeder', '1', 'test', :entity_id, 'seeded', '{}'::jsonb)"
        ),
        {
            "id": ids["audit_event"],
            "organization_id": ids["organization"],
            "entity_id": uuid4(),
        },
    )
    workflow_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO workflow_run "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, trace_id, "
            "initial_state, current_state, started_at) VALUES (:id, :organization_id, "
            ":patient_id, :episode_id, :submission_id, :trace_id, 'pending', 'running', :at)"
        ),
        {
            "id": workflow_id,
            "organization_id": ids["organization"],
            "patient_id": uuid4(),
            "episode_id": uuid4(),
            "submission_id": ids["check_in_submission"],
            "trace_id": str(uuid4()),
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    connection.execute(
        text(
            "INSERT INTO workflow_transition_event "
            "(id, organization_id, workflow_run_id, sequence_number, from_state, to_state, "
            "actor_type, actor_system_component, actor_system_version, reason, transitioned_at) "
            "VALUES (:id, :organization_id, :workflow_id, 1, 'pending', 'running', 'system', "
            "'test-seeder', '1', 'seeded', :at)"
        ),
        {
            "id": ids["workflow_transition_event"],
            "organization_id": ids["organization"],
            "workflow_id": workflow_id,
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    connection.execute(text("SET LOCAL session_replication_role = origin"))
    return ids


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_owner_cannot_bypass_any_append_only_trigger(
    connection: Connection,
    operation: str,
) -> None:
    """Production break: a table owner can rewrite one immutable clinical/audit record."""
    _require_task5_schema(connection)
    ids = _seed_append_only_rows(connection)
    for table in APPEND_ONLY_TABLES:
        statement = (
            f"UPDATE {table} SET id = id WHERE id = :id"
            if operation == "UPDATE"
            else f"DELETE FROM {table} WHERE id = :id"
        )
        with pytest.raises(DBAPIError, match="append-only"):
            with connection.begin_nested():
                connection.execute(text(statement), {"id": ids[table]})


def test_application_role_has_insert_read_but_no_mutation_privileges(
    connection: Connection,
) -> None:
    """Production break: ojcc_app receives UPDATE/DELETE on an append-only table."""
    _require_task5_schema(connection)
    ids = _seed_append_only_rows(connection)
    assert connection.scalar(
        text("SELECT has_schema_privilege('ojcc_app', 'public', 'CREATE')")
    ) is False
    for function_name in PRIVILEGED_TRIGGER_FUNCTIONS:
        assert connection.execute(
            text(
                "SELECT p.prosecdef, p.proconfig "
                "FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = :function_name "
                "AND p.pronargs = 0"
            ),
            {"function_name": function_name},
        ).one() == (True, ["search_path=pg_catalog, public, pg_temp"])
    for table in APPEND_ONLY_TABLES:
        assert connection.scalar(
            text("SELECT has_table_privilege('ojcc_app', :table, 'SELECT')"),
            {"table": f"public.{table}"},
        ) is True
        assert connection.scalar(
            text("SELECT has_table_privilege('ojcc_app', :table, 'INSERT')"),
            {"table": f"public.{table}"},
        ) is True
        assert connection.scalar(
            text("SELECT has_table_privilege('ojcc_app', :table, 'UPDATE')"),
            {"table": f"public.{table}"},
        ) is False
        assert connection.scalar(
            text("SELECT has_table_privilege('ojcc_app', :table, 'DELETE')"),
            {"table": f"public.{table}"},
        ) is False

    connection.execute(text("SET LOCAL ROLE ojcc_app"))
    try:
        for table in APPEND_ONLY_TABLES:
            for statement in (
                f"UPDATE {table} SET id = id WHERE id = :id",
                f"DELETE FROM {table} WHERE id = :id",
            ):
                with pytest.raises(DBAPIError, match="permission denied"):
                    with connection.begin_nested():
                        connection.execute(text(statement), {"id": ids[table]})
    finally:
        connection.execute(text("RESET ROLE"))


def _seed_application_role_operation(connection: Connection) -> dict[str, object]:
    """Create valid aggregate roots before exercising the deliberately narrow group role."""
    ids = {
        name: uuid4()
        for name in (
            "organization",
            "user",
            "patient",
            "pathway",
            "episode",
            "definition",
            "submission",
            "workflow",
            "signal",
            "need",
            "task",
            "role_assignment",
            "policy",
            "resource",
            "proposal",
            "task_resource",
        )
    }
    started_at = datetime(2026, 8, 18, tzinfo=UTC)
    connection.execute(
        text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
        {"id": ids["organization"], "name": f"Application role {uuid4()}"},
    )
    connection.execute(
        text(
            "INSERT INTO user_account (id, primary_organization_id, email, display_name, "
            "is_active) VALUES (:id, :organization_id, :email, 'Navigator', true)"
        ),
        {
            "id": ids["user"],
            "organization_id": ids["organization"],
            "email": f"{uuid4()}@example.test",
        },
    )
    connection.execute(
        text(
            "INSERT INTO synthetic_patient "
            "(id, organization_id, external_ref, display_name, demographics) "
            "VALUES (:id, :organization_id, :external_ref, 'Patient', '{}'::jsonb)"
        ),
        {
            "id": ids["patient"],
            "organization_id": ids["organization"],
            "external_ref": str(uuid4()),
        },
    )
    connection.execute(
        text(
            "INSERT INTO pathway_definition "
            "(id, organization_id, slug, version, name, configuration, is_active) "
            "VALUES (:id, :organization_id, :slug, 1, 'Pathway', '{}'::jsonb, true)"
        ),
        {
            "id": ids["pathway"],
            "organization_id": ids["organization"],
            "slug": str(uuid4()),
        },
    )
    connection.execute(
        text(
            "INSERT INTO care_episode "
            "(id, organization_id, patient_id, status, started_at) "
            "VALUES (:id, :organization_id, :patient_id, 'active', :started_at)"
        ),
        {
            "id": ids["episode"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "started_at": started_at,
        },
    )
    connection.execute(
        text(
            "INSERT INTO check_in_definition "
            "(id, organization_id, pathway_definition_id, slug, version, title, questionnaire) "
            "VALUES (:id, :organization_id, :pathway_id, :slug, 1, 'Check-in', '{}'::jsonb)"
        ),
        {
            "id": ids["definition"],
            "organization_id": ids["organization"],
            "pathway_id": ids["pathway"],
            "slug": str(uuid4()),
        },
    )
    connection.execute(
        text(
            "INSERT INTO check_in_submission "
            "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, "
            "status, answers, submission_source, submitted_by_user_id, submitted_at) "
            "VALUES (:id, :organization_id, :patient_id, :episode_id, :definition_id, "
            "'submitted', '{}'::jsonb, 'patient', :user_id, :submitted_at)"
        ),
        {
            "id": ids["submission"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "episode_id": ids["episode"],
            "definition_id": ids["definition"],
            "user_id": ids["user"],
            "submitted_at": started_at,
        },
    )
    connection.execute(
        text(
            "INSERT INTO workflow_run "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "trace_id, initial_state, current_state, started_at) "
            "VALUES (:id, :organization_id, :patient_id, :episode_id, :submission_id, "
            ":trace_id, 'pending', 'pending', :started_at)"
        ),
        {
            "id": ids["workflow"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "episode_id": ids["episode"],
            "submission_id": ids["submission"],
            "trace_id": str(uuid4()),
            "started_at": started_at,
        },
    )
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            "INSERT INTO reported_need "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "kind, status, evidence, created_at) VALUES (:id, :organization_id, "
            ":patient_id, :episode_id, :submission_id, 'transportation', 'open', "
            "'[]'::jsonb, :detected_at)"
        ),
        {
            "id": ids["need"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "episode_id": ids["episode"],
            "submission_id": ids["submission"],
            "detected_at": started_at,
        },
    )
    connection.execute(
        text(
            "INSERT INTO navigation_task "
            "(id, organization_id, patient_id, reported_need_id, title, status) "
            "VALUES (:id, :organization_id, :patient_id, :need_id, 'Arrange ride', 'open')"
        ),
        {
            "id": ids["task"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "need_id": ids["need"],
        },
    )
    connection.execute(
        text(
            "INSERT INTO safety_signal "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "signal_rule_id, signal_rule_version, deterministic_level, effective_level, "
            "status, evidence, acknowledged_by_user_id, acknowledged_at, created_at) "
            "VALUES (:id, :organization_id, :patient_id, :episode_id, :submission_id, "
            ":rule_id, 1, 'urgent', 'urgent', 'acknowledged', '[]'::jsonb, :user_id, :at, :at)"
        ),
        {
            "id": ids["signal"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "episode_id": ids["episode"],
            "submission_id": ids["submission"],
            "rule_id": uuid4(),
            "user_id": ids["user"],
            "at": started_at,
        },
    )
    connection.execute(text("SET LOCAL session_replication_role = origin"))
    connection.execute(
        text(
            "INSERT INTO role_assignment "
            "(id, organization_id, user_id, role, granted_at) "
            "VALUES (:id, :organization_id, :user_id, 'navigator', :granted_at)"
        ),
        {
            "id": ids["role_assignment"],
            "organization_id": ids["organization"],
            "user_id": ids["user"],
            "granted_at": started_at - timedelta(days=1),
        },
    )
    connection.execute(
        text(
            "INSERT INTO approval_policy "
            "(id, organization_id, change_type, version, effective_from, "
            "allow_self_approval, required_approval_count, required_approver_role) "
            "VALUES (:id, :organization_id, 'authorize_navigation_task', 1, "
            ":effective_from, true, 1, 'navigator')"
        ),
        {
            "id": ids["policy"],
            "organization_id": ids["organization"],
            "effective_from": started_at - timedelta(days=1),
        },
    )
    connection.execute(
        text(
            "INSERT INTO resource "
            "(id, organization_id, name, category, is_active, metadata) "
            "VALUES (:id, :organization_id, 'Ride Service', 'transportation', true, "
            "'{}'::jsonb)"
        ),
        {"id": ids["resource"], "organization_id": ids["organization"]},
    )
    connection.execute(
        text(
            "INSERT INTO proposed_change "
            "(id, organization_id, proposed_by_user_id, proposed_at, change_type, "
            "proposed_value, rationale, value_schema_id, value_schema_version, "
            "navigation_task_id, approval_policy_id, approval_policy_version, "
            "allow_self_approval_snapshot, required_approval_count_snapshot, "
            "required_approver_role_snapshot) VALUES (:id, :organization_id, :user_id, :at, "
            "'authorize_navigation_task', jsonb_build_object('title', 'Arrange ride', "
            "'resources', jsonb_build_array(jsonb_build_object('resource_id', "
            "CAST(:resource_id AS text), 'name', 'Ride Service', 'category', "
            "'transportation', 'url', NULL, 'metadata', '{}'::jsonb, 'match_rationale', "
            "'Matches need'))), 'Patient requested transport', "
            "'ojcc.authorize-navigation-task', 2, :task_id, :policy_id, 1, true, 1, "
            "'navigator')"
        ),
        {
            "id": ids["proposal"],
            "organization_id": ids["organization"],
            "user_id": ids["user"],
            "at": started_at,
            "resource_id": ids["resource"],
            "task_id": ids["task"],
            "policy_id": ids["policy"],
        },
    )
    connection.execute(
        text(
            "INSERT INTO navigation_task_resource "
            "(id, organization_id, navigation_task_id, resource_id, proposed_change_id, "
            "resource_name_snapshot, resource_category_snapshot, resource_metadata_snapshot, "
            "match_rationale_snapshot, proposed_at) VALUES (:id, :organization_id, :task_id, "
            ":resource_id, :proposal_id, 'Ride Service', 'transportation', '{}'::jsonb, "
            "'Matches need', :at)"
        ),
        {
            "id": ids["task_resource"],
            "organization_id": ids["organization"],
            "task_id": ids["task"],
            "resource_id": ids["resource"],
            "proposal_id": ids["proposal"],
            "at": started_at,
        },
    )
    ids["started_at"] = started_at
    return ids


@pytest.mark.parametrize(
    "operation",
    ["transition", "approval", "submission", "resolution", "outcome"],
)
def test_application_role_can_execute_valid_triggered_inserts(
    connection: Connection,
    operation: str,
) -> None:
    """Production break: an invoker trigger reads or writes tables unavailable to ojcc_app."""
    _require_task5_schema(connection)
    ids = _seed_application_role_operation(connection)
    started_at = ids["started_at"]
    statements: dict[str, tuple[str, dict[str, object]]] = {
        "transition": (
            "INSERT INTO workflow_transition_event "
            "(id, organization_id, workflow_run_id, sequence_number, from_state, to_state, "
            "actor_type, actor_system_component, actor_system_version, reason, transitioned_at) "
            "VALUES (:id, :organization_id, :workflow_id, 1, 'pending', 'running', "
            "'system', 'workflow-coordinator', '1', 'started', :at)",
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "workflow_id": ids["workflow"],
                "at": started_at + timedelta(minutes=1),
            },
        ),
        "approval": (
            "INSERT INTO approval_decision "
            "(id, organization_id, proposed_change_id, authorized_by_user_id, "
            "qualifying_role_assignment_id, qualifying_role_snapshot, decision, authorized_at) "
            "VALUES (:id, :organization_id, :proposal_id, :user_id, :assignment_id, "
            "'navigator', 'approved', :at)",
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "proposal_id": ids["proposal"],
                "user_id": ids["user"],
                "assignment_id": ids["role_assignment"],
                "at": started_at + timedelta(minutes=1),
            },
        ),
        "submission": (
            "INSERT INTO check_in_submission "
            "(id, organization_id, patient_id, care_episode_id, check_in_definition_id, "
            "status, answers, submission_source, submitted_by_user_id, submitted_at) "
            "VALUES (:id, :organization_id, :patient_id, :episode_id, :definition_id, "
            "'submitted', '{}'::jsonb, 'patient', :user_id, :at)",
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "patient_id": ids["patient"],
                "episode_id": ids["episode"],
                "definition_id": ids["definition"],
                "user_id": ids["user"],
                "at": started_at + timedelta(minutes=1),
            },
        ),
        "resolution": (
            "INSERT INTO safety_signal_resolution "
            "(id, organization_id, safety_signal_id, resolved_by_user_id, resolved_at, "
            "resolution_reason) VALUES (:id, :organization_id, :signal_id, :user_id, :at, "
            "'Navigator resolved')",
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "signal_id": ids["signal"],
                "user_id": ids["user"],
                "at": started_at + timedelta(minutes=1),
            },
        ),
        "outcome": (
            "INSERT INTO outcome "
            "(id, organization_id, patient_id, reported_need_id, recorded_by_user_id, "
            "disposition, idempotency_key, recorded_at) VALUES (:id, :organization_id, "
            ":patient_id, :need_id, :user_id, 'resolved', :key, :at)",
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "patient_id": ids["patient"],
                "need_id": ids["need"],
                "user_id": ids["user"],
                "key": str(uuid4()),
                "at": started_at + timedelta(minutes=1),
            },
        ),
    }
    statement, parameters = statements[operation]
    connection.execute(text("SET LOCAL ROLE ojcc_app"))
    try:
        with connection.begin_nested():
            connection.execute(text(statement), parameters)
    finally:
        connection.execute(text("RESET ROLE"))

    if operation == "transition":
        assert connection.scalar(
            text("SELECT current_state FROM workflow_run WHERE id = :id"),
            {"id": ids["workflow"]},
        ) == "running"
    elif operation == "approval":
        assert connection.scalar(
            text("SELECT approved_at FROM navigation_task_resource WHERE id = :id"),
            {"id": ids["task_resource"]},
        ) == started_at + timedelta(minutes=1)
    elif operation == "outcome":
        assert connection.scalar(
            text("SELECT status FROM navigation_task WHERE id = :id"),
            {"id": ids["task"]},
        ) == "cancelled"
        assert connection.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE entity_id = :task_id "
                "AND event_type = 'task_cancelled_by_closure'"
            ),
            {"task_id": ids["task"]},
        ) == 1


def test_security_definer_trigger_ignores_pg_temp_relation_shadow(
    connection: Connection,
) -> None:
    """Production break: pg_temp shadows a governed relation in a definer trigger."""
    _require_task5_schema(connection)
    ids = _seed_application_role_operation(connection)
    transitioned_at = ids["started_at"] + timedelta(minutes=1)

    connection.execute(text("SET LOCAL ROLE ojcc_app"))
    try:
        connection.execute(
            text(
                "CREATE TEMP TABLE workflow_run ("
                "id uuid PRIMARY KEY, organization_id uuid NOT NULL, "
                "current_state text NOT NULL, started_at timestamptz NOT NULL, "
                "updated_at timestamptz) ON COMMIT DROP"
            )
        )
        connection.execute(
            text(
                "INSERT INTO pg_temp.workflow_run "
                "(id, organization_id, current_state, started_at) "
                "VALUES (:id, :organization_id, 'pending', :started_at)"
            ),
            {
                "id": ids["workflow"],
                "organization_id": ids["organization"],
                "started_at": ids["started_at"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO public.workflow_transition_event "
                "(id, organization_id, workflow_run_id, sequence_number, from_state, "
                "to_state, actor_type, actor_system_component, actor_system_version, "
                "reason, transitioned_at) VALUES (:id, :organization_id, :workflow_id, "
                "1, 'pending', 'running', 'system', 'workflow-coordinator', '1', "
                "'started', :transitioned_at)"
            ),
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "workflow_id": ids["workflow"],
                "transitioned_at": transitioned_at,
            },
        )
    finally:
        connection.execute(text("RESET ROLE"))

    assert connection.execute(
        text(
            "SELECT "
            "(SELECT current_state FROM public.workflow_run WHERE id = :id), "
            "(SELECT current_state FROM pg_temp.workflow_run WHERE id = :id)"
        ),
        {"id": ids["workflow"]},
    ).one() == ("running", "pending")


def _validate_local_url(url: URL) -> None:
    if url.get_backend_name() != "postgresql" or url.host not in LOOPBACK_HOSTS:
        raise ValueError("Task 5 migration tests require loopback PostgreSQL")


@contextmanager
def _disposable_database() -> Iterator[str]:
    configured = make_url(settings.database_url)
    _validate_local_url(configured)
    disposable = configured.set(database=f"{DISPOSABLE_PREFIX}{uuid4().hex}")
    admin = disposable.set(database="postgres")
    database = disposable.database
    assert database is not None and database.startswith(DISPOSABLE_PREFIX)
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    created = False
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        yield disposable.render_as_string(hide_password=False)
    finally:
        if created:
            with engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :database AND pid <> pg_backend_pid()"
                    ),
                    {"database": database},
                )
                connection.execute(text(f'DROP DATABASE "{database}"'))
        engine.dispose()


def _alembic(
    database_url: str, revision: str, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=PROJECT_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=check,
        capture_output=True,
        text=True,
    )


def _alembic_check(database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "services/api/alembic.ini", "check"],
        cwd=PROJECT_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
    )


def _alembic_downgrade(
    database_url: str, revision: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            "downgrade",
            revision,
        ],
        cwd=PROJECT_ROOT,
        env=os.environ | {"DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
    )


def test_empty_upgrade_reaches_0005_with_metadata_parity() -> None:
    """Production break: a fresh database misses Task 5 DDL or Alembic/ORM parity."""
    with _disposable_database() as database_url:
        _alembic(database_url, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert TASK5_TABLES <= set(inspect(connection).get_table_names())
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0005_workflow_knowledge_audit"
                )
            result = _alembic_check(database_url)
        finally:
            engine.dispose()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new upgrade operations detected" in result.stdout


def test_populated_upgrade_preserves_task_closure_audit_actor_and_payload() -> None:
    """Production break: actor reconciliation drops or fabricates Task 3 closure lineage."""
    with _disposable_database() as database_url:
        _alembic(database_url, "0004_safety_approval_lifecycle")
        ids = {"organization": uuid4(), "user": uuid4(), "event": uuid4(), "task": uuid4()}
        created_at = datetime(2026, 8, 18, tzinfo=UTC)
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                {"id": ids["organization"], "name": f"Closure {uuid4()}"},
            )
            connection.execute(
                text(
                    "INSERT INTO user_account (id, email, display_name, is_active) "
                    "VALUES (:id, :email, 'Closer', true)"
                ),
                {"id": ids["user"], "email": f"{uuid4()}@example.test"},
            )
            connection.execute(
                text(
                    "INSERT INTO audit_event "
                    "(id, organization_id, actor_user_id, entity_type, entity_id, event_type, "
                    "payload, created_at) VALUES (:id, :organization_id, :user_id, "
                    "'navigation_task', :task_id, 'task_cancelled_by_closure', "
                    "'{\"outcome_id\":\"preserved\","
                    "\"cancellation_reason\":\"need_closed\"}'::jsonb, "
                    ":created_at)"
                ),
                {
                    "id": ids["event"],
                    "organization_id": ids["organization"],
                    "user_id": ids["user"],
                    "task_id": ids["task"],
                    "created_at": created_at,
                },
            )
        engine.dispose()
        _alembic(database_url, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT actor_type, actor_user_id, actor_agent_run_id, payload, "
                        "created_at FROM audit_event WHERE id = :id"
                    ),
                    {"id": ids["event"]},
                ).mappings().one()
        finally:
            engine.dispose()
    assert row == {
        "actor_type": "user",
        "actor_user_id": ids["user"],
        "actor_agent_run_id": None,
        "payload": {"outcome_id": "preserved", "cancellation_reason": "need_closed"},
        "created_at": created_at,
    }


def test_populated_upgrade_refuses_ambiguous_audit_actor_without_inventing_provenance() -> None:
    """Production break: a legacy actorless event is silently attributed during migration."""
    with _disposable_database() as database_url:
        _alembic(database_url, "0004_safety_approval_lifecycle")
        organization_id = uuid4()
        event_id = uuid4()
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                {"id": organization_id, "name": f"Ambiguous {uuid4()}"},
            )
            connection.execute(
                text(
                    "INSERT INTO audit_event "
                    "(id, organization_id, entity_type, entity_id, event_type, payload) "
                    "VALUES (:id, :organization_id, 'legacy', :entity_id, 'legacy_event', "
                    "'{}'::jsonb)"
                ),
                {"id": event_id, "organization_id": organization_id, "entity_id": uuid4()},
            )
        engine.dispose()
        result = _alembic(database_url, "head", check=False)

    assert result.returncode != 0
    diagnostic = result.stdout + result.stderr
    assert str(event_id) in diagnostic
    assert "cannot infer audit actor provenance" in diagnostic
    assert "reset the synthetic demo database" in diagnostic


def test_populated_upgrade_refuses_non_draft_knowledge_without_inventing_provenance() -> None:
    """Production break: legacy approved knowledge is reset without approval provenance."""
    with _disposable_database() as database_url:
        _alembic(database_url, "0004_safety_approval_lifecycle")
        organization_id = uuid4()
        document_id = uuid4()
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
                {"id": organization_id, "name": f"Non-draft knowledge {uuid4()}"},
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_document "
                    "(id, organization_id, title, version, status, content, citations) "
                    "VALUES (:id, :organization_id, 'Legacy approved guide', '1', "
                    "'approved', 'Approved content', '[]'::jsonb)"
                ),
                {"id": document_id, "organization_id": organization_id},
            )
        engine.dispose()
        result = _alembic(database_url, "head", check=False)

    assert result.returncode != 0
    diagnostic = result.stdout + result.stderr
    assert str(document_id) in diagnostic
    assert "cannot infer approval or withdrawal provenance" in diagnostic
    assert "reset the synthetic demo database" in diagnostic


def test_0005_downgrade_refuses_before_any_teardown_ddl() -> None:
    """Production break: irreversible downgrade drops Task 5 lineage before refusing."""
    with _disposable_database() as database_url:
        _alembic(database_url, "head")
        result = _alembic_downgrade(database_url, "0004_safety_approval_lifecycle")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                tables_after_refusal = set(inspect(connection).get_table_names())
                revision_after_refusal = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
        finally:
            engine.dispose()

    assert result.returncode != 0
    diagnostic = result.stdout + result.stderr
    assert "Downgrading 0005 would discard workflow, knowledge, resource, and audit lineage" in (
        diagnostic
    )
    assert "reset the synthetic demo database instead" in diagnostic
    assert TASK5_TABLES <= tables_after_refusal
    assert revision_after_refusal == "0005_workflow_knowledge_audit"
