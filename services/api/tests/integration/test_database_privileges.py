from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (
    ApprovalDecision,
    ApprovalPolicy,
    CareEpisode,
    CheckInDefinition,
    CheckInSubmission,
    NavigationTask,
    Organization,
    PathwayDefinition,
    ProposedChange,
    ReportedNeed,
    RoleAssignment,
    SyntheticPatient,
    User,
)
from app.db.targets import validate_database_target_pair
from app.domain.enums import (
    ApprovalChangeType,
    ApprovalDecisionValue,
    CheckInStatus,
    NavigationTaskStatus,
    NeedStatus,
    OutcomeDisposition,
    SubmissionSource,
    UserRole,
)
from app.domain.navigation_tasks import (
    claim_navigation_task,
    complete_navigation_task,
    start_navigation_task,
)
from app.domain.outcomes import record_outcome

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATABASE_PREFIX = "ojcc_privilege_test_"
DATABASE_NAME_PATTERN = re.compile(r"ojcc_privilege_test_[0-9a-f]{32}")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

MIGRATION_ROLE = "ojcc_migrator"
APPLICATION_ROLE = "ojcc_api"
APPLICATION_GROUP = "ojcc_app"

SELECT_ONLY_TABLES = frozenset(
    {
        "agent_run",
        "agent_run_citation",
        "approval_policy",
        "audit_event",
        "care_episode",
        "check_in_definition",
        "episode_pathway_assignment",
        "follow_up_request",
        "knowledge_document",
        "manual_review_task",
        "navigation_task_resource",
        "organization",
        "organization_knowledge_approval",
        "pathway_definition",
        "patient_identity_link",
        "patient_message",
        "proposed_value_schema",
        "reported_need",
        "resource",
        "role_assignment",
        "signal_rule",
        "synthetic_patient",
        "user_account",
        "workflow_run",
        "workflow_transition_event",
    }
)
INSERT_TABLES = frozenset(
    {
        "approval_decision",
        "check_in_submission",
        "follow_up_response",
        "outcome",
        "proposed_change",
        "safety_signal_resolution",
    }
)
UPDATE_TABLES = frozenset({"navigation_task", "safety_signal"})
APPLICATION_TABLES = SELECT_ONLY_TABLES | INSERT_TABLES | UPDATE_TABLES
APPLICATION_VIEWS = frozenset(
    {
        "active_check_in_submission",
        "effective_need_state",
        "effective_proposed_change_state",
        "effective_safety_signal_state",
    }
)
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

APPLICATION_FUNCTIONS = frozenset(
    {
        "append_workflow_transition_event",
        "apply_final_approval_decision",
        "apply_navigation_resource_approval",
        "close_reported_need_from_outcome",
        "guard_agent_run_citation",
        "guard_agent_run_citation_immutable",
        "guard_agent_run_created_at",
        "guard_approval_decision",
        "guard_bound_navigation_task_delete",
        "guard_follow_up_request_insert",
        "guard_follow_up_response_insert",
        "guard_knowledge_approval_history",
        "guard_knowledge_document_immutable",
        "guard_manual_review_task",
        "guard_navigation_task_lifecycle",
        "guard_navigation_task_resource",
        "guard_navigation_task_resource_proposal",
        "guard_patient_identity_link_response_history",
        "guard_proposed_change_revision",
        "guard_reported_need_identity_update",
        "guard_reported_need_reopening",
        "guard_role_assignment_approval_history",
        "guard_role_assignment_knowledge_history",
        "guard_safety_signal_lifecycle",
        "guard_safety_signal_resolution",
        "guard_workflow_run_lineage",
        "record_navigation_task_transition",
        "reject_append_only_mutation",
        "reject_approval_policy_mutation",
        "reject_proposed_value_schema_mutation",
        "reject_signal_rule_mutation",
        "safety_severity_rank",
    }
)
SECURITY_DEFINER_FUNCTIONS = frozenset(
    {
        "append_workflow_transition_event",
        "apply_final_approval_decision",
        "apply_navigation_resource_approval",
        "close_reported_need_from_outcome",
        "guard_approval_decision",
        "guard_bound_navigation_task_delete",
        "guard_follow_up_request_insert",
        "guard_follow_up_response_insert",
        "guard_navigation_task_lifecycle",
        "guard_navigation_task_resource_proposal",
        "guard_patient_identity_link_response_history",
        "guard_proposed_change_revision",
        "guard_safety_signal_resolution",
        "record_navigation_task_transition",
    }
)

MIGRATION_0005_TABLES = (
    "agent_run_citation",
    "manual_review_task",
    "navigation_task_resource",
    "organization_knowledge_approval",
    "workflow_run",
    "workflow_transition_event",
)
MIGRATION_0005_FUNCTIONS = (
    "append_workflow_transition_event",
    "apply_navigation_resource_approval",
    "guard_agent_run_citation",
    "guard_agent_run_citation_immutable",
    "guard_agent_run_created_at",
    "guard_knowledge_approval_history",
    "guard_knowledge_document_immutable",
    "guard_manual_review_task",
    "guard_navigation_task_resource",
    "guard_navigation_task_resource_proposal",
    "guard_role_assignment_knowledge_history",
    "guard_workflow_run_lineage",
    "reject_append_only_mutation",
)
MIGRATION_0005_ENUMS = ("audit_actor_type", "manual_review_task_state")


@dataclass(frozen=True)
class PrivilegeDatabase:
    name: str
    bootstrap_url: str
    migration_url: str
    application_url: str


@dataclass(frozen=True)
class PrivilegeBoundaryCase:
    organization_id: UUID
    navigator_user_id: UUID
    reported_need_id: UUID
    navigation_task_id: UUID
    proposed_change_id: UUID


def _psycopg_url(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _validate_base_url(url: URL, *, label: str) -> None:
    if url.get_backend_name() != "postgresql" or url.host not in LOOPBACK_HOSTS:
        raise ValueError(f"{label} must use loopback PostgreSQL")
    if url.port not in (None, 5432) or url.query:
        raise ValueError(f"{label} must use port 5432 without query parameters")
    if url.database != "postgres":
        raise ValueError(f"{label} must use the postgres maintenance database")


def _alembic_environment(*, application_url: str, migration_url: str) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"DATABASE_URL", "MIGRATION_DATABASE_URL", "BOOTSTRAP_DATABASE_URL"}
        and not key.upper().startswith("PG")
    }
    environment["DATABASE_URL"] = application_url
    environment["MIGRATION_DATABASE_URL"] = migration_url
    return environment


def _upgrade(*, application_url: str, migration_url: str, revision: str) -> None:
    subprocess.run(
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
        check=True,
        capture_output=True,
        text=True,
        env=_alembic_environment(
            application_url=application_url,
            migration_url=migration_url,
        ),
    )


def _alembic(
    *,
    application_url: str,
    migration_url: str,
    arguments: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/api/alembic.ini",
            *arguments,
        ],
        cwd=PROJECT_ROOT,
        check=check,
        capture_output=True,
        text=True,
        env=_alembic_environment(
            application_url=application_url,
            migration_url=migration_url,
        ),
    )


def _transfer_0005_application_ownership(database: PrivilegeDatabase) -> None:
    with psycopg.connect(_psycopg_url(make_url(database.bootstrap_url))) as connection:
        with connection.cursor() as cursor:
            for table_name in MIGRATION_0005_TABLES:
                cursor.execute(
                    sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
                        sql.Identifier(table_name), sql.Identifier(MIGRATION_ROLE)
                    )
                )
            for function_name in MIGRATION_0005_FUNCTIONS:
                cursor.execute(
                    sql.SQL("ALTER FUNCTION public.{}() OWNER TO {}").format(
                        sql.Identifier(function_name), sql.Identifier(MIGRATION_ROLE)
                    )
                )
            for enum_name in MIGRATION_0005_ENUMS:
                cursor.execute(
                    sql.SQL("ALTER TYPE public.{} OWNER TO {}").format(
                        sql.Identifier(enum_name), sql.Identifier(MIGRATION_ROLE)
                    )
                )


@contextmanager
def _provision_privilege_database(
    *, revision: str = "head"
) -> Iterator[PrivilegeDatabase]:
    bootstrap_text = os.getenv("BOOTSTRAP_DATABASE_URL")
    if bootstrap_text is None:
        pytest.skip("BOOTSTRAP_DATABASE_URL is required for privilege integration tests")
    migration_text = settings.require_migration_database_url()
    application_text = settings.database_url

    validate_database_target_pair(
        application_url=application_text,
        migration_url=migration_text,
    )
    validate_database_target_pair(
        application_url=application_text,
        migration_url=bootstrap_text,
    )
    validate_database_target_pair(
        application_url=migration_text,
        migration_url=bootstrap_text,
    )

    bootstrap_base = make_url(bootstrap_text)
    migration_base = make_url(migration_text)
    application_base = make_url(application_text)
    _validate_base_url(bootstrap_base, label="BOOTSTRAP_DATABASE_URL")
    _validate_base_url(migration_base, label="MIGRATION_DATABASE_URL")
    _validate_base_url(application_base, label="DATABASE_URL")

    name = f"{DATABASE_PREFIX}{uuid4().hex}"
    assert DATABASE_NAME_PATTERN.fullmatch(name)
    database = PrivilegeDatabase(
        name=name,
        bootstrap_url=bootstrap_base.set(database=name).render_as_string(hide_password=False),
        migration_url=migration_base.set(database=name).render_as_string(hide_password=False),
        application_url=application_base.set(database=name).render_as_string(hide_password=False),
    )
    created = False
    with psycopg.connect(_psycopg_url(migration_base), autocommit=True) as maintenance:
        with maintenance.cursor() as cursor:
            assert cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone() is None
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(MIGRATION_ROLE)
                )
            )
    created = True
    print(f"CREATED {name}", flush=True)

    try:
        migration_engine = create_engine(database.migration_url)
        try:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(f"ALTER SCHEMA public OWNER TO {MIGRATION_ROLE}")
                )
        finally:
            migration_engine.dispose()

        _upgrade(
            application_url=database.application_url,
            migration_url=database.migration_url,
            revision="0004_safety_approval_lifecycle",
        )
        _upgrade(
            application_url=database.application_url,
            migration_url=database.bootstrap_url,
            revision="0005_workflow_knowledge_audit",
        )
        _transfer_0005_application_ownership(database)
        _upgrade(
            application_url=database.application_url,
            migration_url=database.migration_url,
            revision=revision,
        )
        yield database
    finally:
        if created:
            assert DATABASE_NAME_PATTERN.fullmatch(name)
            try:
                with psycopg.connect(
                    _psycopg_url(migration_base), autocommit=True
                ) as maintenance:
                    with maintenance.cursor() as cursor:
                        cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            except Exception as error:
                print(f"LEFTOVER {name}: {type(error).__name__}", flush=True)
                raise
            else:
                print(f"DROPPED {name}", flush=True)


@pytest.fixture(scope="module")
def privilege_database() -> Iterator[PrivilegeDatabase]:
    with _provision_privilege_database() as database:
        yield database


@pytest.fixture
def catalog(privilege_database: PrivilegeDatabase) -> Iterator[Connection]:
    engine = create_engine(privilege_database.migration_url)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def _has_table_privilege(
    catalog: Connection, *, role: str, relation: str, privilege: str
) -> bool:
    return bool(
        catalog.scalar(
            text("SELECT has_table_privilege(:role, :relation, :privilege)"),
            {"role": role, "relation": f"public.{relation}", "privilege": privilege},
        )
    )


def _public_has_table_privilege(
    catalog: Connection, *, relation: str, privilege: str
) -> bool:
    return bool(
        catalog.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_class class "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "CROSS JOIN LATERAL aclexplode("
                "coalesce(class.relacl, acldefault('r', class.relowner))) acl "
                "WHERE namespace.nspname = 'public' AND class.relname = :relation "
                "AND acl.grantee = 0 AND acl.privilege_type = :privilege)"
            ),
            {"relation": relation, "privilege": privilege},
        )
    )


def _seed_privilege_boundary_case(session: Session) -> PrivilegeBoundaryCase:
    now = datetime.now(UTC)
    organization = Organization(name=f"Privilege boundary organization {uuid4()}")
    navigator = User(
        email=f"privilege-boundary-{uuid4()}@example.test",
        display_name="Privilege boundary navigator",
    )
    session.add_all([organization, navigator])
    session.flush()
    navigator.primary_organization_id = organization.id

    patient = SyntheticPatient(
        organization_id=organization.id,
        external_ref=f"privilege-boundary-{uuid4()}",
        display_name="Synthetic privilege-boundary patient",
    )
    pathway = PathwayDefinition(
        organization_id=organization.id,
        slug=f"privilege-boundary-{uuid4()}",
        version=1,
        name="Privilege boundary pathway",
    )
    session.add_all([patient, pathway])
    session.flush()

    navigator_role = RoleAssignment(
        organization_id=organization.id,
        user_id=navigator.id,
        role=UserRole.NAVIGATOR,
        granted_at=now - timedelta(hours=1),
    )
    episode = CareEpisode(
        organization_id=organization.id,
        patient_id=patient.id,
        status="active",
        started_at=now - timedelta(days=1),
    )
    definition = CheckInDefinition(
        organization_id=organization.id,
        pathway_definition_id=pathway.id,
        slug=f"privilege-boundary-{uuid4()}",
        version=1,
        title="Privilege boundary check-in",
        questionnaire={"questions": [{"id": "transportation", "type": "boolean"}]},
    )
    session.add_all([navigator_role, episode, definition])
    session.flush()

    submission = CheckInSubmission(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        check_in_definition_id=definition.id,
        status=CheckInStatus.SUBMITTED,
        answers={"transportation": True},
        submission_source=SubmissionSource.CLINICIAN,
        submitted_by_user_id=navigator.id,
        submitted_at=now - timedelta(minutes=30),
    )
    session.add(submission)
    session.flush()

    need = ReportedNeed(
        organization_id=organization.id,
        patient_id=patient.id,
        care_episode_id=episode.id,
        source_submission_id=submission.id,
        kind="transportation",
        status=NeedStatus.OPEN,
        evidence=[{"field": "transportation", "text": "yes"}],
    )
    session.add(need)
    session.flush()
    task = NavigationTask(
        organization_id=organization.id,
        patient_id=patient.id,
        reported_need_id=need.id,
        title="Unapproved privilege-boundary title",
        status=NavigationTaskStatus.OPEN,
    )
    policy = ApprovalPolicy(
        organization_id=organization.id,
        change_type=ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK,
        version=1,
        effective_from=now - timedelta(days=1),
        deterministic_severity_threshold=None,
        allow_self_approval=True,
        required_approval_count=1,
        required_approver_role=UserRole.NAVIGATOR,
    )
    session.add_all([task, policy])
    session.flush()

    proposal = ProposedChange(
        organization_id=organization.id,
        proposed_by_user_id=navigator.id,
        proposed_at=now - timedelta(minutes=10),
        change_type=ApprovalChangeType.AUTHORIZE_NAVIGATION_TASK,
        proposed_value={"title": "Arrange synthetic transportation support"},
        rationale="Synthetic transportation support is requested.",
        value_schema_id="ojcc.authorize-navigation-task",
        value_schema_version=1,
        navigation_task_id=task.id,
        approval_policy_id=policy.id,
        approval_policy_version=policy.version,
        deterministic_severity_threshold_snapshot=None,
        allow_self_approval_snapshot=policy.allow_self_approval,
        required_approval_count_snapshot=policy.required_approval_count,
        required_approver_role_snapshot=policy.required_approver_role,
    )
    session.add(proposal)
    session.flush()
    session.add(
        ApprovalDecision(
            organization_id=organization.id,
            proposed_change_id=proposal.id,
            authorized_by_user_id=navigator.id,
            qualifying_role_assignment_id=navigator_role.id,
            qualifying_role_snapshot=UserRole.NAVIGATOR,
            decision=ApprovalDecisionValue.APPROVED,
            authorized_at=now - timedelta(minutes=5),
        )
    )
    session.flush()
    return PrivilegeBoundaryCase(
        organization_id=organization.id,
        navigator_user_id=navigator.id,
        reported_need_id=need.id,
        navigation_task_id=task.id,
        proposed_change_id=proposal.id,
    )


def test_roles_and_ownership_have_the_final_non_owner_profile(
    catalog: Connection, privilege_database: PrivilegeDatabase
) -> None:
    roles = {
        row.rolname: (
            row.rolsuper,
            row.rolinherit,
            row.rolcreaterole,
            row.rolcreatedb,
            row.rolcanlogin,
            row.rolreplication,
            row.rolbypassrls,
        )
        for row in catalog.execute(
            text(
                "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
                "rolcanlogin, rolreplication, rolbypassrls FROM pg_roles "
                "WHERE rolname IN (:migration, :application, :group)"
            ),
            {
                "migration": MIGRATION_ROLE,
                "application": APPLICATION_ROLE,
                "group": APPLICATION_GROUP,
            },
        )
    }
    assert roles == {
        MIGRATION_ROLE: (False, True, False, True, True, False, False),
        APPLICATION_ROLE: (False, True, False, False, True, False, False),
        APPLICATION_GROUP: (False, True, False, False, False, False, False),
    }

    membership = catalog.execute(
        text(
            "SELECT m.inherit_option, m.set_option, m.admin_option "
            "FROM pg_auth_members m "
            "JOIN pg_roles member ON member.oid = m.member "
            "JOIN pg_roles granted ON granted.oid = m.roleid "
            "WHERE member.rolname = :application AND granted.rolname = :group"
        ),
        {"application": APPLICATION_ROLE, "group": APPLICATION_GROUP},
    ).one()
    assert tuple(membership) == (True, False, False)
    assert catalog.scalar(
        text("SELECT pg_has_role(:application, :group, 'USAGE')"),
        {"application": APPLICATION_ROLE, "group": APPLICATION_GROUP},
    )
    assert not catalog.scalar(
        text("SELECT pg_has_role(:application, :group, 'SET')"),
        {"application": APPLICATION_ROLE, "group": APPLICATION_GROUP},
    )

    database_owner = catalog.scalar(
        text(
            "SELECT owner.rolname FROM pg_database database "
            "JOIN pg_roles owner ON owner.oid = database.datdba "
            "WHERE database.datname = :database"
        ),
        {"database": privilege_database.name},
    )
    schema_owner = catalog.scalar(
        text(
            "SELECT owner.rolname FROM pg_namespace namespace "
            "JOIN pg_roles owner ON owner.oid = namespace.nspowner "
            "WHERE namespace.nspname = 'public'"
        )
    )
    assert database_owner == schema_owner == MIGRATION_ROLE

    application_owned_objects = catalog.scalar(
        text(
            "SELECT count(*) FROM ("
            "SELECT class.oid FROM pg_class class WHERE class.relowner = "
            "(SELECT oid FROM pg_roles WHERE rolname = :application) "
            "UNION ALL SELECT proc.oid FROM pg_proc proc WHERE proc.proowner = "
            "(SELECT oid FROM pg_roles WHERE rolname = :application)"
            ") owned"
        ),
        {"application": APPLICATION_ROLE},
    )
    assert application_owned_objects == 0

    owner_is_reachable = catalog.scalar(
        text(
            "WITH RECURSIVE memberships(roleid) AS ("
            "SELECT membership.roleid FROM pg_auth_members membership "
            "JOIN pg_roles member ON member.oid = membership.member "
            "WHERE member.rolname = :application "
            "UNION SELECT membership.roleid FROM pg_auth_members membership "
            "JOIN memberships prior ON prior.roleid = membership.member"
            ") SELECT EXISTS (SELECT 1 FROM memberships "
            "JOIN pg_roles role ON role.oid = memberships.roleid "
            "WHERE role.rolname = :migration)"
        ),
        {"application": APPLICATION_ROLE, "migration": MIGRATION_ROLE},
    )
    assert not owner_is_reachable


def test_role_surface_removes_direct_event_writes(catalog: Connection) -> None:
    for relation in ("audit_event", "workflow_transition_event"):
        assert not _has_table_privilege(
            catalog,
            role=APPLICATION_GROUP,
            relation=relation,
            privilege="INSERT",
        )


def test_relation_and_view_privileges_match_the_complete_matrix(catalog: Connection) -> None:
    relations = {
        row.relname: row.relkind
        for row in catalog.execute(
            text(
                "SELECT class.relname, class.relkind FROM pg_class class "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = 'public' AND class.relkind IN ('r', 'p', 'v', 'm')"
            )
        )
    }
    assert {name for name, kind in relations.items() if kind in {"r", "p"}} == (
        APPLICATION_TABLES | {"alembic_version"}
    )
    assert {name for name, kind in relations.items() if kind in {"v", "m"}} == APPLICATION_VIEWS

    expected: dict[tuple[str, str], bool] = {}
    for relation in APPLICATION_TABLES | APPLICATION_VIEWS:
        for privilege in TABLE_PRIVILEGES:
            expected[(relation, privilege)] = privilege == "SELECT"
    for relation in INSERT_TABLES:
        expected[(relation, "INSERT")] = True
    for relation in UPDATE_TABLES:
        expected[(relation, "UPDATE")] = True

    actual = {
        (relation, privilege): _has_table_privilege(
            catalog,
            role=APPLICATION_GROUP,
            relation=relation,
            privilege=privilege,
        )
        for relation in APPLICATION_TABLES | APPLICATION_VIEWS
        for privilege in TABLE_PRIVILEGES
    }
    assert actual == expected

    for role in (APPLICATION_GROUP, APPLICATION_ROLE):
        for privilege in TABLE_PRIVILEGES:
            assert not _has_table_privilege(
                catalog,
                role=role,
                relation="alembic_version",
                privilege=privilege,
            )
    for relation in APPLICATION_TABLES | APPLICATION_VIEWS | {"alembic_version"}:
        for privilege in TABLE_PRIVILEGES:
            assert not _public_has_table_privilege(
                catalog,
                relation=relation,
                privilege=privilege,
            )

    assert catalog.scalar(
        text(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE grantee IN (:group, :application) AND is_grantable = 'YES'"
        ),
        {"group": APPLICATION_GROUP, "application": APPLICATION_ROLE},
    ) == 0
    assert catalog.scalar(
        text(
            "SELECT count(*) FROM pg_class class "
            "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
            "WHERE namespace.nspname = 'public' AND class.relkind = 'S'"
        )
    ) == 0

    owners = {
        row.owner
        for row in catalog.execute(
            text(
                "SELECT owner.rolname AS owner FROM pg_class class "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "JOIN pg_roles owner ON owner.oid = class.relowner "
                "WHERE namespace.nspname = 'public' "
                "AND class.relkind IN ('r', 'p', 'v', 'm')"
            )
        )
    }
    assert owners == {MIGRATION_ROLE}


def test_database_privileges_are_deny_by_default(
    catalog: Connection, privilege_database: PrivilegeDatabase
) -> None:
    for role in (APPLICATION_GROUP, APPLICATION_ROLE):
        assert catalog.scalar(
            text("SELECT has_database_privilege(:role, :database, 'CONNECT')"),
            {"role": role, "database": privilege_database.name},
        )
        assert not catalog.scalar(
            text("SELECT has_database_privilege(:role, :database, 'CREATE')"),
            {"role": role, "database": privilege_database.name},
        )
        assert not catalog.scalar(
            text("SELECT has_database_privilege(:role, :database, 'TEMPORARY')"),
            {"role": role, "database": privilege_database.name},
        )

    for privilege in ("CONNECT", "CREATE", "TEMPORARY"):
        assert not catalog.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_database database "
                "CROSS JOIN LATERAL aclexplode("
                "coalesce(database.datacl, acldefault('d', database.datdba))) acl "
                "WHERE database.datname = :database AND acl.grantee = 0 "
                "AND acl.privilege_type = :privilege)"
            ),
            {"database": privilege_database.name, "privilege": privilege},
        )


def test_schema_privileges_are_deny_by_default(catalog: Connection) -> None:
    for role in (APPLICATION_GROUP, APPLICATION_ROLE):
        assert catalog.scalar(
            text("SELECT has_schema_privilege(:role, 'public', 'USAGE')"),
            {"role": role},
        )
        assert not catalog.scalar(
            text("SELECT has_schema_privilege(:role, 'public', 'CREATE')"),
            {"role": role},
        )
    for privilege in ("USAGE", "CREATE"):
        assert not catalog.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_namespace namespace "
                "CROSS JOIN LATERAL aclexplode("
                "coalesce(namespace.nspacl, acldefault('n', namespace.nspowner))) acl "
                "WHERE namespace.nspname = 'public' AND acl.grantee = 0 "
                "AND acl.privilege_type = :privilege)"
            ),
            {"privilege": privilege},
        )


def test_function_privileges_and_security_metadata_match_the_complete_matrix(
    catalog: Connection,
) -> None:
    functions = {
        (row.proname, row.identity_arguments): row
        for row in catalog.execute(
            text(
                "SELECT proc.proname, pg_get_function_identity_arguments(proc.oid) "
                "AS identity_arguments, owner.rolname AS owner, proc.prosecdef, proc.proconfig, "
                "EXISTS (SELECT 1 FROM aclexplode("
                "coalesce(proc.proacl, acldefault('f', proc.proowner))) acl "
                "WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE') AS public_execute, "
                "has_function_privilege(:group, proc.oid, 'EXECUTE') AS group_execute "
                "FROM pg_proc proc "
                "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
                "JOIN pg_roles owner ON owner.oid = proc.proowner "
                "WHERE namespace.nspname = 'public' AND NOT EXISTS ("
                "SELECT 1 FROM pg_depend dependency "
                "WHERE dependency.classid = 'pg_proc'::regclass "
                "AND dependency.objid = proc.oid AND dependency.deptype = 'e')"
            ),
            {"group": APPLICATION_GROUP},
        )
    }
    expected_signatures = {
        (name, "value safety_severity" if name == "safety_severity_rank" else "")
        for name in APPLICATION_FUNCTIONS
    }
    assert set(functions) == expected_signatures

    for (name, _), function in functions.items():
        assert function.owner == MIGRATION_ROLE
        assert function.prosecdef is (name in SECURITY_DEFINER_FUNCTIONS)
        expected_config = (
            ["search_path=pg_catalog, public, pg_temp"]
            if name in SECURITY_DEFINER_FUNCTIONS
            else None
        )
        assert function.proconfig == expected_config
        assert not function.public_execute
        assert function.group_execute is (name == "safety_severity_rank")

    assert catalog.scalar(
        text(
            "SELECT count(*) FROM pg_proc proc "
            "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
            "CROSS JOIN LATERAL aclexplode(proc.proacl) acl "
            "LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee "
            "WHERE namespace.nspname = 'public' "
            "AND proc.proname = ANY(:functions) "
            "AND acl.privilege_type = 'EXECUTE' "
            "AND coalesce(grantee.rolname, 'PUBLIC') NOT IN (:owner, :group, 'PUBLIC')"
        ),
        {
            "functions": sorted(APPLICATION_FUNCTIONS),
            "owner": MIGRATION_ROLE,
            "group": APPLICATION_GROUP,
        },
    ) == 0


def test_runtime_commands_cross_only_the_security_definer_write_boundary(
    privilege_database: PrivilegeDatabase,
) -> None:
    owner_engine = create_engine(privilege_database.migration_url)
    try:
        with Session(owner_engine, expire_on_commit=False) as owner_session:
            case = _seed_privilege_boundary_case(owner_session)
            owner_session.commit()
    finally:
        owner_engine.dispose()

    application_engine = create_engine(privilege_database.application_url)
    try:
        with Session(application_engine, expire_on_commit=False) as application_session:
            claim_navigation_task(
                application_session,
                organization_id=case.organization_id,
                task_id=case.navigation_task_id,
                actor_user_id=case.navigator_user_id,
                proposed_change_id=case.proposed_change_id,
                due_at=datetime.now(UTC) + timedelta(days=2),
            )
            start_navigation_task(
                application_session,
                organization_id=case.organization_id,
                task_id=case.navigation_task_id,
                actor_user_id=case.navigator_user_id,
            )
            completion = complete_navigation_task(
                application_session,
                organization_id=case.organization_id,
                task_id=case.navigation_task_id,
                actor_user_id=case.navigator_user_id,
            )
            outcome = record_outcome(
                application_session,
                organization_id=case.organization_id,
                need_id=case.reported_need_id,
                recorded_by_user_id=case.navigator_user_id,
                disposition=OutcomeDisposition.RESOLVED,
                note="Synthetic transportation support was resolved.",
                idempotency_key=f"privilege-boundary-{uuid4()}",
            )
            application_session.commit()
    finally:
        application_engine.dispose()

    assert completion.follow_up_request_id is not None
    assert outcome.need_id == case.reported_need_id

    verification_engine = create_engine(privilege_database.migration_url)
    try:
        with verification_engine.connect() as connection:
            facts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM follow_up_request "
                    "WHERE navigation_task_id = :task_id), "
                    "(SELECT count(*) FROM audit_event "
                    "WHERE entity_id = :task_id), "
                    "(SELECT effective_state FROM effective_need_state "
                    "WHERE id = :need_id)"
                ),
                {"task_id": case.navigation_task_id, "need_id": case.reported_need_id},
            ).one()
    finally:
        verification_engine.dispose()
    assert facts[0] == 1
    assert facts[1] >= 3
    assert facts[2] == "closed"

    for relation_name in ("audit_event", "workflow_transition_event"):
        with psycopg.connect(
            _psycopg_url(make_url(privilege_database.application_url))
        ) as connection:
            with connection.cursor() as cursor:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cursor.execute(
                        sql.SQL("INSERT INTO public.{} DEFAULT VALUES").format(
                            sql.Identifier(relation_name)
                        )
                    )


def _acl_snapshot(database: PrivilegeDatabase) -> tuple[tuple[object, ...], ...]:
    engine = create_engine(database.migration_url)
    try:
        with engine.connect() as connection:
            return tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT object_type, object_name, acl FROM ("
                        "SELECT 'database' AS object_type, datname AS object_name, "
                        "datacl::text AS acl FROM pg_database "
                        "WHERE datname = current_database() "
                        "UNION ALL SELECT 'schema', nspname, nspacl::text "
                        "FROM pg_namespace WHERE nspname = 'public' "
                        "UNION ALL SELECT 'relation', class.relname, class.relacl::text "
                        "FROM pg_class class JOIN pg_namespace namespace "
                        "ON namespace.oid = class.relnamespace "
                        "WHERE namespace.nspname = 'public' "
                        "AND class.relkind IN ('r', 'p', 'v', 'm') "
                        "UNION ALL SELECT 'function', proc.oid::regprocedure::text, "
                        "proc.proacl::text FROM pg_proc proc "
                        "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
                        "WHERE namespace.nspname = 'public' AND NOT EXISTS ("
                        "SELECT 1 FROM pg_depend dependency "
                        "WHERE dependency.classid = 'pg_proc'::regclass "
                        "AND dependency.objid = proc.oid AND dependency.deptype = 'e')"
                        ") objects ORDER BY object_type, object_name"
                    )
                )
            )
    finally:
        engine.dispose()


def test_populated_0006_upgrade_is_privilege_only() -> None:
    with _provision_privilege_database(
        revision="0006_navigator_closed_loop"
    ) as database:
        engine = create_engine(database.migration_url)
        try:
            with Session(engine, expire_on_commit=False) as session:
                case = _seed_privilege_boundary_case(session)
                session.commit()
            with engine.connect() as connection:
                before = tuple(
                    connection.execute(
                        text(
                            "SELECT * FROM navigation_task "
                            "WHERE organization_id = :organization_id ORDER BY id"
                        ),
                        {"organization_id": case.organization_id},
                    ).one()
                )
        finally:
            engine.dispose()

        _upgrade(
            application_url=database.application_url,
            migration_url=database.migration_url,
            revision="head",
        )

        engine = create_engine(database.migration_url)
        try:
            with engine.connect() as connection:
                after = tuple(
                    connection.execute(
                        text(
                            "SELECT * FROM navigation_task "
                            "WHERE organization_id = :organization_id ORDER BY id"
                        ),
                        {"organization_id": case.organization_id},
                    ).one()
                )
                version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        finally:
            engine.dispose()
        assert after == before
        assert version == "0007_database_least_privilege"


def test_invalid_ownership_preflight_preserves_0006_version_and_acl() -> None:
    with _provision_privilege_database(
        revision="0006_navigator_closed_loop"
    ) as database:
        with psycopg.connect(
            _psycopg_url(make_url(database.bootstrap_url))
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("ALTER TABLE public.audit_event OWNER TO {}").format(
                        sql.Identifier(APPLICATION_ROLE)
                    )
                )
        before = _acl_snapshot(database)

        result = _alembic(
            application_url=database.application_url,
            migration_url=database.migration_url,
            arguments=["upgrade", "head"],
            check=False,
        )
        assert result.returncode != 0
        assert "migration owner does not own relations" in result.stderr
        assert _acl_snapshot(database) == before

        engine = create_engine(database.migration_url)
        try:
            with engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ) == "0006_navigator_closed_loop"
        finally:
            engine.dispose()


def test_head_version_and_metadata_check_are_clean(
    privilege_database: PrivilegeDatabase,
) -> None:
    current = _alembic(
        application_url=privilege_database.application_url,
        migration_url=privilege_database.migration_url,
        arguments=["current"],
    )
    assert "0007_database_least_privilege (head)" in current.stdout

    check_result = _alembic(
        application_url=privilege_database.application_url,
        migration_url=privilege_database.migration_url,
        arguments=["check"],
    )
    assert "No new upgrade operations detected" in check_result.stdout


def test_0007_online_and_offline_downgrades_refuse_without_acl_output(
    privilege_database: PrivilegeDatabase,
) -> None:
    online = _alembic(
        application_url=privilege_database.application_url,
        migration_url=privilege_database.migration_url,
        arguments=["downgrade", "0006_navigator_closed_loop"],
        check=False,
    )
    assert online.returncode != 0
    assert "Refusing to downgrade 0007" in online.stderr

    offline = _alembic(
        application_url=privilege_database.application_url,
        migration_url=privilege_database.migration_url,
        arguments=[
            "downgrade",
            "--sql",
            "0007_database_least_privilege:0006_navigator_closed_loop",
        ],
        check=False,
    )
    assert offline.returncode != 0
    assert "Refusing to downgrade 0007" in offline.stderr
    output = f"{offline.stdout}\n{offline.stderr}".upper()
    for forbidden in ("REVOKE ", "GRANT ", "ALTER FUNCTION", "UPDATE ALEMBIC_VERSION"):
        assert forbidden not in output

    engine = create_engine(privilege_database.migration_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0007_database_least_privilege"
    finally:
        engine.dispose()


def test_0007_contains_no_rejected_task_binding_logic() -> None:
    source = (
        PROJECT_ROOT
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0007_database_least_privilege.py"
    ).read_text()
    for rejected_term in (
        "ck_navigation_task_binding_required",
        "_require_bindable_task_history",
        "unbound_executing_navigation_task",
    ):
        assert rejected_term not in source
