"""Shared catalog statements and online validation for database privilege boundaries."""

from __future__ import annotations

import sqlalchemy as sa

from app.db.privilege_contracts import v0007


def application_relation_catalog_statement() -> sa.TextClause:
    placeholders = ", ".join(
        f":relkind_{index}" for index, _ in enumerate(v0007.APPLICATION_RELKINDS)
    )
    statement = sa.text(
        "SELECT class.relname, class.relkind, owner.rolname AS owner "
        "FROM pg_class class "
        "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
        "JOIN pg_roles owner ON owner.oid = class.relowner "
        "WHERE namespace.nspname = 'public' "
        f"AND class.relkind IN ({placeholders}) "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend dependency "
        "WHERE dependency.classid = 'pg_class'::regclass "
        "AND dependency.objid = class.oid AND dependency.deptype = 'e')"
    )
    return statement.bindparams(
        **{
            f"relkind_{index}": relkind
            for index, relkind in enumerate(v0007.APPLICATION_RELKINDS)
        }
    )


def validate_v0007_relation_catalog(
    bind: sa.Connection, *, migration_role: str
) -> None:
    relations = {
        (row.relname, row.relkind): row.owner
        for row in bind.execute(application_relation_catalog_statement())
    }
    expected = set(v0007.EXPECTED_RELATION_KINDS)
    actual = set(relations)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise RuntimeError(
            "public application relation boundary does not match revision 0006; "
            f"unexpected objects (name, kind): {unexpected}; missing: {missing}; "
            "review catalog drift before retrying"
        )
    wrong_owners = sorted(
        (name, kind) for (name, kind), owner in relations.items() if owner != migration_role
    )
    if wrong_owners:
        raise RuntimeError(f"migration owner does not own relations: {wrong_owners}")


def validate_exact_outgoing_memberships(
    bind: sa.Connection,
    *,
    migration_role: str,
    application_role: str,
) -> None:
    rows = {
        (
            row.member_name,
            row.granted_name,
            row.inherit_option,
            row.set_option,
            row.admin_option,
        )
        for row in bind.execute(
            sa.text(
                "SELECT member.rolname AS member_name, granted.rolname AS granted_name, "
                "membership.inherit_option, membership.set_option, "
                "membership.admin_option FROM pg_auth_members membership "
                "JOIN pg_roles member ON member.oid = membership.member "
                "JOIN pg_roles granted ON granted.oid = membership.roleid "
                "WHERE member.rolname IN "
                "(:application_role, :application_group, :migration_role)"
            ),
            {
                "application_role": application_role,
                "application_group": v0007.APPLICATION_GROUP,
                "migration_role": migration_role,
            },
        ).mappings()
    }
    expected = {
        (
            application_role,
            v0007.APPLICATION_GROUP,
            True,
            False,
            False,
        )
    }
    if rows != expected:
        unexpected = sorted(rows - expected)
        missing = sorted(expected - rows)
        raise RuntimeError(
            "unexpected outgoing role membership; "
            f"unexpected: {unexpected}; missing approved edge: {missing}"
        )
