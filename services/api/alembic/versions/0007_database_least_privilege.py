"""Enforce the complete least-privilege database surface."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from app.config import settings
from app.db.privilege_contracts import v0007
from app.db.privilege_validation import (
    validate_exact_outgoing_memberships,
    validate_v0007_relation_catalog,
)
from app.db.targets import validate_database_target_pair

revision: str = "0007_database_least_privilege"
down_revision: str | None = "0006_navigator_closed_loop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_GROUP = v0007.APPLICATION_GROUP
SELECT_ONLY_RELATIONS = v0007.SELECT_ONLY_RELATIONS
INSERT_RELATIONS = v0007.INSERT_RELATIONS
UPDATE_RELATIONS = v0007.UPDATE_RELATIONS
APPLICATION_VIEWS = v0007.APPLICATION_VIEWS
APPLICATION_RELATIONS = v0007.APPLICATION_RELATIONS
CATALOG_RELATIONS = v0007.CATALOG_RELATIONS
SECURITY_DEFINER_FUNCTIONS = v0007.SECURITY_DEFINER_FUNCTIONS
SECURITY_INVOKER_FUNCTIONS = v0007.SECURITY_INVOKER_FUNCTIONS
APPLICATION_FUNCTIONS = v0007.APPLICATION_FUNCTIONS


def _function_identity(function_name: str) -> str:
    arguments = "safety_severity" if function_name == "safety_severity_rank" else ""
    return f"public.{function_name}({arguments})"


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _offline_expected_relations() -> str:
    return ",\n                ".join(
        f"({_sql_literal(name)}, {_sql_literal(kind)})"
        for name, kind in v0007.EXPECTED_RELATION_KINDS
    )


def _offline_expected_functions() -> str:
    values = []
    for name in APPLICATION_FUNCTIONS:
        arguments = "value safety_severity" if name == "safety_severity_rank" else ""
        values.append(f"({_sql_literal(name)}, {_sql_literal(arguments)})")
    return ",\n                ".join(values)


def _emit_offline_preflight(*, migration_role: str, application_role: str) -> None:
    migration_literal = _sql_literal(migration_role)
    application_literal = _sql_literal(application_role)
    group_literal = _sql_literal(APPLICATION_GROUP)
    expected_relations = _offline_expected_relations()
    expected_functions = _offline_expected_functions()
    op.execute(
        f"""
        -- DATABASE PRIVILEGE PREFLIGHT 0007
        DO $ojcc_preflight$
        DECLARE
            migration_role constant text := {migration_literal};
            application_role constant text := {application_literal};
            application_group constant text := {group_literal};
            drift_name text;
            drift_kind text;
        BEGIN
            IF migration_role = application_role
               OR migration_role = application_group
               OR application_role = application_group THEN
                RAISE EXCEPTION 'database privilege preflight requires three distinct roles';
            END IF;

            IF (SELECT count(*) FROM pg_roles WHERE rolname IN
                (migration_role, application_role, application_group)) <> 3 THEN
                RAISE EXCEPTION 'database privilege preflight is missing required roles';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = migration_role
                  AND (NOT rolcanlogin OR NOT rolinherit OR rolsuper OR rolcreaterole
                       OR rolreplication OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION 'database privilege preflight rejected migration owner profile';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = application_role
                  AND (NOT rolcanlogin OR NOT rolinherit OR rolsuper OR rolcreaterole
                       OR rolcreatedb OR rolreplication OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION 'database privilege preflight rejected application role profile';
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = application_group
                  AND (rolcanlogin OR rolsuper OR rolcreaterole OR rolcreatedb
                       OR rolreplication OR rolbypassrls)
            ) THEN
                RAISE EXCEPTION 'database privilege preflight rejected application group profile';
            END IF;

            IF (SELECT count(*) FROM pg_auth_members membership
                JOIN pg_roles member ON member.oid = membership.member
                WHERE member.rolname IN
                    (migration_role, application_role, application_group)) <> 1
               OR NOT EXISTS (
                    SELECT 1 FROM pg_auth_members membership
                    JOIN pg_roles member ON member.oid = membership.member
                    JOIN pg_roles granted ON granted.oid = membership.roleid
                    WHERE member.rolname = application_role
                      AND granted.rolname = application_group
                      AND membership.inherit_option
                      AND NOT membership.set_option
                      AND NOT membership.admin_option
               ) THEN
                RAISE EXCEPTION 'unexpected outgoing role membership';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_database database
                JOIN pg_roles owner ON owner.oid = database.datdba
                WHERE database.datname = current_database()
                  AND owner.rolname = migration_role
                  AND current_user = migration_role
            ) OR NOT EXISTS (
                SELECT 1 FROM pg_namespace namespace
                JOIN pg_roles owner ON owner.oid = namespace.nspowner
                WHERE namespace.nspname = 'public' AND owner.rolname = migration_role
            ) THEN
                RAISE EXCEPTION 'migration login must own the target database and public schema';
            END IF;

            WITH expected(relname, relkind) AS (
                VALUES {expected_relations}
            ), actual AS (
                SELECT class.relname, class.relkind::text AS relkind,
                       owner.rolname AS owner
                FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                JOIN pg_roles owner ON owner.oid = class.relowner
                WHERE namespace.nspname = 'public'
                  AND class.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_class'::regclass
                        AND dependency.objid = class.oid
                        AND dependency.deptype = 'e'
                  )
            )
            SELECT coalesce(actual.relname, expected.relname),
                   coalesce(actual.relkind, expected.relkind)
              INTO drift_name, drift_kind
              FROM expected FULL JOIN actual USING (relname, relkind)
             WHERE expected.relname IS NULL OR actual.relname IS NULL
                OR actual.owner <> migration_role
             ORDER BY 1, 2 LIMIT 1;
            IF drift_name IS NOT NULL THEN
                RAISE EXCEPTION
                    'public application relation boundary drift: object % kind %; '
                    'review catalog drift before retrying',
                    drift_name, drift_kind;
            END IF;

            drift_name := NULL;
            WITH expected(proname, identity_arguments) AS (
                VALUES {expected_functions}
            ), actual AS (
                SELECT proc.proname,
                       pg_get_function_identity_arguments(proc.oid) AS identity_arguments,
                       owner.rolname AS owner
                FROM pg_proc proc
                JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
                JOIN pg_roles owner ON owner.oid = proc.proowner
                WHERE namespace.nspname = 'public'
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_proc'::regclass
                        AND dependency.objid = proc.oid
                        AND dependency.deptype = 'e'
                  )
            )
            SELECT coalesce(actual.proname, expected.proname)
              INTO drift_name
              FROM expected FULL JOIN actual USING (proname, identity_arguments)
             WHERE expected.proname IS NULL OR actual.proname IS NULL
                OR actual.owner <> migration_role
             ORDER BY 1 LIMIT 1;
            IF drift_name IS NOT NULL THEN
                RAISE EXCEPTION
                    'public application function boundary drift: function %', drift_name;
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                JOIN pg_roles owner ON owner.oid = class.relowner
                WHERE namespace.nspname = 'public' AND owner.rolname = application_role
                UNION ALL
                SELECT 1 FROM pg_proc proc
                JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
                JOIN pg_roles owner ON owner.oid = proc.proowner
                WHERE namespace.nspname = 'public' AND owner.rolname = application_role
            ) THEN
                RAISE EXCEPTION 'application login must not own public objects';
            END IF;

            IF EXISTS (
                SELECT 1 FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                JOIN pg_roles owner ON owner.oid = class.relowner
                WHERE namespace.nspname = 'public'
                  AND class.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'i', 'I')
                  AND owner.rolname <> migration_role
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_class'::regclass
                        AND dependency.objid = class.oid
                        AND dependency.deptype = 'e'
                  )
                UNION ALL
                SELECT 1 FROM pg_type type
                JOIN pg_namespace namespace ON namespace.oid = type.typnamespace
                JOIN pg_roles owner ON owner.oid = type.typowner
                WHERE namespace.nspname = 'public'
                  AND type.typtype IN ('c', 'd', 'e', 'm', 'r')
                  AND owner.rolname <> migration_role
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_type'::regclass
                        AND dependency.objid = type.oid
                        AND dependency.deptype = 'e'
                  )
            ) THEN
                RAISE EXCEPTION 'migration login must own every non-extension public object';
            END IF;
        END
        $ojcc_preflight$
        """
    )


def _role_profile(bind: sa.Connection, role_names: tuple[str, str]) -> dict[str, dict]:
    rows = bind.execute(
        sa.text(
            "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
            "rolcanlogin, rolreplication, rolbypassrls FROM pg_roles "
            "WHERE rolname IN (:migration_role, :application_role, :application_group)"
        ),
        {
            "migration_role": role_names[0],
            "application_role": role_names[1],
            "application_group": APPLICATION_GROUP,
        },
    ).mappings()
    return {row["rolname"]: dict(row) for row in rows}


def _validate_role_profile(
    bind: sa.Connection, *, migration_role: str, application_role: str
) -> None:
    if APPLICATION_GROUP in {migration_role, application_role}:
        raise RuntimeError("migration, application, and group roles must be distinct")

    roles = _role_profile(bind, (migration_role, application_role))
    expected_names = {migration_role, application_role, APPLICATION_GROUP}
    if set(roles) != expected_names:
        missing = sorted(expected_names - set(roles))
        raise RuntimeError(f"database privilege preflight is missing roles: {missing}")

    owner = roles[migration_role]
    if not owner["rolcanlogin"] or not owner["rolinherit"]:
        raise RuntimeError("migration owner must be a LOGIN role with INHERIT")
    forbidden_owner_fields = (
        "rolsuper",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
    )
    if any(owner[field] for field in forbidden_owner_fields):
        raise RuntimeError(
            "migration owner must be NOSUPERUSER, NOCREATEROLE, "
            "NOREPLICATION, and NOBYPASSRLS; "
            f"CREATEDB={owner['rolcreatedb']} CREATEROLE={owner['rolcreaterole']}"
        )

    application = roles[application_role]
    if not application["rolcanlogin"] or not application["rolinherit"]:
        raise RuntimeError("application role must be a LOGIN role with INHERIT")
    if any(
        application[field]
        for field in (
            "rolsuper",
            "rolcreaterole",
            "rolcreatedb",
            "rolreplication",
            "rolbypassrls",
        )
    ):
        raise RuntimeError("application role has a forbidden cluster capability")

    group = roles[APPLICATION_GROUP]
    if group["rolcanlogin"] or any(
        group[field]
        for field in (
            "rolsuper",
            "rolcreaterole",
            "rolcreatedb",
            "rolreplication",
            "rolbypassrls",
        )
    ):
        raise RuntimeError("application group must be a capability-free NOLOGIN role")

    validate_exact_outgoing_memberships(
        bind,
        migration_role=migration_role,
        application_role=application_role,
    )


def _validate_ownership(
    bind: sa.Connection, *, migration_role: str, application_role: str
) -> None:
    current_user, database_owner, schema_owner = bind.execute(
        sa.text(
            "SELECT current_user, database_owner.rolname, schema_owner.rolname "
            "FROM pg_database database "
            "JOIN pg_roles database_owner ON database_owner.oid = database.datdba "
            "JOIN pg_namespace namespace ON namespace.nspname = 'public' "
            "JOIN pg_roles schema_owner ON schema_owner.oid = namespace.nspowner "
            "WHERE database.datname = current_database()"
        )
    ).one()
    if (current_user, database_owner, schema_owner) != (
        migration_role,
        migration_role,
        migration_role,
    ):
        raise RuntimeError("migration login must own the target database and public schema")

    validate_v0007_relation_catalog(bind, migration_role=migration_role)

    functions = {
        (row.proname, row.identity_arguments): row.owner
        for row in bind.execute(
            sa.text(
                "SELECT proc.proname, pg_get_function_identity_arguments(proc.oid) "
                "AS identity_arguments, owner.rolname AS owner FROM pg_proc proc "
                "JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace "
                "JOIN pg_roles owner ON owner.oid = proc.proowner "
                "WHERE namespace.nspname = 'public' AND NOT EXISTS ("
                "SELECT 1 FROM pg_depend dependency "
                "WHERE dependency.classid = 'pg_proc'::regclass "
                "AND dependency.objid = proc.oid AND dependency.deptype = 'e')"
            )
        )
    }
    expected_functions = {
        (
            name,
            "value safety_severity" if name == "safety_severity_rank" else "",
        )
        for name in APPLICATION_FUNCTIONS
    }
    if set(functions) != expected_functions:
        raise RuntimeError("public application function set does not match revision 0006")
    wrong_functions = sorted(
        name for name, owner in functions.items() if owner != migration_role
    )
    if wrong_functions:
        raise RuntimeError(f"migration owner does not own functions: {wrong_functions}")

    application_owned = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_class class JOIN pg_namespace namespace "
            "ON namespace.oid = class.relnamespace JOIN pg_roles owner "
            "ON owner.oid = class.relowner WHERE namespace.nspname = 'public' "
            "AND owner.rolname = :application_role "
            "UNION ALL SELECT 1 FROM pg_proc proc JOIN pg_namespace namespace "
            "ON namespace.oid = proc.pronamespace JOIN pg_roles owner "
            "ON owner.oid = proc.proowner WHERE namespace.nspname = 'public' "
            "AND owner.rolname = :application_role)"
        ),
        {"application_role": application_role},
    )
    if application_owned:
        raise RuntimeError("application login must not own public objects")

    wrong_owned_objects = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_class class JOIN pg_namespace namespace "
            "ON namespace.oid = class.relnamespace JOIN pg_roles owner "
            "ON owner.oid = class.relowner WHERE namespace.nspname = 'public' "
            "AND class.relkind IN ('r', 'p', 'v', 'm', 'S', 'i', 'I') "
            "AND owner.rolname <> :migration_role AND NOT EXISTS ("
            "SELECT 1 FROM pg_depend dependency "
            "WHERE dependency.classid = 'pg_class'::regclass "
            "AND dependency.objid = class.oid AND dependency.deptype = 'e') "
            "UNION ALL SELECT 1 FROM pg_type type JOIN pg_namespace namespace "
            "ON namespace.oid = type.typnamespace JOIN pg_roles owner "
            "ON owner.oid = type.typowner WHERE namespace.nspname = 'public' "
            "AND type.typtype IN ('c', 'd', 'e', 'm', 'r') "
            "AND owner.rolname <> :migration_role AND NOT EXISTS ("
            "SELECT 1 FROM pg_depend dependency "
            "WHERE dependency.classid = 'pg_type'::regclass "
            "AND dependency.objid = type.oid AND dependency.deptype = 'e'))"
        ),
        {"migration_role": migration_role},
    )
    if wrong_owned_objects:
        raise RuntimeError("migration login must own every non-extension public object")


def _set_application_role(bind: sa.Connection, *, application_role: str) -> None:
    bind.execute(
        sa.text("SELECT set_config('ojcc.application_role', :application_role, true)"),
        {"application_role": application_role},
    )


def _revoke_dynamic_application_privileges() -> None:
    op.execute(
        """
        DO $ojcc$
        DECLARE
            application_role text := current_setting('ojcc.application_role');
            relation_name text;
            function_oid oid;
        BEGIN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
                current_database(), application_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', application_role
            );

            FOR relation_name IN
                SELECT class.relname FROM pg_class class
                JOIN pg_namespace namespace ON namespace.oid = class.relnamespace
                WHERE namespace.nspname = 'public'
                  AND class.relkind IN ('r', 'p', 'v', 'm')
            LOOP
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                    relation_name, application_role
                );
            END LOOP;

            FOR function_oid IN
                SELECT proc.oid FROM pg_proc proc
                JOIN pg_namespace namespace ON namespace.oid = proc.pronamespace
                WHERE namespace.nspname = 'public'
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_depend dependency
                      WHERE dependency.classid = 'pg_proc'::regclass
                        AND dependency.objid = proc.oid
                        AND dependency.deptype = 'e'
                  )
            LOOP
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I',
                    function_oid::regprocedure, application_role
                );
            END LOOP;
        END
        $ojcc$
        """
    )


def _configure_database_and_schema() -> None:
    op.execute(
        """
        DO $ojcc$
        BEGIN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC', current_database()
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON DATABASE %I FROM ojcc_app', current_database()
            );
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO ojcc_app', current_database()
            );
        END
        $ojcc$
        """
    )
    op.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC, ojcc_app")
    op.execute("GRANT USAGE ON SCHEMA public TO ojcc_app")


def _configure_relations() -> None:
    for relation_name in CATALOG_RELATIONS:
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{relation_name} FROM PUBLIC, ojcc_app"
        )

    for relation_name in (*SELECT_ONLY_RELATIONS, *APPLICATION_VIEWS):
        op.execute(f"GRANT SELECT ON TABLE public.{relation_name} TO ojcc_app")
    for relation_name in INSERT_RELATIONS:
        op.execute(
            f"GRANT SELECT, INSERT ON TABLE public.{relation_name} TO ojcc_app"
        )
    for relation_name in UPDATE_RELATIONS:
        op.execute(
            f"GRANT SELECT, UPDATE ON TABLE public.{relation_name} TO ojcc_app"
        )


def _configure_functions() -> None:
    for function_name in APPLICATION_FUNCTIONS:
        function_identity = _function_identity(function_name)
        op.execute(f"ALTER FUNCTION {function_identity} SECURITY INVOKER")
        op.execute(f"ALTER FUNCTION {function_identity} RESET ALL")
        op.execute(
            f"REVOKE ALL PRIVILEGES ON FUNCTION {function_identity} FROM PUBLIC, ojcc_app"
        )

    for function_name in SECURITY_DEFINER_FUNCTIONS:
        function_identity = _function_identity(function_name)
        op.execute(f"ALTER FUNCTION {function_identity} SECURITY DEFINER")
        op.execute(
            f"ALTER FUNCTION {function_identity} "
            "SET search_path = pg_catalog, public, pg_temp"
        )

    op.execute(
        "GRANT EXECUTE ON FUNCTION public.safety_severity_rank(safety_severity) "
        "TO ojcc_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )


def upgrade() -> None:
    application_target, migration_target = validate_database_target_pair(
        application_url=settings.database_url,
        migration_url=settings.require_migration_database_url(),
    )
    if context.is_offline_mode():
        _emit_offline_preflight(
            migration_role=migration_target.username,
            application_role=application_target.username,
        )
        op.execute(
            "SELECT set_config('ojcc.application_role', "
            f"{_sql_literal(application_target.username)}, true)"
        )
    else:
        bind = op.get_bind()
        _validate_role_profile(
            bind,
            migration_role=migration_target.username,
            application_role=application_target.username,
        )
        _validate_ownership(
            bind,
            migration_role=migration_target.username,
            application_role=application_target.username,
        )
        _set_application_role(bind, application_role=application_target.username)

    _revoke_dynamic_application_privileges()
    _configure_database_and_schema()
    _configure_relations()
    _configure_functions()


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to downgrade 0007: reverting would restore owner-runtime and "
        "trigger-table write exposure. Preserve this database or restore a reviewed "
        "pre-0007 snapshot instead."
    )
