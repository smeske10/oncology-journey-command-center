from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.db.models import Base

TASK5_WORKFLOW_TABLES = {
    "workflow_run",
    "workflow_transition_event",
    "manual_review_task",
}


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
    """Production break: migration 0005 omitted one or more workflow lineage tables."""
    assert TASK5_WORKFLOW_TABLES <= set(inspect(connection).get_table_names())


def _seed_workflow_source(connection: Connection) -> dict[str, UUID]:
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
        )
    }
    now = datetime(2026, 8, 18, tzinfo=UTC)
    connection.execute(
        text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
        {"id": ids["organization"], "name": f"Workflow {uuid4()}"},
    )
    connection.execute(
        text(
            "INSERT INTO user_account (id, email, display_name, is_active) "
            "VALUES (:id, :email, 'Workflow User', true)"
        ),
        {"id": ids["user"], "email": f"{uuid4()}@example.test"},
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
            "VALUES (:id, :organization_id, :patient_id, 'active', :now)"
        ),
        {
            "id": ids["episode"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "now": now,
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
            "'submitted', '{}'::jsonb, 'patient', :user_id, :now)"
        ),
        {
            "id": ids["submission"],
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "episode_id": ids["episode"],
            "definition_id": ids["definition"],
            "user_id": ids["user"],
            "now": now,
        },
    )
    return ids


def _insert_workflow(connection: Connection, ids: dict[str, UUID]) -> UUID:
    workflow_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO workflow_run "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "trace_id, initial_state, current_state, started_at) "
            "VALUES (:id, :organization_id, :patient_id, :episode_id, :submission_id, "
            ":trace_id, 'pending', 'pending', :started_at)"
        ),
        {
            "id": workflow_id,
            "organization_id": ids["organization"],
            "patient_id": ids["patient"],
            "episode_id": ids["episode"],
            "submission_id": ids["submission"],
            "trace_id": str(uuid4()),
            "started_at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    return workflow_id


def test_workflow_metadata_owns_tenant_safe_ordered_lineage() -> None:
    """Production break: workflow lineage loses an ordered, tenant-scoped parent edge."""
    assert TASK5_WORKFLOW_TABLES <= set(Base.metadata.tables)
    workflow = Base.metadata.tables["workflow_run"]
    transition = Base.metadata.tables["workflow_transition_event"]
    agent_run = Base.metadata.tables["agent_run"]
    review = Base.metadata.tables["manual_review_task"]

    assert {
        "patient_id",
        "care_episode_id",
        "source_submission_id",
        "reported_need_id",
        "trace_id",
        "initial_state",
        "current_state",
    } <= set(workflow.c.keys())
    assert {
        "workflow_run_id",
        "sequence_number",
        "from_state",
        "to_state",
        "actor_type",
        "reason",
        "transitioned_at",
    } <= set(transition.c.keys())
    assert {"workflow_run_id", "workflow_transition_event_id"} <= set(agent_run.c.keys())
    assert {
        "workflow_run_id",
        "agent_run_id",
        "failure_reason",
        "retry_context",
        "state",
        "assignee_user_id",
        "resolved_by_user_id",
        "resolved_at",
        "resolution",
    } <= set(review.c.keys())

    transition_foreign_keys = {
        constraint.referred_table.name: tuple(constraint.column_keys)
        for constraint in transition.foreign_key_constraints
    }
    agent_foreign_keys = {
        constraint.referred_table.name: tuple(constraint.column_keys)
        for constraint in agent_run.foreign_key_constraints
    }
    assert transition_foreign_keys["workflow_run"][:2] == (
        "organization_id",
        "workflow_run_id",
    )
    assert agent_foreign_keys["workflow_transition_event"][:2] == (
        "organization_id",
        "workflow_transition_event_id",
    )
    assert not any(
        constraint.columns[0].name == "workflow_transition_event_id"
        for constraint in agent_run.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )


def test_transition_events_advance_materialized_state_in_strict_order(
    connection: Connection,
) -> None:
    """Production break: an event can skip sequence order or diverge from current state."""
    _require_task5_schema(connection)
    ids = _seed_workflow_source(connection)
    workflow_id = _insert_workflow(connection, ids)
    first_at = datetime(2026, 8, 18, 0, 1, tzinfo=UTC)

    for sequence, from_state, to_state, transitioned_at in (
        (1, "pending", "running", first_at),
        (2, "running", "completed", first_at + timedelta(minutes=1)),
    ):
        connection.execute(
            text(
                "INSERT INTO workflow_transition_event "
                "(id, organization_id, workflow_run_id, sequence_number, from_state, "
                "to_state, actor_type, actor_system_component, actor_system_version, "
                "reason, transitioned_at) VALUES (:id, :organization_id, :workflow_id, "
                ":sequence, :from_state, :to_state, 'system', 'workflow-coordinator', "
                "'1', :reason, :transitioned_at)"
            ),
            {
                "id": uuid4(),
                "organization_id": ids["organization"],
                "workflow_id": workflow_id,
                "sequence": sequence,
                "from_state": from_state,
                "to_state": to_state,
                "reason": f"transition {sequence}",
                "transitioned_at": transitioned_at,
            },
        )

    assert connection.scalar(
        text("SELECT current_state FROM workflow_run WHERE id = :id"),
        {"id": workflow_id},
    ) == "completed"
    assert connection.execute(
        text(
            "SELECT sequence_number, from_state, to_state "
            "FROM workflow_transition_event WHERE workflow_run_id = :id "
            "ORDER BY sequence_number"
        ),
        {"id": workflow_id},
    ).all() == [(1, "pending", "running"), (2, "running", "completed")]

    with pytest.raises(DBAPIError, match="next contiguous sequence"):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO workflow_transition_event "
                    "(id, organization_id, workflow_run_id, sequence_number, from_state, "
                    "to_state, actor_type, actor_system_component, actor_system_version, "
                    "reason, transitioned_at) VALUES (:id, :organization_id, :workflow_id, "
                    "4, 'completed', 'running', 'system', 'workflow-coordinator', '1', "
                    "'invalid gap', :transitioned_at)"
                ),
                {
                    "id": uuid4(),
                    "organization_id": ids["organization"],
                    "workflow_id": workflow_id,
                    "transitioned_at": first_at + timedelta(minutes=2),
                },
            )


def test_materialized_workflow_state_cannot_change_without_an_event(
    connection: Connection,
) -> None:
    """Production break: current workflow state can diverge from its event stream."""
    _require_task5_schema(connection)
    ids = _seed_workflow_source(connection)
    workflow_id = _insert_workflow(connection, ids)

    with pytest.raises(DBAPIError, match="only workflow transition events"):
        with connection.begin_nested():
            connection.execute(
                text("UPDATE workflow_run SET current_state = 'completed' WHERE id = :id"),
                {"id": workflow_id},
            )


@pytest.mark.parametrize(
    ("column_name", "replacement"),
    [
        ("id", uuid4),
        ("organization_id", uuid4),
        ("patient_id", uuid4),
        ("care_episode_id", uuid4),
        ("source_submission_id", uuid4),
        ("reported_need_id", uuid4),
        ("trace_id", lambda: str(uuid4())),
        ("initial_state", lambda: "rewritten"),
        ("started_at", lambda: datetime(2026, 8, 19, tzinfo=UTC)),
    ],
)
def test_workflow_owner_cannot_mutate_any_durable_identity_field(
    connection: Connection,
    column_name: str,
    replacement: Callable[[], object],
) -> None:
    """Production break: one durable WorkflowRun identity field is rewritable by its owner."""
    _require_task5_schema(connection)
    ids = _seed_workflow_source(connection)
    workflow_id = _insert_workflow(connection, ids)
    replacement_value = replacement()

    with pytest.raises(DBAPIError, match="identity and source are immutable"):
        with connection.begin_nested():
            connection.execute(
                text(
                    f"UPDATE workflow_run SET {column_name} = :replacement "
                    "WHERE id = :workflow_id"
                ),
                {"replacement": replacement_value, "workflow_id": workflow_id},
            )


def test_one_transition_can_own_multiple_agent_runs_and_operational_review(
    connection: Connection,
) -> None:
    """Production break: agent lineage becomes one-to-one or failure creates patient work."""
    _require_task5_schema(connection)
    ids = _seed_workflow_source(connection)
    workflow_id = _insert_workflow(connection, ids)
    transition_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO workflow_transition_event "
            "(id, organization_id, workflow_run_id, sequence_number, from_state, to_state, "
            "actor_type, actor_system_component, actor_system_version, reason, transitioned_at) "
            "VALUES (:id, :organization_id, :workflow_id, 1, 'pending', 'manual_review', "
            "'system', 'workflow-coordinator', '1', 'dead letter', :transitioned_at)"
        ),
        {
            "id": transition_id,
            "organization_id": ids["organization"],
            "workflow_id": workflow_id,
            "transitioned_at": datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
        },
    )
    agent_ids = [uuid4(), uuid4()]
    for index, agent_id in enumerate(agent_ids):
        connection.execute(
            text(
                "INSERT INTO agent_run "
                "(id, organization_id, patient_id, source_submission_id, trace_id, "
                "agent_name, status, input_payload, output_payload, validation, created_at, "
                "workflow_run_id, workflow_transition_event_id) "
                "VALUES (:id, :organization_id, :patient_id, :submission_id, :trace_id, "
                ":agent_name, :status, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, :created_at, "
                ":workflow_id, :transition_id)"
            ),
            {
                "id": agent_id,
                "organization_id": ids["organization"],
                "patient_id": ids["patient"],
                "submission_id": ids["submission"],
                "trace_id": str(uuid4()),
                "agent_name": f"agent-{index}",
                "status": "failed" if index == 0 else "succeeded",
                "created_at": datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
                "workflow_id": workflow_id,
                "transition_id": transition_id,
            },
        )

    review_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO manual_review_task "
            "(id, organization_id, workflow_run_id, agent_run_id, failure_reason, "
            "retry_context, state, created_at) VALUES (:id, :organization_id, :workflow_id, "
            ":agent_run_id, 'dead-lettered after bounded retries', "
                "jsonb_build_object('attempts', 3), 'open', :created_at)"
        ),
        {
            "id": review_id,
            "organization_id": ids["organization"],
            "workflow_id": workflow_id,
            "agent_run_id": agent_ids[0],
            "created_at": datetime(2026, 8, 18, 0, 2, tzinfo=UTC),
        },
    )

    assert connection.scalar(
        text(
            "SELECT count(*) FROM agent_run "
            "WHERE organization_id = :organization_id "
            "AND workflow_transition_event_id = :transition_id"
        ),
        {"organization_id": ids["organization"], "transition_id": transition_id},
    ) == 2
    assert connection.execute(
        text(
            "SELECT agent_run_id, state, retry_context FROM manual_review_task "
            "WHERE id = :id"
        ),
        {"id": review_id},
    ).one() == (agent_ids[0], "open", {"attempts": 3})
    assert connection.scalar(
        text(
            "SELECT count(*) FROM navigation_task "
            "WHERE organization_id = :organization_id"
        ),
        {"organization_id": ids["organization"]},
    ) == 0


def test_live_postgresql_rejects_cross_tenant_workflow_edges(
    connection: Connection,
) -> None:
    """Production break: a Task 5 workflow child crosses a composite tenant boundary."""
    _require_task5_schema(connection)
    first = _seed_workflow_source(connection)
    second = _seed_workflow_source(connection)
    first_workflow = _insert_workflow(connection, first)
    second_workflow = _insert_workflow(connection, second)
    first_transition = uuid4()
    second_transition = uuid4()
    for ids, workflow_id, transition_id in (
        (first, first_workflow, first_transition),
        (second, second_workflow, second_transition),
    ):
        connection.execute(
            text(
                "INSERT INTO workflow_transition_event "
                "(id, organization_id, workflow_run_id, sequence_number, from_state, "
                "to_state, actor_type, actor_system_component, actor_system_version, "
                "reason, transitioned_at) VALUES (:id, :organization_id, :workflow_id, "
                "1, 'pending', 'running', 'system', 'test', '1', 'started', :at)"
            ),
            {
                "id": transition_id,
                "organization_id": ids["organization"],
                "workflow_id": workflow_id,
                "at": datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
            },
        )
    first_agent = uuid4()
    second_agent = uuid4()
    for ids, workflow_id, transition_id, agent_id in (
        (first, first_workflow, first_transition, first_agent),
        (second, second_workflow, second_transition, second_agent),
    ):
        connection.execute(
            text(
                "INSERT INTO agent_run "
                "(id, organization_id, trace_id, agent_name, status, input_payload, "
                "output_payload, validation, created_at, workflow_run_id, "
                "workflow_transition_event_id) VALUES (:id, :organization_id, :trace_id, "
                "'worker', 'failed', '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, :at, "
                ":workflow_id, :transition_id)"
            ),
            {
                "id": agent_id,
                "organization_id": ids["organization"],
                "trace_id": str(uuid4()),
                "at": datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
                "workflow_id": workflow_id,
                "transition_id": transition_id,
            },
        )
    second_need = uuid4()
    connection.execute(
        text(
            "INSERT INTO reported_need "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "kind, status, evidence, created_at) VALUES (:id, :organization_id, "
            ":patient_id, :episode_id, :submission_id, 'transportation', 'open', "
            "'[]'::jsonb, :at)"
        ),
        {
            "id": second_need,
            "organization_id": second["organization"],
            "patient_id": second["patient"],
            "episode_id": second["episode"],
            "submission_id": second["submission"],
            "at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )

    cases = (
        (
            "workflow_run",
            "INSERT INTO workflow_run "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "trace_id, initial_state, current_state, started_at) VALUES (:id, :org, "
            ":patient, :episode, :submission, :trace, 'pending', 'pending', :at)",
            {
                "id": uuid4(),
                "org": first["organization"],
                "patient": second["patient"],
                "episode": second["episode"],
                "submission": second["submission"],
                "trace": str(uuid4()),
                "at": datetime(2026, 8, 18, tzinfo=UTC),
            },
        ),
        (
            "workflow_run",
            "INSERT INTO workflow_run "
            "(id, organization_id, patient_id, care_episode_id, source_submission_id, "
            "trace_id, initial_state, current_state, started_at) VALUES (:id, :org, "
            ":patient, :episode, :submission, :trace, 'pending', 'pending', :at)",
            {
                "id": uuid4(),
                "org": first["organization"],
                "patient": first["patient"],
                "episode": first["episode"],
                "submission": second["submission"],
                "trace": str(uuid4()),
                "at": datetime(2026, 8, 18, tzinfo=UTC),
            },
        ),
        (
            "workflow_run",
            "INSERT INTO workflow_run "
            "(id, organization_id, patient_id, care_episode_id, reported_need_id, trace_id, "
            "initial_state, current_state, started_at) VALUES (:id, :org, :patient, "
            ":episode, :need, :trace, 'pending', 'pending', :at)",
            {
                "id": uuid4(),
                "org": first["organization"],
                "patient": first["patient"],
                "episode": first["episode"],
                "need": second_need,
                "trace": str(uuid4()),
                "at": datetime(2026, 8, 18, tzinfo=UTC),
            },
        ),
        (
            "workflow_transition_event",
            "INSERT INTO workflow_transition_event "
            "(id, organization_id, workflow_run_id, sequence_number, from_state, to_state, "
            "actor_type, actor_system_component, actor_system_version, reason, transitioned_at) "
            "VALUES (:id, :org, :workflow, 2, 'running', 'done', 'system', 'test', '1', "
            "'cross tenant', :at)",
            {
                "id": uuid4(),
                "org": first["organization"],
                "workflow": second_workflow,
                "at": datetime(2026, 8, 18, 0, 2, tzinfo=UTC),
            },
        ),
        (
            "workflow_transition_event",
            "INSERT INTO workflow_transition_event "
            "(id, organization_id, workflow_run_id, sequence_number, from_state, to_state, "
            "actor_type, actor_agent_run_id, reason, transitioned_at) VALUES (:id, :org, "
            ":workflow, 2, 'running', 'done', 'agent', :agent, 'cross tenant', :at)",
            {
                "id": uuid4(),
                "org": first["organization"],
                "workflow": first_workflow,
                "agent": second_agent,
                "at": datetime(2026, 8, 18, 0, 2, tzinfo=UTC),
            },
        ),
        (
            "agent_run",
            "INSERT INTO agent_run "
            "(id, organization_id, trace_id, agent_name, status, input_payload, "
            "output_payload, validation, workflow_run_id, workflow_transition_event_id) "
            "VALUES (:id, :org, :trace, 'worker', 'succeeded', '{}'::jsonb, '{}'::jsonb, "
            "'{}'::jsonb, :workflow, :transition)",
            {
                "id": uuid4(),
                "org": first["organization"],
                "trace": str(uuid4()),
                "workflow": first_workflow,
                "transition": second_transition,
            },
        ),
        (
            "manual_review_task",
            "INSERT INTO manual_review_task "
            "(id, organization_id, workflow_run_id, agent_run_id, failure_reason, "
            "retry_context, state) VALUES (:id, :org, :workflow, :agent, 'failed', "
            "'{}'::jsonb, 'open')",
            {
                "id": uuid4(),
                "org": first["organization"],
                "workflow": first_workflow,
                "agent": second_agent,
            },
        ),
    )
    disabled_tables: set[str] = set()
    try:
        for table_name, statement, parameters in cases:
            if table_name not in disabled_tables:
                connection.execute(text(f"ALTER TABLE {table_name} DISABLE TRIGGER USER"))
                disabled_tables.add(table_name)
            with pytest.raises(DBAPIError, match="foreign key"):
                with connection.begin_nested():
                    connection.execute(text(statement), parameters)
    finally:
        for table_name in disabled_tables:
            connection.execute(text(f"ALTER TABLE {table_name} ENABLE TRIGGER USER"))
