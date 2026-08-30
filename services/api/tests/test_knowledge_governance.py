from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from app.config import settings
from app.db.models import Base

TASK5_KNOWLEDGE_TABLES = {
    "navigation_task_resource",
    "organization_knowledge_approval",
    "agent_run_citation",
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
    """Production break: migration 0005 omitted a governed knowledge table."""
    assert TASK5_KNOWLEDGE_TABLES <= set(inspect(connection).get_table_names())


def _seed_organization_user(connection: Connection) -> dict[str, UUID]:
    ids = {"organization": uuid4(), "user": uuid4()}
    connection.execute(
        text("INSERT INTO organization (id, name) VALUES (:id, :name)"),
        {"id": ids["organization"], "name": f"Knowledge {uuid4()}"},
    )
    connection.execute(
        text(
            "INSERT INTO user_account (id, email, display_name, is_active) "
            "VALUES (:id, :email, 'Knowledge Reviewer', true)"
        ),
        {"id": ids["user"], "email": f"{uuid4()}@example.test"},
    )
    return ids


def test_knowledge_metadata_preserves_exact_versions_and_proposal_authority() -> None:
    """Production break: a citation or resource match loses exact tenant/version authority."""
    assert TASK5_KNOWLEDGE_TABLES <= set(Base.metadata.tables)
    document = Base.metadata.tables["knowledge_document"]
    approval = Base.metadata.tables["organization_knowledge_approval"]
    citation = Base.metadata.tables["agent_run_citation"]
    task_resource = Base.metadata.tables["navigation_task_resource"]

    assert "status" not in document.c
    assert "reviewed_at" not in document.c
    assert {
        "knowledge_document_id",
        "knowledge_document_version",
        "approved_by_user_id",
        "approved_at",
        "effective_from",
        "withdrawn_at",
        "withdrawn_by_user_id",
        "withdrawal_reason",
    } <= set(approval.c.keys())
    assert {
        "agent_run_id",
        "knowledge_document_id",
        "knowledge_document_version",
        "passage",
        "cited_at",
    } <= set(citation.c.keys())
    assert {
        "navigation_task_id",
        "resource_id",
        "proposed_change_id",
        "resource_name_snapshot",
        "resource_category_snapshot",
        "resource_url_snapshot",
        "resource_metadata_snapshot",
        "match_rationale_snapshot",
        "approved_at",
        "delivered_at",
        "delivered_by_user_id",
    } <= set(task_resource.c.keys())

    approval_fks = {
        constraint.referred_table.name: tuple(constraint.column_keys)
        for constraint in approval.foreign_key_constraints
    }
    citation_fks = {
        constraint.referred_table.name: tuple(constraint.column_keys)
        for constraint in citation.foreign_key_constraints
    }
    resource_fks = {
        constraint.referred_table.name: tuple(constraint.column_keys)
        for constraint in task_resource.foreign_key_constraints
    }
    assert approval_fks["knowledge_document"] == (
        "organization_id",
        "knowledge_document_id",
        "knowledge_document_version",
    )
    assert citation_fks["knowledge_document"] == (
        "organization_id",
        "knowledge_document_id",
        "knowledge_document_version",
    )
    assert citation_fks["agent_run"] == ("organization_id", "agent_run_id")
    assert resource_fks["navigation_task"] == (
        "organization_id",
        "navigation_task_id",
    )
    assert resource_fks["resource"] == ("organization_id", "resource_id")
    assert resource_fks["proposed_change"] == (
        "organization_id",
        "proposed_change_id",
    )


def test_withdrawal_blocks_new_citations_but_preserves_historical_evidence(
    connection: Connection,
) -> None:
    """Production break: withdrawal either permits future use or erases historical evidence."""
    _require_task5_schema(connection)
    ids = _seed_organization_user(connection)
    ids.update({"resource": uuid4(), "document": uuid4(), "approval": uuid4()})
    approved_at = datetime(2026, 8, 18, tzinfo=UTC)
    connection.execute(
        text(
            "INSERT INTO resource "
            "(id, organization_id, name, category, is_active, metadata) "
            "VALUES (:id, :organization_id, 'Transportation guide', 'transportation', "
            "true, '{}'::jsonb)"
        ),
        {"id": ids["resource"], "organization_id": ids["organization"]},
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_document "
            "(id, organization_id, resource_id, title, version, content, citations) "
            "VALUES (:id, :organization_id, :resource_id, 'Transit guide', '2026.08', "
            "'Call the transportation desk.', '[]'::jsonb)"
        ),
        {
            "id": ids["document"],
            "organization_id": ids["organization"],
            "resource_id": ids["resource"],
        },
    )
    connection.execute(
        text(
            "INSERT INTO organization_knowledge_approval "
            "(id, organization_id, knowledge_document_id, knowledge_document_version, "
            "approved_by_user_id, approved_at, effective_from) "
            "VALUES (:id, :organization_id, :document_id, '2026.08', :user_id, "
            ":approved_at, :effective_from)"
        ),
        {
            "id": ids["approval"],
            "organization_id": ids["organization"],
            "document_id": ids["document"],
            "user_id": ids["user"],
            "approved_at": approved_at,
            "effective_from": approved_at,
        },
    )

    first_run = uuid4()
    citation_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO agent_run "
            "(id, organization_id, trace_id, agent_name, status, input_payload, "
            "output_payload, validation, created_at) VALUES (:id, :organization_id, "
            ":trace_id, 'knowledge-retriever', 'succeeded', '{}'::jsonb, '{}'::jsonb, "
            "'{}'::jsonb, :created_at)"
        ),
        {
            "id": first_run,
            "organization_id": ids["organization"],
            "trace_id": str(uuid4()),
            "created_at": approved_at + timedelta(minutes=1),
        },
    )
    connection.execute(
        text(
            "INSERT INTO agent_run_citation "
            "(id, organization_id, agent_run_id, knowledge_document_id, "
            "knowledge_document_version, passage, cited_at) "
            "VALUES (:id, :organization_id, :agent_run_id, :document_id, '2026.08', "
            "'Call the transportation desk.', :cited_at)"
        ),
        {
            "id": citation_id,
            "organization_id": ids["organization"],
            "agent_run_id": first_run,
            "document_id": ids["document"],
            "cited_at": approved_at + timedelta(minutes=1),
        },
    )

    withdrawn_at = approved_at + timedelta(minutes=2)
    connection.execute(
        text(
            "UPDATE organization_knowledge_approval SET withdrawn_at = :withdrawn_at, "
            "withdrawn_by_user_id = :user_id, withdrawal_reason = 'Superseded source' "
            "WHERE id = :id"
        ),
        {
            "withdrawn_at": withdrawn_at,
            "user_id": ids["user"],
            "id": ids["approval"],
        },
    )
    assert connection.execute(
        text(
            "SELECT citation.knowledge_document_version, citation.passage, document.content "
            "FROM agent_run_citation citation JOIN knowledge_document document "
            "ON document.organization_id = citation.organization_id "
            "AND document.id = citation.knowledge_document_id "
            "AND document.version = citation.knowledge_document_version "
            "WHERE citation.id = :id"
        ),
        {"id": citation_id},
    ).one() == (
        "2026.08",
        "Call the transportation desk.",
        "Call the transportation desk.",
    )

    second_run = uuid4()
    connection.execute(
        text(
            "INSERT INTO agent_run "
            "(id, organization_id, trace_id, agent_name, status, input_payload, "
            "output_payload, validation, created_at) VALUES (:id, :organization_id, "
            ":trace_id, 'knowledge-retriever', 'succeeded', '{}'::jsonb, '{}'::jsonb, "
            "'{}'::jsonb, :created_at)"
        ),
        {
            "id": second_run,
            "organization_id": ids["organization"],
            "trace_id": str(uuid4()),
            "created_at": withdrawn_at + timedelta(minutes=1),
        },
    )
    with pytest.raises(DBAPIError, match="not approved for citation at this time"):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO agent_run_citation "
                    "(id, organization_id, agent_run_id, knowledge_document_id, "
                    "knowledge_document_version, passage, cited_at) "
                    "VALUES (:id, :organization_id, :agent_run_id, :document_id, '2026.08', "
                    "'Stale passage', :cited_at)"
                ),
                {
                    "id": uuid4(),
                    "organization_id": ids["organization"],
                    "agent_run_id": second_run,
                    "document_id": ids["document"],
                    "cited_at": withdrawn_at + timedelta(minutes=1),
                },
            )

    with pytest.raises(DBAPIError, match="Knowledge approval history is immutable"):
        with connection.begin_nested():
            connection.execute(
                text("DELETE FROM organization_knowledge_approval WHERE id = :id"),
                {"id": ids["approval"]},
            )


def test_knowledge_document_content_and_version_are_immutable(connection: Connection) -> None:
    """Production break: cited source content or version can be rewritten in place."""
    _require_task5_schema(connection)
    ids = _seed_organization_user(connection)
    document_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO knowledge_document "
            "(id, organization_id, title, version, content, citations) "
            "VALUES (:id, :organization_id, 'Immutable guide', '1', 'Original', '[]'::jsonb)"
        ),
        {"id": document_id, "organization_id": ids["organization"]},
    )

    with pytest.raises(DBAPIError, match="Knowledge documents are immutable"):
        with connection.begin_nested():
            connection.execute(
                text("UPDATE knowledge_document SET content = 'Rewritten' WHERE id = :id"),
                {"id": document_id},
            )
    with pytest.raises(DBAPIError, match="Knowledge documents are immutable"):
        with connection.begin_nested():
            connection.execute(
                text("DELETE FROM knowledge_document WHERE id = :id"),
                {"id": document_id},
            )


def _seed_navigation_authorization(connection: Connection) -> dict[str, UUID]:
    ids = _seed_organization_user(connection)
    ids.update(
        {
            "role_assignment": uuid4(),
            "task": uuid4(),
            "policy": uuid4(),
            "proposal": uuid4(),
            "resource": uuid4(),
        }
    )
    proposed_at = datetime(2026, 8, 18, tzinfo=UTC)
    connection.execute(text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        text(
            "INSERT INTO navigation_task "
            "(id, organization_id, patient_id, reported_need_id, title, status) "
            "VALUES (:id, :organization_id, :patient_id, :need_id, 'Arrange transport', 'open')"
        ),
        {
            "id": ids["task"],
            "organization_id": ids["organization"],
            "patient_id": uuid4(),
            "need_id": uuid4(),
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
            "granted_at": proposed_at - timedelta(days=1),
        },
    )
    connection.execute(
        text(
            "INSERT INTO approval_policy "
            "(id, organization_id, change_type, version, effective_from, allow_self_approval, "
            "required_approval_count, required_approver_role) "
            "VALUES (:id, :organization_id, 'authorize_navigation_task', 1, :effective_from, "
            "true, 1, 'navigator')"
        ),
        {
            "id": ids["policy"],
            "organization_id": ids["organization"],
            "effective_from": proposed_at - timedelta(days=1),
        },
    )
    connection.execute(
        text(
            "INSERT INTO resource "
            "(id, organization_id, name, category, url, is_active, metadata) "
            "VALUES (:id, :organization_id, 'Ride Service', 'transportation', "
            "'https://example.test/rides', true, '{\"hours\":\"9-5\"}'::jsonb)"
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
            "required_approver_role_snapshot) VALUES (:id, :organization_id, :user_id, "
            ":proposed_at, 'authorize_navigation_task', jsonb_build_object("
            "'title', 'Arrange transport', 'resources', jsonb_build_array(jsonb_build_object("
            "'resource_id', CAST(:resource_id AS text), 'name', 'Ride Service', "
            "'category', 'transportation', 'url', 'https://example.test/rides', "
            "'metadata', '{\"hours\":\"9-5\"}'::jsonb, "
            "'match_rationale', 'Matches transportation need'))), "
            "'Patient requested transport', 'ojcc.authorize-navigation-task', 2, :task_id, "
            ":policy_id, 1, true, 1, 'navigator')"
        ),
        {
            "id": ids["proposal"],
            "organization_id": ids["organization"],
            "user_id": ids["user"],
            "proposed_at": proposed_at,
            "resource_id": ids["resource"],
            "task_id": ids["task"],
            "policy_id": ids["policy"],
        },
    )
    return ids


def test_navigation_resource_match_is_proposal_authorized_before_later_delivery(
    connection: Connection,
) -> None:
    """Production break: resource approval drifts from its proposal or delivery is conflated."""
    _require_task5_schema(connection)
    ids = _seed_navigation_authorization(connection)
    match_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO navigation_task_resource "
            "(id, organization_id, navigation_task_id, resource_id, proposed_change_id, "
            "resource_name_snapshot, resource_category_snapshot, resource_url_snapshot, "
            "resource_metadata_snapshot, match_rationale_snapshot, proposed_at) "
            "VALUES (:id, :organization_id, :task_id, :resource_id, :proposal_id, "
            "'Ride Service', 'transportation', 'https://example.test/rides', "
            "'{\"hours\":\"9-5\"}'::jsonb, 'Matches transportation need', :proposed_at)"
        ),
        {
            "id": match_id,
            "organization_id": ids["organization"],
            "task_id": ids["task"],
            "resource_id": ids["resource"],
            "proposal_id": ids["proposal"],
            "proposed_at": datetime(2026, 8, 18, tzinfo=UTC),
        },
    )
    assert connection.scalar(
        text("SELECT approved_at FROM navigation_task_resource WHERE id = :id"),
        {"id": match_id},
    ) is None

    authorized_at = datetime(2026, 8, 18, 0, 1, tzinfo=UTC)
    connection.execute(
        text(
            "INSERT INTO approval_decision "
            "(id, organization_id, proposed_change_id, authorized_by_user_id, "
            "qualifying_role_assignment_id, qualifying_role_snapshot, decision, authorized_at) "
            "VALUES (:id, :organization_id, :proposal_id, :user_id, :assignment_id, "
            "'navigator', 'approved', :authorized_at)"
        ),
        {
            "id": uuid4(),
            "organization_id": ids["organization"],
            "proposal_id": ids["proposal"],
            "user_id": ids["user"],
            "assignment_id": ids["role_assignment"],
            "authorized_at": authorized_at,
        },
    )
    assert connection.scalar(
        text("SELECT approved_at FROM navigation_task_resource WHERE id = :id"),
        {"id": match_id},
    ) == authorized_at

    delivered_at = authorized_at + timedelta(hours=1)
    connection.execute(
        text(
            "UPDATE navigation_task_resource SET delivered_at = :delivered_at, "
            "delivered_by_user_id = :user_id WHERE id = :id"
        ),
        {"delivered_at": delivered_at, "user_id": ids["user"], "id": match_id},
    )
    assert connection.execute(
        text(
            "SELECT approved_at, delivered_at, delivered_by_user_id "
            "FROM navigation_task_resource WHERE id = :id"
        ),
        {"id": match_id},
    ).one() == (authorized_at, delivered_at, ids["user"])


def test_navigation_task_cannot_be_approved_with_unmaterialized_resource_links(
    connection: Connection,
) -> None:
    """Production break: approval succeeds while a proposed resource link is missing."""
    _require_task5_schema(connection)
    ids = _seed_navigation_authorization(connection)

    with pytest.raises(DBAPIError, match="Every proposed resource match must be materialized"):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO approval_decision "
                    "(id, organization_id, proposed_change_id, authorized_by_user_id, "
                    "qualifying_role_assignment_id, qualifying_role_snapshot, decision, "
                    "authorized_at) VALUES (:id, :organization_id, :proposal_id, :user_id, "
                    ":assignment_id, 'navigator', 'approved', :authorized_at)"
                ),
                {
                    "id": uuid4(),
                    "organization_id": ids["organization"],
                    "proposal_id": ids["proposal"],
                    "user_id": ids["user"],
                    "assignment_id": ids["role_assignment"],
                    "authorized_at": datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
                },
            )


def test_navigation_resource_must_be_listed_in_the_authorizing_proposal(
    connection: Connection,
) -> None:
    """Production break: a resource can be attached outside the exact approved value."""
    _require_task5_schema(connection)
    ids = _seed_navigation_authorization(connection)
    unproposed_resource_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO resource "
            "(id, organization_id, name, category, is_active, metadata) "
            "VALUES (:id, :organization_id, 'Unlisted', 'other', true, '{}'::jsonb)"
        ),
        {"id": unproposed_resource_id, "organization_id": ids["organization"]},
    )

    with pytest.raises(DBAPIError, match="not part of the authorize_navigation_task proposal"):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO navigation_task_resource "
                    "(id, organization_id, navigation_task_id, resource_id, "
                    "proposed_change_id, resource_name_snapshot, resource_category_snapshot, "
                    "resource_metadata_snapshot, match_rationale_snapshot, proposed_at) "
                    "VALUES (:id, :organization_id, :task_id, :resource_id, :proposal_id, "
                    "'Unlisted', 'other', '{}'::jsonb, 'Not proposed', :proposed_at)"
                ),
                {
                    "id": uuid4(),
                    "organization_id": ids["organization"],
                    "task_id": ids["task"],
                    "resource_id": unproposed_resource_id,
                    "proposal_id": ids["proposal"],
                    "proposed_at": datetime(2026, 8, 18, tzinfo=UTC),
                },
            )
