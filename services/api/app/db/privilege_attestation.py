"""Fail-closed runtime attestation for the frozen revision 0007 privilege boundary."""

from __future__ import annotations

from typing import NoReturn

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.db.privilege_contracts import v0007
from app.db.privilege_validation import application_relation_catalog_statement
from app.db.targets import DatabaseTarget


class RuntimePrivilegeBoundaryError(RuntimeError):
    """A sanitized runtime database boundary failure."""

    def __init__(self, boundary: str, detail: str) -> None:
        self.boundary = boundary
        super().__init__(f"{boundary}: {detail}")


def _reject(boundary: str, detail: str) -> NoReturn:
    raise RuntimePrivilegeBoundaryError(f"runtime_database_boundary.{boundary}", detail)


def _expected_relation_privilege(relation: str, privilege: str) -> bool:
    if privilege == "SELECT":
        return relation in v0007.RUNTIME_SELECT_RELATIONS
    if privilege == "INSERT":
        return relation in v0007.INSERT_RELATIONS
    if privilege == "UPDATE":
        return relation in v0007.UPDATE_RELATIONS
    return False


def _attest_identity_and_revision(
    connection: sa.Connection, *, target: DatabaseTarget
) -> None:
    identity = connection.execute(
        sa.text("SELECT current_user, session_user, current_database()")
    ).one()
    if tuple(identity) != (target.username, target.username, target.database):
        _reject("identity", "connected identity or database does not match DATABASE_URL")
    revision = connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
    if revision != v0007.REVISION:
        _reject("schema_revision", "database is not at the attested privilege revision")


def _attest_roles(connection: sa.Connection, *, application_role: str) -> None:
    roles = {
        row.rolname: tuple(row)[1:]
        for row in connection.execute(
            sa.text(
                "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
                "rolcanlogin, rolreplication, rolbypassrls FROM pg_roles "
                "WHERE rolname IN (:application_role, :application_group)"
            ),
            {
                "application_role": application_role,
                "application_group": v0007.APPLICATION_GROUP,
            },
        )
    }
    if roles != {
        application_role: (False, True, False, False, True, False, False),
        v0007.APPLICATION_GROUP: (False, True, False, False, False, False, False),
    }:
        _reject("role_profile", "runtime login or application group profile is not approved")

    memberships = {
        tuple(row)
        for row in connection.execute(
            sa.text(
                "SELECT member.rolname, granted.rolname, membership.inherit_option, "
                "membership.set_option, membership.admin_option "
                "FROM pg_auth_members membership "
                "JOIN pg_roles member ON member.oid = membership.member "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "WHERE member.rolname IN (:application_role, :application_group)"
            ),
            {
                "application_role": application_role,
                "application_group": v0007.APPLICATION_GROUP,
            },
        )
    }
    if memberships != {
        (application_role, v0007.APPLICATION_GROUP, True, False, False)
    }:
        _reject("membership", "runtime outgoing role membership graph is not approved")


def _attest_ownership(connection: sa.Connection, *, application_role: str) -> None:
    database_owner, schema_owner = connection.execute(
        sa.text(
            "SELECT database_owner.rolname, schema_owner.rolname "
            "FROM pg_database database "
            "JOIN pg_roles database_owner ON database_owner.oid = database.datdba "
            "JOIN pg_namespace namespace ON namespace.nspname = 'public' "
            "JOIN pg_roles schema_owner ON schema_owner.oid = namespace.nspowner "
            "WHERE database.datname = current_database()"
        )
    ).one()
    if application_role in {database_owner, schema_owner}:
        _reject("ownership", "runtime login owns the database or public schema")
    owns_object = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_class class "
            "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
            "JOIN pg_roles owner ON owner.oid = class.relowner "
            "WHERE namespace.nspname = 'public' AND owner.rolname = :application_role "
            "UNION ALL SELECT 1 FROM pg_proc proc "
            "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
            "JOIN pg_roles owner ON owner.oid = proc.proowner "
            "WHERE namespace.nspname = 'public' AND owner.rolname = :application_role)"
        ),
        {"application_role": application_role},
    )
    if owns_object:
        _reject("ownership", "runtime login owns a public object")


def _attest_catalog(connection: sa.Connection) -> None:
    relations = {
        (row.relname, row.relkind): row
        for row in connection.execute(application_relation_catalog_statement())
    }
    if set(relations) != set(v0007.EXPECTED_RELATION_KINDS):
        _reject("catalog", "public application relation set or kind is not approved")


def _relation_oids(connection: sa.Connection) -> dict[str, int]:
    return {
        row.relname: row.oid
        for row in connection.execute(
            sa.text(
                "SELECT class.oid, class.relname FROM pg_class class "
                "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = 'public' "
                "AND class.relname = ANY(:relations)"
            ),
            {"relations": list(v0007.CATALOG_RELATIONS)},
        )
    }


def _attest_scoped_privileges(
    connection: sa.Connection, *, application_role: str
) -> None:
    database_privileges = tuple(
        connection.execute(
            sa.text(
                "SELECT has_database_privilege(:role, current_database(), 'CONNECT'), "
                "has_database_privilege(:role, current_database(), 'CREATE'), "
                "has_database_privilege(:role, current_database(), 'TEMPORARY'), "
                "has_database_privilege(:role, current_database(), "
                "'CONNECT WITH GRANT OPTION')"
            ),
            {"role": application_role},
        ).one()
    )
    if database_privileges != (True, False, False, False):
        _reject("database_privileges", "effective database privileges are not approved")

    schema_privileges = tuple(
        connection.execute(
            sa.text(
                "SELECT has_schema_privilege(:role, 'public', 'USAGE'), "
                "has_schema_privilege(:role, 'public', 'CREATE'), "
                "has_schema_privilege(:role, 'public', 'USAGE WITH GRANT OPTION')"
            ),
            {"role": application_role},
        ).one()
    )
    if schema_privileges != (True, False, False):
        _reject("schema_privileges", "effective public schema privileges are not approved")

    relation_oids = _relation_oids(connection)
    if set(relation_oids) != set(v0007.CATALOG_RELATIONS):
        _reject("catalog", "public application relation set is not approved")
    for relation, oid in relation_oids.items():
        for privilege in v0007.TABLE_PRIVILEGES:
            actual, grantable = connection.execute(
                sa.text(
                    "SELECT has_table_privilege(:role, :oid, :privilege), "
                    "has_table_privilege(:role, :oid, :grantable)"
                ),
                {
                    "role": application_role,
                    "oid": oid,
                    "privilege": privilege,
                    "grantable": f"{privilege} WITH GRANT OPTION",
                },
            ).one()
            if bool(actual) != _expected_relation_privilege(relation, privilege) or grantable:
                _reject(
                    "relation_privileges",
                    f"effective {privilege} relation privilege is not approved for {relation}",
                )

    columns = connection.execute(
        sa.text(
            "SELECT class.relname, class.oid, attribute.attnum, attribute.attname "
            "FROM pg_attribute attribute "
            "JOIN pg_class class ON class.oid = attribute.attrelid "
            "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
            "WHERE namespace.nspname = 'public' AND attribute.attnum > 0 "
            "AND NOT attribute.attisdropped AND class.relname = ANY(:relations)"
        ),
        {"relations": list(v0007.CATALOG_RELATIONS)},
    )
    for row in columns:
        for privilege in v0007.COLUMN_PRIVILEGES:
            actual, grantable = connection.execute(
                sa.text(
                    "SELECT has_column_privilege(:role, :oid, :attnum, :privilege), "
                    "has_column_privilege(:role, :oid, :attnum, :grantable)"
                ),
                {
                    "role": application_role,
                    "oid": row.oid,
                    "attnum": row.attnum,
                    "privilege": privilege,
                    "grantable": f"{privilege} WITH GRANT OPTION",
                },
            ).one()
            if bool(actual) != _expected_relation_privilege(row.relname, privilege) or grantable:
                _reject(
                    "column_privileges",
                    f"effective {privilege} column privilege is not approved for {row.relname}",
                )


def _attest_functions(connection: sa.Connection, *, application_role: str) -> None:
    functions = {
        (row.proname, row.identity_arguments): row
        for row in connection.execute(
            sa.text(
                "SELECT proc.oid, proc.proname, "
                "pg_get_function_identity_arguments(proc.oid) AS identity_arguments "
                "FROM pg_proc proc "
                "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
                "WHERE namespace.nspname = 'public' AND NOT EXISTS ("
                "SELECT 1 FROM pg_depend dependency "
                "WHERE dependency.classid = 'pg_proc'::regclass "
                "AND dependency.objid = proc.oid AND dependency.deptype = 'e')"
            )
        )
    }
    if set(functions) != set(v0007.FUNCTION_IDENTITIES):
        _reject("catalog", "public application function set is not approved")
    for identity, row in functions.items():
        execute, grantable = connection.execute(
            sa.text(
                "SELECT has_function_privilege(:role, :oid, 'EXECUTE'), "
                "has_function_privilege(:role, :oid, 'EXECUTE WITH GRANT OPTION')"
            ),
            {"role": application_role, "oid": row.oid},
        ).one()
        expected = identity[0] == "safety_severity_rank"
        if bool(execute) != expected or grantable:
            _reject(
                "function_privileges",
                f"effective function privilege is not approved for {identity[0]}",
            )


def attest_runtime_database(
    connection: sa.Connection, *, target: DatabaseTarget
) -> None:
    """Attest the runtime session and effective revision 0007 privilege surface once."""
    try:
        _attest_identity_and_revision(connection, target=target)
        _attest_roles(connection, application_role=target.username)
        _attest_ownership(connection, application_role=target.username)
        _attest_catalog(connection)
        _attest_scoped_privileges(connection, application_role=target.username)
        _attest_functions(connection, application_role=target.username)
    except RuntimePrivilegeBoundaryError:
        raise
    except SQLAlchemyError:
        _reject("unavailable", "database attestation could not be completed")
