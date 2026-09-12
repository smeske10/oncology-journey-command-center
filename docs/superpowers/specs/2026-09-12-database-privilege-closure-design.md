# Database Privilege Closure Design

**Date:** 2026-09-12
**Status:** Implemented, independently reviewed, and verified; ready for pull request
**Branch:** `feature/database-least-privilege`

## Purpose

Close two validation gaps in the database least-privilege milestone: incomplete role-membership
checking and incomplete catalog object-kind coverage. Add two capabilities: execution-time
preflight for offline SQL artifacts and runtime credential attestation at API startup. Consolidating
the expected-privilege metadata is supporting work for those four behavior changes.

There are no patient-facing or navigator-facing changes. Online execution currently works; offline
generation through 0007 does not. At completion, online upgrades and the documented offline
execution paths below must both work. Atomicity is defined per migration transaction or replay
stage, not across multiple credentialed connections.

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

### Threat model and timing

- **T1 — misconfiguration:** the runtime URL points at the migration owner, bootstrap account, or
  another privileged login.
- **T2 — provisioning drift:** privileges or memberships have been added out of band and are
  present when the application starts.
- **Excluded — malicious database administrator:** attestation does not defend against an actor
  who can alter database catalogs and the objects being checked.

Attestation is a startup snapshot. It does not detect grants made after startup, attest every pooled
connection or request, or continuously monitor privileges. A restart reruns the check. Continuous
drift detection is separate work.

The connected session must satisfy all of the following:

- **T1:** `current_user` and `session_user` both equal the username decoded from `DATABASE_URL`,
  and `current_database()` equals its database name.
- **T1/T2:** the login is non-superuser, LOGIN, INHERIT, NOCREATEDB, NOCREATEROLE,
  NOREPLICATION, and NOBYPASSRLS.
- **T1/T2:** the login's outgoing role graph is exactly the approved `ojcc_app` edge.
- **T2:** `ojcc_app` has the capability-free NOLOGIN profile and no outgoing memberships.
- **T1/T2:** the login owns neither the target database nor its public schema, and owns no public
  relations or functions. The check does not require querying objects in other databases.
- **T2:** effective target-database, public-schema, relation, and function privileges match the
  frozen 0007 contract, including the expected object set and absence of grant options. Check the
  API login's effective privileges, not only ACLs addressed to `ojcc_app`; account for direct,
  inherited, PUBLIC, and column privileges that could confer additional access.

### Failure behavior

Run attestation in the FastAPI startup lifespan before serving requests, not during module import.
A mismatch or inability to complete the check raises, aborts startup, and causes the supported
Uvicorn launcher to exit nonzero. Emit a stable boundary identifier and sanitized explanation;
do not log connection URLs, passwords, or unsanitized driver exception details.

`GET /health` remains the existing liveness endpoint with `{"status":"ok"}`. A process that fails
attestation never serves it. This change adds neither a readiness endpoint nor an up-but-unready
state. Close the attestation connection on success and failure; dispose owned resources when
startup aborts. Importing the app for OpenAPI generation must remain connection-free.

## Online and offline migration preflight

Online migration keeps its result-reading preflight and strengthens it with exact membership and
catalog-kind checks.

Offline `upgrade` generation cannot read catalogs. Migration 0007 therefore emits PostgreSQL
`DO` blocks that perform equivalent role, membership, ownership, and catalog checks when the SQL
artifact is executed. Generation succeeds without opening a connection. Safely render the
configured role identifiers and literals without embedding credentials.

Every 0007 check precedes the first 0007 ACL mutation and its Alembic version update. This ordering
does not apply to grants in immutable earlier migrations. Test equivalent acceptance and rejection
for online and offline execution; text generation alone is insufficient evidence.

### Execution paths

For a database already at 0006, generate `0006_navigator_closed_loop:head --sql` and execute it
as the migration owner. The artifact contains a single transaction; failure leaves version 0006
and all captured ACLs unchanged. Successful execution reaches 0007 with the exact expected
ownership and privilege matrix.

Fresh offline replay uses an ordered bundle produced by the existing replay tooling with an
explicit SQL-output mode. It validates the credential triple without connecting and emits:

1. **Owner stage:** `base:0004_safety_approval_lifecycle`, in one transaction.
2. **Bootstrap stage:** `0004_safety_approval_lifecycle:0005_workflow_knowledge_audit`, followed
   by the existing exact allowlist of 0005 ownership transfers, in the same transaction. Transfer
   must complete before this stage commits its version and newly created objects.
3. **Owner stage:** `0005_workflow_knowledge_audit:head`, in one transaction.

The bundle includes a manifest identifying order, expected starting/ending revisions, target
database, and role names; it contains no passwords or URLs. Before mutation, each stage verifies
its connected identity, target database, and expected starting revision. Base means an empty
application schema. Ownership transfer uses the established table/function/type allowlist and
never broad `REASSIGN OWNED`. Generation writes only artifacts; execution uses separately
supplied credentials and stops on the first error.

Use three connections, one per stage, with the indicated credential. A failed stage rolls back its
own work; earlier successful stages remain committed. Resume only after inspecting the recorded
revision and satisfying the next stage's preconditions. Do not claim whole-bundle atomicity or
automatically rerun completed stages.

Raw `alembic upgrade head --sql` must also generate successfully without a connection, but is an
inspection artifact, not the supported executable fresh-replay path. It lacks the bootstrap/owner
transition and ownership transfer. Document this distinction next to the commands. The executable
bundle, rather than raw fresh SQL, is tested from an empty disposable database through head.

Offline downgrade continues to refuse before emitting ACL or version mutations.

## Catalog closure

The application-relation scan uses exactly `relkind IN ('r', 'p', 'v', 'm', 'S', 'f')`: ordinary
tables, partitioned tables, views, materialized views, sequences, and foreign tables. The approved
set remains the existing tables and views only. Compare object names and kinds as well as owners;
an expected name with an unexpected kind must not pass. Any non-extension sequence or foreign
table in public is unexpected drift, regardless of owner or current grants, so migration 0007
refuses before revoking or granting anything. Existing broader ownership checks for indexes and
types remain separate and must not be narrowed to this six-kind scan.

Extension membership is determined through catalog dependencies, not inferred from an object's
name or use of a foreign-data wrapper. Installing `postgres_fdw` and creating a foreign table
using it are separate steps. Extension-owned supporting objects remain outside the application
object allowlist; they must not provide unreviewed effective API access in the scoped schema.

The required `btree_gist` extension is pinned at version 1.7. PostgreSQL installations may leave
its bootstrap-owned functions with the extension's default PUBLIC EXECUTE privilege. Runtime
attestation accepts only that exact versioned PUBLIC EXECUTE compatibility surface; direct API or
group grants, grant options, other extension versions, and every other effective extension
relation, column, sequence, or function privilege are rejected. Migration 0007 revokes PUBLIC,
group, and direct API extension-object privileges when the migration role owns those objects, but
does not claim it can rewrite bootstrap-owned extension ACLs.

Require executing tests for an API-granted sequence and an API-granted foreign table in the
supported `postgres:16-alpine` environment used by Compose and CI. Verify `postgres_fdw`
availability as a test-environment prerequisite. Install it only in a recorded disposable database
through bootstrap, then create a test server and foreign table; no remote data query is needed.
Missing support fails environment setup, not a skipped security test. Each refusal names the
object and kind and directs the operator to review drift before retrying; it never deletes objects.

A supplementary unit test asserts the scan-kind set, but must verify that the query builder uses
that set. Constant equality alone does not establish catalog coverage. Integration tests remain
the proof that real unexpected objects are rejected.

## Shared contract and boundaries

A versioned module, `app/db/privilege_contracts/v0007.py`, contains the frozen role, relation,
view, function, and scoped catalog metadata for this revision. Migration 0007 imports that exact
version, never an alias to the latest runtime contract. The module contains no credentials, opens
no connections, and does not read current application settings or ORM metadata.

Runtime attestation explicitly selects v0007 for this milestone and rejects an incompatible schema
revision. Future privilege changes add a new contract version and migration; they do not edit
v0007. Pin the frozen contract with an independently maintained test expectation once implemented,
alongside the existing immutable-migration checks. Migration rendering stays in migration tooling;
runtime lifecycle integration stays in the API database/session layer.

Move common local/CI role provisioning and validation into one callable script, invoked by both CI
and the README instructions. Reuse the existing quoted-identifier and credential-handling
patterns. Keep unique database creation and CI environment export in their current orchestration
layer. Do not add a generator solely to make README SQL and CI SQL byte-identical.

Security tests maintain independent expected grants and role profiles. They must not derive all
expected results from the same contract that drives the implementation. Test actual provisioning
behavior and that local/CI entry points invoke the shared script with the intended configuration.

## Error handling and safety

- Every rejection names the violated boundary without printing URLs or passwords.
- Migration refusal occurs before 0007 ACL or version changes; offline execution rolls back the
  failing transaction. Fresh replay has the explicit stage boundaries above.
- Runtime refusal aborts startup before requests are served.
- Tests use only freshly named, recorded disposable databases and ordinary cleanup.
- No force-drop, connection termination, persistent-database mutation, deployment, or merge is in
  scope.

## Verification

Development follows red-green cycles for these observable assertions:

1. Given an extra direct API role grant, and separately a transitive path through `ojcc_app`,
   online and offline 0007 execution reject the membership boundary; version remains 0006 and
   the independently captured ACL digest is unchanged. Cover INHERIT, SET, and ADMIN options,
   including unexpected membership with all three disabled under the exact-graph policy.
2. Given an outgoing membership from `ojcc_app`, and separately from the migration owner,
   execution gives the same refusal and preservation guarantees. Provisioning also rejects these
   profiles without silently repairing existing roles or memberships.
3. Given valid runtime credentials, the actual Uvicorn process starts and serves the unchanged
   health response. Given owner credentials, bootstrap credentials, an extra membership, or an
   extra direct table/column/function privilege, startup exits nonzero. Assert the correct boundary
   message and absence of secret sentinel values. App import and OpenAPI export open no
   database connection.
4. Given offline generation for `0006:head` and raw fresh `head`, generation exits zero with no
   database connection; every 0007 check precedes the first 0007 mutation. Execute the 0006
   artifact against valid and drifted databases and assert success or full transaction rollback.
   Generate and execute the fresh three-stage bundle; assert identities, revisions, ownership
   transfer, final ACLs, and preservation of earlier stages if a later stage is refused.
5. Given a public API-granted sequence, and separately a foreign table, 0007 names the unexpected
   kind and operator remedy, preserves version 0006, and preserves ACLs including the unexpected
   object's ACL. Both tests execute without conditional skips. Verify name/kind mismatches and
   the scan builder's six-kind set as well.
6. Given the shared provisioning script, valid missing roles are created with the approved
   properties, valid existing roles remain valid, and drifted existing roles are rejected. CI and
   README use that script. Independent security expectations detect an erroneous contract edit;
   the v0007 freeze check prevents future runtime changes from changing historical replay.
7. Independent read-only review findings are closed with RED/GREEN evidence for extension-object
   privilege coverage, SQLAlchemy traceback-context sanitization, standalone offline target-name
   validation, and exact-one-row replay revision validation. A second read-only review must find
   no remaining actionable issue before push.

Role grants are cluster-wide even when their test databases are disposable. Use an isolated test
PostgreSQL service, isolated fixture role names, and a serialized fixture where the fixed
`ojcc_app` identity must be exercised. Record
temporary roles and grants, restore only fixture-owned mutations in `finally`, and never alter an
unrelated existing role to make a test pass. Database ACL digests do not replace explicit assertions
on cluster membership state.

After focused tests pass, rerun immutable migration hashes, the full privilege/non-owner security
gate, the complete repository verifier, and an independent read-only code review. Only then push
the branch and create the pull request against `master`.

## Non-goals

- Changing the approved application write matrix.
- Creating or repairing production roles from Alembic.
- Adding patient-facing or navigator-facing behavior.
- Altering immutable migrations 0001–0006.
- Merging, deploying, or deleting the worktree.
