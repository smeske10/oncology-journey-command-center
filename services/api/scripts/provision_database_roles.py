"""Create missing OJCC roles and refuse any existing privilege drift."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.db.targets import parse_database_target


class RoleProvisioningError(RuntimeError):
    """An existing cluster role boundary is incompatible with provisioning."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create missing OJCC roles and reject existing role drift."
    )
    parser.add_argument("--bootstrap-database-url", required=True)
    parser.add_argument("--migration-role", required=True)
    parser.add_argument("--migration-password", required=True)
    parser.add_argument("--application-role", required=True)
    parser.add_argument("--application-password", required=True)
    parser.add_argument("--application-group", default="ojcc_app")
    return parser


def _psycopg_url(url_text: str) -> str:
    parse_database_target(url_text, label="BOOTSTRAP_DATABASE_URL")
    return make_url(url_text).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _validate_existing_profiles(
    cursor: psycopg.Cursor,
    *,
    migration_role: str,
    application_role: str,
    application_group: str,
) -> set[str]:
    expected = {
        migration_role: (True, True, False, False, False, False, True),
        application_role: (True, False, False, False, False, False, True),
        application_group: (False, False, False, False, False, False, True),
    }
    rows = cursor.execute(
        "SELECT rolname, rolcanlogin, rolcreatedb, rolcreaterole, rolsuper, "
        "rolreplication, rolbypassrls, rolinherit FROM pg_roles "
        "WHERE rolname = ANY(%s)",
        (list(expected),),
    ).fetchall()
    for row in rows:
        role_name = str(row[0])
        if tuple(row[1:]) != expected[role_name]:
            raise RoleProvisioningError(
                f"existing role has unexpected properties: {role_name}"
            )
    return {str(row[0]) for row in rows}


def _validate_existing_memberships(
    cursor: psycopg.Cursor,
    *,
    migration_role: str,
    application_role: str,
    application_group: str,
) -> bool:
    rows = {
        tuple(row)
        for row in cursor.execute(
            "SELECT member.rolname, granted.rolname, membership.inherit_option, "
            "membership.set_option, membership.admin_option "
            "FROM pg_auth_members membership "
            "JOIN pg_roles member ON member.oid = membership.member "
            "JOIN pg_roles granted ON granted.oid = membership.roleid "
            "WHERE member.rolname = ANY(%s)",
            ([migration_role, application_role, application_group],),
        ).fetchall()
    }
    approved = (application_role, application_group, True, False, False)
    unexpected = rows - {approved}
    if unexpected:
        raise RoleProvisioningError(
            f"unexpected outgoing role membership: {sorted(unexpected)}"
        )
    if rows and rows != {approved}:
        raise RoleProvisioningError("approved membership has unexpected options")
    return approved in rows


def _create_role(
    cursor: psycopg.Cursor,
    *,
    role_name: str,
    login: bool,
    createdb: bool,
    password: str | None,
) -> None:
    login_sql = sql.SQL("LOGIN") if login else sql.SQL("NOLOGIN")
    createdb_sql = sql.SQL("CREATEDB") if createdb else sql.SQL("NOCREATEDB")
    password_sql = (
        sql.SQL(" PASSWORD {}").format(sql.Literal(password))
        if password is not None
        else sql.SQL("")
    )
    cursor.execute(
        sql.SQL(
            "CREATE ROLE {} {} {} NOCREATEROLE NOSUPERUSER "
            "NOREPLICATION NOBYPASSRLS INHERIT{}"
        ).format(
            sql.Identifier(role_name),
            login_sql,
            createdb_sql,
            password_sql,
        )
    )


def provision_database_roles(
    *,
    bootstrap_database_url: str,
    migration_role: str,
    migration_password: str,
    application_role: str,
    application_password: str,
    application_group: str,
) -> None:
    if len({migration_role, application_role, application_group}) != 3:
        raise RoleProvisioningError("migration, application, and group roles must be distinct")
    with psycopg.connect(_psycopg_url(bootstrap_database_url)) as connection:
        with connection.cursor() as cursor:
            existing = _validate_existing_profiles(
                cursor,
                migration_role=migration_role,
                application_role=application_role,
                application_group=application_group,
            )
            membership_exists = _validate_existing_memberships(
                cursor,
                migration_role=migration_role,
                application_role=application_role,
                application_group=application_group,
            )
            if migration_role not in existing:
                _create_role(
                    cursor,
                    role_name=migration_role,
                    login=True,
                    createdb=True,
                    password=migration_password,
                )
            if application_role not in existing:
                _create_role(
                    cursor,
                    role_name=application_role,
                    login=True,
                    createdb=False,
                    password=application_password,
                )
            if application_group not in existing:
                _create_role(
                    cursor,
                    role_name=application_group,
                    login=False,
                    createdb=False,
                    password=None,
                )
            if not membership_exists:
                cursor.execute(
                    sql.SQL(
                        "GRANT {} TO {} WITH INHERIT TRUE, SET FALSE, ADMIN FALSE"
                    ).format(
                        sql.Identifier(application_group),
                        sql.Identifier(application_role),
                    )
                )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        provision_database_roles(
            bootstrap_database_url=arguments.bootstrap_database_url,
            migration_role=arguments.migration_role,
            migration_password=arguments.migration_password,
            application_role=arguments.application_role,
            application_password=arguments.application_password,
            application_group=arguments.application_group,
        )
    except (RoleProvisioningError, psycopg.Error) as error:
        message = (
            str(error)
            if isinstance(error, RoleProvisioningError)
            else "database role provisioning could not be completed"
        )
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
