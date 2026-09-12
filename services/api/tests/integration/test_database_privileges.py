from __future__ import annotations

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
from tests.database_support import (
    DisposableDatabase,
    alembic_environment,
    bootstrap_database_url,
    disposable_database,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATABASE_PREFIX = "ojcc_migration_test_"

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


def _alembic_environment(*, application_url: str, migration_url: str) -> dict[str, str]:
    target = make_url(migration_url)
    assert target.database is not None
    return alembic_environment(
        DisposableDatabase(
            name=target.database,
            migration_url=migration_url,
            application_url=application_url,
        )
    )


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


@contextmanager
def _provision_privilege_database(
    *, revision: str = "head"
) -> Iterator[PrivilegeDatabase]:
    with disposable_database(prefix=DATABASE_PREFIX, migrate_to=revision) as shared:
        yield PrivilegeDatabase(
            name=shared.name,
            bootstrap_url=bootstrap_database_url(shared),
            migration_url=shared.migration_url,
            application_url=shared.application_url,
        )


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
                        "AND class.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
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


def _relation_digest_snapshot(
    database: PrivilegeDatabase,
) -> tuple[tuple[str, str], ...]:
    with psycopg.connect(_psycopg_url(make_url(database.migration_url))) as connection:
        with connection.cursor() as cursor:
            digests: list[tuple[str, str]] = []
            for relation_name in sorted(APPLICATION_TABLES):
                cursor.execute(
                    sql.SQL(
                        "SELECT md5(coalesce(string_agg(to_jsonb(row_value)::text, '|' "
                        "ORDER BY to_jsonb(row_value)::text), '')) FROM public.{} AS row_value"
                    ).format(sql.Identifier(relation_name))
                )
                digest = cursor.fetchone()
                assert digest is not None
                digests.append((relation_name, digest[0]))
            return tuple(digests)


def _assert_runtime_statement_is_forbidden(
    database: PrivilegeDatabase,
    statement: sql.Composable,
) -> None:
    with psycopg.connect(
        _psycopg_url(make_url(database.application_url))
    ) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(statement)


def _assert_runtime_object_grant_is_a_noop(database: PrivilegeDatabase) -> None:
    notices: list[str] = []
    with psycopg.connect(
        _psycopg_url(make_url(database.application_url))
    ) as connection:
        connection.add_notice_handler(lambda diagnostic: notices.append(diagnostic.message_primary))
        connection.execute("GRANT SELECT ON public.organization TO PUBLIC")
    assert any("no privileges were granted" in notice for notice in notices)


def _first_column_by_relation(database: PrivilegeDatabase) -> dict[str, str]:
    engine = create_engine(database.migration_url)
    try:
        with engine.connect() as connection:
            return {
                row.table_name: row.column_name
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT ON (table_name) table_name, column_name "
                        "FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name = ANY(:relations) "
                        "ORDER BY table_name, ordinal_position"
                    ),
                    {"relations": sorted(APPLICATION_TABLES)},
                )
            }
    finally:
        engine.dispose()


def test_runtime_cannot_escape_the_complete_denied_sql_surface(
    privilege_database: PrivilegeDatabase,
) -> None:
    first_columns = _first_column_by_relation(privilege_database)
    assert set(first_columns) == APPLICATION_TABLES
    groups: tuple[tuple[str, tuple[sql.Composable, ...]], ...] = (
        (
            "schema mutation",
            (
                sql.SQL("CREATE TABLE public.runtime_escape (id integer)"),
                sql.SQL("CREATE SCHEMA runtime_escape"),
                sql.SQL(
                    "CREATE FUNCTION public.runtime_escape() RETURNS integer "
                    "LANGUAGE sql AS 'SELECT 1'"
                ),
                sql.SQL("CREATE TEMP TABLE runtime_escape (id integer)"),
                sql.SQL("ALTER TABLE public.organization ADD COLUMN runtime_escape integer"),
                sql.SQL(
                    "ALTER FUNCTION public.safety_severity_rank(safety_severity) "
                    "RENAME TO runtime_escape"
                ),
                sql.SQL("DROP TABLE public.organization"),
                sql.SQL("TRUNCATE TABLE public.organization"),
            ),
        ),
        (
            "role escalation",
            (
                sql.SQL("GRANT ojcc_app TO ojcc_api"),
                sql.SQL("CREATE ROLE runtime_escape"),
                sql.SQL("SET ROLE ojcc_app"),
                sql.SQL("SET ROLE ojcc_migrator"),
            ),
        ),
        (
            "delete",
            tuple(
                sql.SQL("DELETE FROM public.{} WHERE false").format(
                    sql.Identifier(relation_name)
                )
                for relation_name in sorted(APPLICATION_TABLES)
            ),
        ),
        (
            "out-of-matrix insert and update",
            tuple(
                sql.SQL("INSERT INTO public.{} DEFAULT VALUES").format(
                    sql.Identifier(relation_name)
                )
                for relation_name in sorted(APPLICATION_TABLES - INSERT_TABLES)
            )
            + tuple(
                sql.SQL("UPDATE public.{} SET {} = {} WHERE false").format(
                    sql.Identifier(relation_name),
                    sql.Identifier(first_columns[relation_name]),
                    sql.Identifier(first_columns[relation_name]),
                )
                for relation_name in sorted(APPLICATION_TABLES - UPDATE_TABLES)
            ),
        ),
    )

    for group_name, statements in groups:
        acl_before = _acl_snapshot(privilege_database)
        rows_before = _relation_digest_snapshot(privilege_database)
        if group_name == "role escalation":
            # PostgreSQL reports an ungrantable object privilege as a successful
            # no-op with a warning; the unchanged ACL is the security assertion.
            _assert_runtime_object_grant_is_a_noop(privilege_database)
        for statement in statements:
            _assert_runtime_statement_is_forbidden(privilege_database, statement)
        assert _acl_snapshot(privilege_database) == acl_before, group_name
        assert _relation_digest_snapshot(privilege_database) == rows_before, group_name


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


@pytest.mark.parametrize("member_role", [APPLICATION_ROLE, APPLICATION_GROUP, MIGRATION_ROLE])
def test_unexpected_outgoing_membership_preserves_0006_version_and_acl(
    member_role: str,
) -> None:
    with _provision_privilege_database(
        revision="0006_navigator_closed_loop"
    ) as database:
        extra_role = f"ojcc_test_extra_{uuid4().hex}"
        before = _acl_snapshot(database)
        try:
            with psycopg.connect(
                _psycopg_url(make_url(database.bootstrap_url)), autocommit=True
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("CREATE ROLE {} NOLOGIN").format(
                            sql.Identifier(extra_role)
                        )
                    )
                    cursor.execute(
                        sql.SQL(
                            "GRANT {} TO {} WITH INHERIT FALSE, SET FALSE, ADMIN FALSE"
                        ).format(
                            sql.Identifier(extra_role), sql.Identifier(member_role)
                        )
                    )

            result = _alembic(
                application_url=database.application_url,
                migration_url=database.migration_url,
                arguments=["upgrade", "head"],
                check=False,
            )

            assert result.returncode != 0
            assert "unexpected outgoing role membership" in result.stderr
            assert _acl_snapshot(database) == before
            engine = create_engine(database.migration_url)
            try:
                with engine.connect() as connection:
                    assert connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    ) == "0006_navigator_closed_loop"
            finally:
                engine.dispose()
        finally:
            with psycopg.connect(
                _psycopg_url(make_url(database.bootstrap_url)), autocommit=True
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("REVOKE {} FROM {}").format(
                            sql.Identifier(extra_role), sql.Identifier(member_role)
                        )
                    )
                    cursor.execute(
                        sql.SQL("DROP ROLE {}").format(sql.Identifier(extra_role))
                    )


@pytest.mark.parametrize("object_kind", ["sequence", "foreign table"])
def test_unexpected_relation_kind_preserves_0006_version_and_acl(
    object_kind: str,
) -> None:
    with _provision_privilege_database(
        revision="0006_navigator_closed_loop"
    ) as database:
        object_name = f"unexpected_{object_kind.replace(' ', '_')}_{uuid4().hex}"
        with psycopg.connect(
            _psycopg_url(make_url(database.bootstrap_url)), autocommit=True
        ) as bootstrap:
            if object_kind == "foreign table":
                bootstrap.execute("CREATE EXTENSION postgres_fdw")
                bootstrap.execute(
                    sql.SQL("GRANT USAGE ON FOREIGN DATA WRAPPER postgres_fdw TO {}").format(
                        sql.Identifier(MIGRATION_ROLE)
                    )
                )

        with psycopg.connect(
            _psycopg_url(make_url(database.migration_url)), autocommit=True
        ) as owner:
            if object_kind == "sequence":
                owner.execute(
                    sql.SQL("CREATE SEQUENCE public.{}").format(
                        sql.Identifier(object_name)
                    )
                )
                owner.execute(
                    sql.SQL("GRANT SELECT, USAGE ON SEQUENCE public.{} TO {}").format(
                        sql.Identifier(object_name), sql.Identifier(APPLICATION_ROLE)
                    )
                )
            else:
                server_name = f"unexpected_server_{uuid4().hex}"
                owner.execute(
                    sql.SQL(
                        "CREATE SERVER {} FOREIGN DATA WRAPPER postgres_fdw "
                        "OPTIONS (host '127.0.0.1', dbname 'postgres')"
                    ).format(sql.Identifier(server_name))
                )
                owner.execute(
                    sql.SQL(
                        "CREATE FOREIGN TABLE public.{} (id integer) SERVER {} "
                        "OPTIONS (table_name 'pg_class')"
                    ).format(
                        sql.Identifier(object_name), sql.Identifier(server_name)
                    )
                )
                owner.execute(
                    sql.SQL("GRANT SELECT ON TABLE public.{} TO {}").format(
                        sql.Identifier(object_name), sql.Identifier(APPLICATION_ROLE)
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
        assert object_name in result.stderr
        assert "review catalog drift before retrying" in result.stderr
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
