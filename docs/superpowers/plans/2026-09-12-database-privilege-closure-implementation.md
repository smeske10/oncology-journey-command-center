# Database Privilege Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents unless the user separately authorizes delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining database membership and catalog gaps, add fail-closed API startup attestation, and make the approved offline migration paths executable and verifiable.

**Architecture:** Freeze revision 0007's privilege matrix in an import-only contract module shared by migration and runtime validation. Keep online catalog reads and offline emitted PostgreSQL assertions separate, add a three-stage replay artifact around the immutable 0005 bootstrap bridge, and use one role-provisioning command from local and CI entry points.

**Tech Stack:** Python 3.12, FastAPI lifespan, SQLAlchemy 2, Alembic, psycopg 3, PostgreSQL 16, pytest, PowerShell, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-12-database-privilege-closure-design.md`

## Global Constraints

- Preserve migrations 0001–0006 byte-for-byte and retain their recorded SHA-256 baseline.
- Never mutate persistent `ojcc`; integration tests use UUID-suffixed disposable databases only.
- The API process receives only `DATABASE_URL`; it must not require owner or bootstrap credentials.
- Runtime attestation is a startup snapshot. `GET /health` stays a liveness endpoint returning `{"status":"ok"}`.
- Migration and provisioning refuse drift before changing privileges, memberships, or Alembic version state.
- Fresh offline replay is atomic per owner/bootstrap/owner stage, not across the complete bundle.
- Security assertions use independent literal expectations rather than deriving expected results from the production contract.
- No dependency upgrades, patient-facing behavior, deployment, merge, or worktree cleanup.

---

### Task 1: Freeze the v0007 Contract and Close Online Catalog Validation

**Files:**
- Create: `services/api/app/db/privilege_contracts/__init__.py`
- Create: `services/api/app/db/privilege_contracts/v0007.py`
- Modify: `services/api/alembic/versions/0007_database_least_privilege.py`
- Test: `services/api/tests/test_privilege_contract_v0007.py`
- Test: `services/api/tests/integration/test_database_privileges.py`

**Interfaces:**
- Produces immutable tuples for `APPLICATION_GROUP`, relation grants, function security modes, `APPLICATION_RELKINDS`, and revision identifiers.
- Produces query/result validation that rejects any extra outgoing membership and unexpected `(name, relkind)` catalog row before mutation.

- [ ] Write a unit test with literal expected object sets that fails until `v0007` exists and the catalog query consumes all six kinds `r,p,v,m,S,f`.
- [ ] Run the unit test and confirm the missing contract/query behavior is the failure.
- [ ] Add the dependency-free frozen contract and import it by exact version from migration 0007.
- [ ] Write integration tests that grant an extra direct role to the API, add transitive outgoing membership through `ojcc_app`, and create unexpected sequence/foreign-table objects; capture revision, ACL, and membership state before upgrade.
- [ ] Run each integration test at revision 0006 and confirm 0007 currently accepts or misclassifies the drift.
- [ ] Implement exact outgoing membership and `(name, kind, owner)` validation, including extension-dependency exclusion and operator-facing object/kind errors.
- [ ] Run the focused unit and integration tests and confirm every refusal preserves revision 0006 and captured state.
- [ ] Run formatting, immutable-hash, and diff checks; record RED/GREEN evidence in the progress ledger and commit this task.

### Task 2: Add Executing Offline Preflight to Migration 0007

**Files:**
- Modify: `services/api/alembic/versions/0007_database_least_privilege.py`
- Test: `services/api/tests/test_database_targets.py`
- Test: `services/api/tests/integration/test_database_privileges.py`

**Interfaces:**
- Produces offline PostgreSQL `DO` assertions using configured role literals without opening a connection.
- Preserves the online validator from Task 1 and emits every assertion before the first 0007 ACL statement.

- [ ] Add a subprocess test proving raw `alembic upgrade head --sql` completes with deliberately unreachable URLs and contains preflight before privilege mutation.
- [ ] Run it and confirm generation fails at the current mock connection query.
- [ ] Add offline rendering for identity, role profile, exact memberships, ownership, relation kinds, and function sets; quote all configured values and include no credentials.
- [ ] Run generation tests and inspect the artifact ordering and secret-sentinel absence.
- [ ] Add execution tests for `0006:head --sql` against valid and drifted disposable databases, including the sequence and real `postgres_fdw` foreign-table cases.
- [ ] Run each test and confirm valid execution reaches 0007 while refusal rolls back the complete transaction and leaves the ACL snapshot unchanged.
- [ ] Retain fail-closed offline downgrade behavior and verify it emits no ACL/version mutations.
- [ ] Run formatting, immutable-hash, and diff checks; update the ledger and commit this task.

### Task 3: Add Runtime Startup Attestation

**Files:**
- Create: `services/api/app/db/privilege_attestation.py`
- Modify: `services/api/app/db/session.py`
- Modify: `services/api/app/main.py`
- Create: `services/api/tests/integration/test_runtime_privilege_attestation.py`
- Modify: `services/api/tests/test_health.py`

**Interfaces:**
- Produces `attest_runtime_database(connection, target)` and a sanitized `RuntimePrivilegeBoundaryError` with stable boundary identifiers.
- Produces an application lifespan that acquires one engine connection, attests revision 0007, closes it, and aborts startup on mismatch.

- [ ] Write unit/integration tests for valid API credentials and each misconfiguration class: owner/bootstrap login, extra outgoing membership, direct relation privilege, column privilege, and direct function privilege.
- [ ] Run them and confirm startup currently serves or lacks the attestation interface.
- [ ] Implement effective database/schema/relation/function checks using the frozen v0007 contract, including direct, inherited, PUBLIC, column, and grant-option exposure.
- [ ] Integrate attestation into FastAPI lifespan while keeping module import and OpenAPI export connection-free.
- [ ] Launch the real Uvicorn subprocess in tests: assert valid startup serves the unchanged health response; invalid startup exits nonzero with its stable boundary identifier and without URL/password/driver details.
- [ ] Run focused runtime and health tests, formatting, types, immutable hashes, and diff checks; update the ledger and commit this task.

### Task 4: Produce and Execute the Three-Stage Offline Replay Bundle

**Files:**
- Modify: `services/api/scripts/replay_schema.py`
- Create: `services/api/tests/test_replay_schema_offline.py`
- Modify: `services/api/tests/integration/test_database_privileges.py`
- Modify: `README.md`

**Interfaces:**
- Adds `--sql-output-directory PATH` to generate `manifest.json`, `01-owner.sql`, `02-bootstrap.sql`, and `03-owner.sql` without connecting.
- Each SQL stage verifies identity, database, and starting revision before mutation; stage 2 includes exact 0005 ownership transfers before commit.

- [ ] Write CLI tests using unreachable credential URLs and a temporary output directory; assert the exact manifest, file order, stage identities/revisions, and absence of passwords/URLs.
- [ ] Run and confirm the new mode is rejected.
- [ ] Implement generation by invoking bounded Alembic ranges, wrapping each stage in one transaction, and rendering the exact allowlisted transfer SQL into stage 2.
- [ ] Add an integration executor test that uses three separate connections from an empty disposable database through head and verifies revisions, owners, and final ACLs.
- [ ] Add a later-stage refusal case and assert earlier committed stages remain while the failing stage is rolled back.
- [ ] Document raw fresh Alembic SQL as inspection-only and the bundle as the supported executable fresh path.
- [ ] Run focused replay tests, formatting, immutable hashes, and diff checks; update the ledger and commit this task.

### Task 5: Consolidate Fail-Closed Role Provisioning

**Files:**
- Create: `services/api/scripts/provision_database_roles.py`
- Create: `services/api/tests/test_provision_database_roles.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `services/api/tests/test_verify_harness.py`

**Interfaces:**
- Adds a bootstrap-only CLI accepting explicit migration/API/group names and login passwords, creating missing roles and the one exact membership while rejecting existing drift.
- Local instructions and CI invoke the same command; database creation and environment export remain in their existing orchestration.

- [ ] Write behavior tests against an isolated PostgreSQL fixture for missing roles, already-valid roles, role-attribute drift, membership-option drift, and extra outgoing memberships.
- [ ] Run and confirm the command is missing.
- [ ] Implement identifier-safe creation and exact-graph validation with no repair of pre-existing drift.
- [ ] Replace duplicated README/CI role SQL with invocations of the command while retaining unique database creation and CI variable export.
- [ ] Update harness tests to execute/inspect entry-point behavior rather than compare duplicated source snippets.
- [ ] Run focused provisioning and harness tests, formatting, immutable hashes, and diff checks; update the ledger and commit this task.

### Task 6: Complete the Corrective Security Gate

**Files:**
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`
- Modify: `docs/superpowers/specs/2026-09-12-database-privilege-closure-design.md`

**Interfaces:**
- Produces the final evidence record and marks the design implemented only after fresh complete verification.

- [ ] Run all focused membership, catalog, offline execution, runtime startup, replay, provisioning, and independent-contract tests.
- [ ] Recompute and compare SHA-256 hashes for migrations 0001–0006.
- [ ] Run the complete privilege/non-owner security gate and full `scripts/verify.ps1` repository verifier.
- [ ] Run Ruff, Pyright, web lint/tests/build, `git diff --check`, and a credential/sentinel scan.
- [ ] Review the final diff line-by-line against every design verification item; record exact test counts, disposable database cleanup, and any intentional limits.
- [ ] Mark the design implemented and commit the final documentation/evidence update.
- [ ] Use `superpowers:finishing-a-development-branch` to present the integration choices; do not merge, deploy, or remove the worktree without the user's selection.
