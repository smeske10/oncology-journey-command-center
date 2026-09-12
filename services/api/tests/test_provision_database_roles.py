from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class FixtureRoles:
    migration: str
    application: str
    group: str
    extra: str


def _bootstrap_url() -> str:
    configured = os.environ["BOOTSTRAP_DATABASE_URL"]
    return make_url(configured).set(
        drivername="postgresql", database="postgres"
    ).render_as_string(hide_password=False)


@contextmanager
def _fixture_roles() -> Iterator[FixtureRoles]:
    suffix = uuid4().hex[:12]
    roles = FixtureRoles(
        migration=f"ojcc_test_m_{suffix}",
        application=f"ojcc_test_a_{suffix}",
        group=f"ojcc_test_g_{suffix}",
        extra=f"ojcc_test_x_{suffix}",
    )
    try:
        yield roles
    finally:
        with psycopg.connect(_bootstrap_url(), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP ROLE IF EXISTS {}, {}, {}, {}").format(
                    sql.Identifier(roles.application),
                    sql.Identifier(roles.migration),
                    sql.Identifier(roles.group),
                    sql.Identifier(roles.extra),
                )
            )


def _run_provisioning(roles: FixtureRoles) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.provision_database_roles",
            "--bootstrap-database-url",
            _bootstrap_url(),
            "--migration-role",
            roles.migration,
            "--migration-password",
            "migration-fixture-secret",
            "--application-role",
            roles.application,
            "--application-password",
            "application-fixture-secret",
            "--application-group",
            roles.group,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_provisioning_creates_exact_roles_and_is_idempotent() -> None:
    with _fixture_roles() as roles:
        first = _run_provisioning(roles)
        second = _run_provisioning(roles)

        assert first.returncode == second.returncode == 0
        with psycopg.connect(_bootstrap_url()) as connection:
            profiles = connection.execute(
                "SELECT rolname, rolcanlogin, rolcreatedb, rolcreaterole, rolsuper, "
                "rolreplication, rolbypassrls, rolinherit FROM pg_roles "
                "WHERE rolname = ANY(%s) ORDER BY rolname",
                ([roles.migration, roles.application, roles.group],),
            ).fetchall()
            memberships = connection.execute(
                "SELECT member.rolname, granted.rolname, membership.inherit_option, "
                "membership.set_option, membership.admin_option "
                "FROM pg_auth_members membership "
                "JOIN pg_roles member ON member.oid = membership.member "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "WHERE member.rolname = ANY(%s)",
                ([roles.migration, roles.application, roles.group],),
            ).fetchall()

        assert profiles == sorted(
            [
                (roles.application, True, False, False, False, False, False, True),
                (roles.group, False, False, False, False, False, False, True),
                (roles.migration, True, True, False, False, False, False, True),
            ]
        )
        assert memberships == [(roles.application, roles.group, True, False, False)]


def test_provisioning_refuses_outgoing_membership_drift_without_repair() -> None:
    with _fixture_roles() as roles:
        assert _run_provisioning(roles).returncode == 0
        with psycopg.connect(_bootstrap_url(), autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(roles.extra))
            )
            connection.execute(
                sql.SQL(
                    "GRANT {} TO {} WITH INHERIT FALSE, SET FALSE, ADMIN FALSE"
                ).format(sql.Identifier(roles.extra), sql.Identifier(roles.group))
            )

        result = _run_provisioning(roles)

        assert result.returncode != 0
        assert "unexpected outgoing role membership" in result.stderr
        with psycopg.connect(_bootstrap_url()) as connection:
            membership = connection.execute(
                "SELECT membership.inherit_option, membership.set_option, "
                "membership.admin_option FROM pg_auth_members membership "
                "JOIN pg_roles member ON member.oid = membership.member "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "WHERE member.rolname = %s AND granted.rolname = %s",
                (roles.group, roles.extra),
            ).fetchone()
        assert membership == (False, False, False)


@pytest.mark.parametrize("drift_kind", ["role profile", "membership options"])
def test_provisioning_refuses_existing_drift_without_repair(drift_kind: str) -> None:
    with _fixture_roles() as roles:
        assert _run_provisioning(roles).returncode == 0
        with psycopg.connect(_bootstrap_url(), autocommit=True) as connection:
            if drift_kind == "role profile":
                connection.execute(
                    sql.SQL("ALTER ROLE {} LOGIN").format(sql.Identifier(roles.group))
                )
            else:
                connection.execute(
                    sql.SQL("GRANT {} TO {} WITH SET TRUE").format(
                        sql.Identifier(roles.group),
                        sql.Identifier(roles.application),
                    )
                )

        result = _run_provisioning(roles)

        assert result.returncode != 0
        with psycopg.connect(_bootstrap_url()) as connection:
            if drift_kind == "role profile":
                assert connection.execute(
                    "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
                    (roles.group,),
                ).fetchone() == (True,)
            else:
                assert connection.execute(
                    "SELECT membership.set_option FROM pg_auth_members membership "
                    "JOIN pg_roles member ON member.oid = membership.member "
                    "JOIN pg_roles granted ON granted.oid = membership.roleid "
                    "WHERE member.rolname = %s AND granted.rolname = %s",
                    (roles.application, roles.group),
                ).fetchone() == (True,)
