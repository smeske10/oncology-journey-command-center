# Database Privilege Closure Design

**Date:** 2026-09-12
**Status:** Approved in chat; awaiting written-spec review
**Branch:** `feature/database-least-privilege`

## Purpose

Close three pre-PR gaps in the database least-privilege milestone without expanding the product
surface: unexpected inherited roles, privileged runtime credential miswiring, and unreviewed public
catalog object kinds. Preserve online and offline Alembic workflows and keep all database changes
failure-atomic.

## Exact role-membership closure

The allowed membership graph has one edge only: the configured API login is a direct member of
`ojcc_app` with `INHERIT = true`, `SET = false`, and `ADMIN = false`.

- The API login must have no other direct or transitive granted roles.
- `ojcc_app` must not itself be a member of another role.
- The migration owner must not be a member of another role.
- Existing incoming members of `ojcc_app` do not grant capabilities to the configured API login and
  are outside this target-specific boundary.

Migration 0007 validates this complete outgoing graph before changing an ACL or Alembic version.
Local and CI provisioning apply the same validation and refuse drift rather than repairing it.

## Runtime database attestation

FastAPI verifies the database boundary once during application startup using only `DATABASE_URL`.
The runtime process never requires or receives bootstrap or migration-owner credentials.

The connected session must satisfy all of the following:

- `current_user` and `session_user` both equal the username decoded from `DATABASE_URL`;
- the login is non-superuser, LOGIN, INHERIT, NOCREATEDB, NOCREATEROLE, NOREPLICATION, and
  NOBYPASSRLS;
- the login's outgoing role graph is exactly the approved `ojcc_app` edge;
- `ojcc_app` has the capability-free NOLOGIN profile and no outgoing memberships;
- the login owns no database, public schema, application relation, or application function;
- effective database, schema, relation, and function privileges match the approved matrix, with no
  grant options or extra write capabilities.

Startup fails closed on any mismatch. The health endpoint reports ready only after attestation has
succeeded. Tests call the attestation boundary directly for valid API credentials, owner
credentials, and adversarial extra-membership cases; the live Uvicorn journey proves startup uses
it in the real server lifecycle.

## Online and offline migration preflight

Online migration keeps its result-reading preflight and strengthens it with exact membership and
catalog-kind checks.

Offline `upgrade` generation cannot read catalogs. Migration 0007 therefore emits PostgreSQL
`DO` blocks that perform the same role, membership, ownership, and catalog checks when the SQL
artifact is executed. Generation succeeds without opening a connection, and the emitted checks
precede every 0007 ACL mutation. The surrounding Alembic transaction makes a failed execution
atomic.

Offline downgrade continues to refuse before emitting ACL or version mutations.

## Catalog closure

The migration's public catalog scan includes ordinary tables, partitioned tables, views,
materialized views, sequences, and foreign tables. The approved set remains the existing tables and
views only. Any owner-controlled sequence or foreign table is unexpected drift, so migration 0007
refuses before revoking or granting anything.

This refusal is safer than trying to guess privileges for an unapproved object. Tests create an
API-granted sequence and, where the local PostgreSQL installation supports it, an API-granted
foreign table; both must preserve the 0006 version and captured ACL digest when 0007 refuses.

## Shared contract and boundaries

A small application-owned privilege-contract module contains immutable expected role, relation,
view, and function metadata used by migration, runtime attestation, and tests. It contains no
credentials and opens no connections. Migration-specific rendering remains in migration 0007;
runtime startup remains in the API database/session layer.

CI and README provisioning SQL mirror the shared contract explicitly because they execute outside
the Python application. Tests prevent those copies from drifting.

## Error handling and safety

- Every rejection names the violated boundary without printing URLs or passwords.
- Migration refusal occurs before 0007 ACL or version changes.
- Runtime refusal prevents the server from becoming ready.
- Tests use only freshly named, recorded disposable databases and ordinary cleanup.
- No force-drop, connection termination, persistent-database mutation, deployment, or merge is in
  scope.

## Verification

Development follows red-green cycles for:

1. extra direct and transitive API memberships;
2. outgoing memberships from `ojcc_app` and the migration owner;
3. API startup with valid runtime credentials versus owner or drifted credentials;
4. offline `0006:head` and fresh `head` SQL generation with execution-time preflight preceding ACL
   changes;
5. unexpected sequence and foreign-table drift with failure-atomic preservation;
6. CI and README provisioning parity.

After focused tests pass, rerun immutable migration hashes, the full privilege/non-owner security
gate, the complete repository verifier, and an independent read-only code review. Only then push
the branch and create the pull request against `master`.

## Non-goals

- Changing the approved application write matrix.
- Creating or repairing production roles from Alembic.
- Adding patient-facing or navigator-facing behavior.
- Altering immutable migrations 0001–0006.
- Merging, deploying, or deleting the worktree.
