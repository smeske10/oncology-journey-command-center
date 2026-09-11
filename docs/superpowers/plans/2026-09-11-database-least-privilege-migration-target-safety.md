# Navigator Closed-Loop Post-Merge Security and Operational Hardening — Milestone 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents unless the user separately authorizes delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make schema changes run through an explicit migration/object-owner credential while the API and complete closed-loop journey run through a distinct non-owner credential with a catalog-complete, deny-by-default PostgreSQL privilege surface and fail-closed target matching.

**Architecture:** Keep immutable migrations 0001–0006 unchanged and add a privilege-only 0007. A shared URL-target module compares `MIGRATION_DATABASE_URL` and `DATABASE_URL` by PostgreSQL backend, normalized host, explicit/effective port, and database name while requiring distinct usernames; Alembic and every destructive/test wrapper call it before connecting. PostgreSQL retains `ojcc_app` as a NOLOGIN privilege group, an externally provisioned API login inherits that group without role-administration or `SET ROLE`, and a distinct non-superuser migration login owns the database, schema, tables, views, and functions.

**Tech Stack:** Existing Python 3.12, FastAPI, SQLAlchemy 2, Alembic, psycopg 3, PostgreSQL 16, pytest, PowerShell verification scripts, GitHub Actions, Next.js 16/React 19/Playwright. No dependency upgrades or new services.

**Spec:** `docs/product-design.md` §§7.3, 8.2, 8.5, 8.10–8.12, 9.3, 11.1, 11.5, and 14; the delivered closed-loop contract in `docs/superpowers/plans/2026-09-08-navigator-closed-loop-implementation.md`; and the fixed milestone constraints in this plan.

## Global Constraints

- The approved base is merge commit `47aa43c5cc7a307140e5317240f20d58dbb8b6f5` (PR #6) on `feature/database-least-privilege` in `.worktrees/database-least-privilege`.
- Never edit migrations 0001–0006. Their accepted SHA-256 values are:
  - `0001_core_domain.py`: `a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391`
  - `0002_identity_pathway_submission.py`: `6fdf3c15fdf51cb9c3729f7f6d0458b51c88eeee8119a10a1084a425f78f5648`
  - `0003_need_task_outcome_lifecycle.py`: `25641a9831bf6cce4198cc60636d60a03058ecc179752d32328c7513bcb6b556`
  - `0004_safety_approval_lifecycle.py`: `301eae2be84c8685b14b0335525fa011cc88015f654cef95212327cf4cee6704`
  - `0005_workflow_knowledge_audit.py`: `c81f81976dd82fde31eb70b1a271205938b9dc1ecae34e84e5780d09c3fdd5cc`
  - `0006_navigator_closed_loop.py`: `9b325c30e7bf0ab82925adbcfc2462546866ed07355e8fef740ac1832ee5f91b`
- Preserve `.gitattributes` LF enforcement for migration Python files.
- Do not copy, commit, amend, clean, reset, delete, or continue work in `.worktrees/navigator-closed-loop`. Its rejected 0007 and tests are review evidence only.
- Do not add the rejected task-binding CHECK, its upgrade pre-scan/refusal, or an integrity violation for legacy unbound execution history.
- Historical unbound tasks remain valid and readable. Open unbound tasks may be claimed only through the approved command, which binds the exact approved proposal atomically. Assigned/in-progress unbound tasks remain non-executable; start/complete still require a binding. Do not fabricate, backfill, cancel, delete, or rewrite historical authorization rows.
- Migration files create no passwords or login credentials. Local/CI roles are provisioned explicitly before Alembic; production deployment and provider-specific provisioning are out of scope.
- A migration must not alter pre-existing role attributes to make them acceptable. It validates and refuses on unexpected capabilities or ownership.
- Never reset, drop, seed, migrate, or otherwise mutate persistent `ojcc`. Use only new UUID-suffixed disposable databases, record every created name in the progress ledger, never reuse an uncertain database, and never force-terminate unknown connections.
- Keep the existing npm, pip, and Next.js GitHub Actions caching unless a test proves a caching defect. No such defect is currently known.
- No workflow behavior, agentic orchestration, policy engine, retrieval, new scenario, authentication-cardinality change, deterministic-ID/FIPS change, deployment, merge, or deferred milestone is included.
- After each task: review the focused diff, run `git diff --check`, re-hash migrations 0001–0006, update the progress ledger with RED/GREEN evidence and the next exact step, and commit only the task's explicit files after review.

---

## 1. Evidence-backed fixed design

### 1.1 Credential and process contract

| Process | Credential | May receive the other credential? | Contract |
|---|---|---|---|
| FastAPI runtime | `DATABASE_URL` API login | No | Non-owner, inherited `ojcc_app` privileges only |
| Alembic online/offline | `MIGRATION_DATABASE_URL` owner plus `DATABASE_URL` only for target comparison | Yes, in the migration process | Refuse before migration when the pair differs in host, port, or database, or usernames are equal |
| Reset | Both explicit arguments | Yes | Validate pair and disposable confirmation before any engine; schema reset/migrate/seed as owner; post-seed runtime smoke as API login |
| Seed CLI | Both explicit arguments | Yes | Validate pair first; seed as owner because it uses trigger bypass; never fall back to process settings |
| Integrity CLI | Explicit URL chosen by caller | No | Remains read-only; final runtime evidence runs it as API login |
| FastAPI live web server | `DATABASE_URL` only | No | Its environment is explicitly stripped of migration/admin URLs |
| Next live web server | Neither database URL | No | Receives only the web variables it needs |
| Disposable-database helper | Both base URLs | Yes | Creates/migrates through owner; yields both per-database URLs; overwrites inherited values in every Alembic child |

`DATABASE_URL` has no persistent-`ojcc` default. API startup requires it. `MIGRATION_DATABASE_URL` may be absent in a pure API process; any migration, reset, seed, CI provisioning, or migration-test entry point requires it explicitly.

The pair validator uses SQLAlchemy `make_url` and compares:

- PostgreSQL backend (`postgresql`), independent of the `+psycopg` driver spelling;
- normalized lowercase host text (no DNS lookup or alias guessing);
- effective port, with omitted PostgreSQL port normalized to 5432;
- exact non-empty database name;
- distinct, non-empty usernames.

Passwords may differ and are never logged. URL query parameters are excluded from target identity so managed-service options such as SSL do not create false mismatches; destructive disposable workflows retain their stricter existing rule that rejects all query parameters and non-loopback hosts. `localhost`, `127.0.0.1`, and `::1` remain individually allowed for disposable targets, but a pair must use the same normalized host spelling.

### 1.2 Role profile

| Property | Migration/object owner | API login | `ojcc_app` group |
|---|---:|---:|---:|
| LOGIN | true | true | false |
| Own target database/public schema/all application objects | true | false | false |
| SUPERUSER | false | false | false |
| REPLICATION | false | false | false |
| BYPASSRLS | false | false | false |
| CREATEDB | local/CI true for new disposable DBs; environment-specific outside this milestone | false | false |
| CREATEROLE | local/CI true because immutable migration 0005 creates/alters `ojcc_app`; use a controlled bootstrap arrangement outside local/CI | false | false |
| Inherits `ojcc_app` | false | true | n/a |
| May `SET ROLE ojcc_app` | false | false | n/a |
| ADMIN OPTION on `ojcc_app` | false | false | n/a |
| Member/SET/ADMIN path to owner role | n/a | none | none |

Local/CI provisioning may create missing roles with explicit synthetic-only credentials and the exact profile above. If a same-named role already exists, provisioning queries and validates it; it does not silently add/remove SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS, LOGIN, or membership capabilities. Configured role and database identifiers are rendered with `psycopg.sql.Identifier`; catalog lookups use bound parameters. No f-string, shell interpolation, or raw environment value becomes a SQL identifier.

### 1.3 Complete application relation matrix

Every application relation in `public` is named below. `ojcc_app` receives no DELETE, TRUNCATE, REFERENCES, or TRIGGER privilege on any table/view and no grant option.

| Runtime privilege | Complete relation set |
|---|---|
| SELECT only | `agent_run`, `agent_run_citation`, `approval_policy`, `audit_event`, `care_episode`, `check_in_definition`, `episode_pathway_assignment`, `follow_up_request`, `knowledge_document`, `manual_review_task`, `navigation_task_resource`, `organization`, `organization_knowledge_approval`, `pathway_definition`, `patient_identity_link`, `patient_message`, `proposed_value_schema`, `reported_need`, `resource`, `role_assignment`, `signal_rule`, `synthetic_patient`, `user_account`, `workflow_run`, `workflow_transition_event` |
| SELECT + INSERT | `approval_decision`, `check_in_submission`, `follow_up_response`, `outcome`, `proposed_change`, `safety_signal_resolution` |
| SELECT + UPDATE | `navigation_task`, `safety_signal` |
| No application privilege | `alembic_version` (migration metadata, not an application relation) |

`reported_need` is intentionally SELECT-only in this milestone: no merged API route creates/reopens a need, and Week 3 automatic intake is deferred. Trigger-authored changes to `reported_need`, `navigation_task_resource`, `follow_up_request`, `audit_event`, and `workflow_transition_event` run through the fixed SECURITY DEFINER boundary; the API login cannot issue those writes directly.

The four views `active_check_in_submission`, `effective_need_state`, `effective_proposed_change_state`, and `effective_safety_signal_state` are SELECT-only. `public` schema is USAGE-only for `ojcc_app`; CREATE is false. The target database is CONNECT-only for `ojcc_app`; CREATE and TEMPORARY are false. PUBLIC receives no database CONNECT/TEMPORARY/CREATE, no public-schema USAGE/CREATE, no application relation privilege, and no application-function EXECUTE.

### 1.4 Complete function contract

The catalog test enumerates every application-defined `public` function, including its identity arguments, owner, security mode, `proconfig`, PUBLIC execution, and `ojcc_app` execution. The expected function-name set is:

`append_workflow_transition_event`, `apply_final_approval_decision`, `apply_navigation_resource_approval`, `close_reported_need_from_outcome`, `guard_agent_run_citation`, `guard_agent_run_citation_immutable`, `guard_agent_run_created_at`, `guard_approval_decision`, `guard_bound_navigation_task_delete`, `guard_follow_up_request_insert`, `guard_follow_up_response_insert`, `guard_knowledge_approval_history`, `guard_knowledge_document_immutable`, `guard_manual_review_task`, `guard_navigation_task_lifecycle`, `guard_navigation_task_resource`, `guard_navigation_task_resource_proposal`, `guard_patient_identity_link_response_history`, `guard_proposed_change_revision`, `guard_reported_need_identity_update`, `guard_reported_need_reopening`, `guard_role_assignment_approval_history`, `guard_role_assignment_knowledge_history`, `guard_safety_signal_lifecycle`, `guard_safety_signal_resolution`, `guard_workflow_run_lineage`, `record_navigation_task_transition`, `reject_append_only_mutation`, `reject_approval_policy_mutation`, `reject_proposed_value_schema_mutation`, `reject_signal_rule_mutation`, and `safety_severity_rank`.

These fourteen existing functions remain SECURITY DEFINER because they cross protected write boundaries or are already part of that trusted trigger chain:

`append_workflow_transition_event`, `guard_approval_decision`, `apply_final_approval_decision`, `apply_navigation_resource_approval`, `guard_proposed_change_revision`, `guard_navigation_task_resource_proposal`, `guard_safety_signal_resolution`, `close_reported_need_from_outcome`, `guard_navigation_task_lifecycle`, `guard_bound_navigation_task_delete`, `guard_follow_up_request_insert`, `guard_follow_up_response_insert`, `guard_patient_identity_link_response_history`, and `record_navigation_task_transition`.

Each is owned by the migration/object-owner role and has exactly `search_path=pg_catalog, public, pg_temp`, keeping the temporary schema last. PUBLIC and `ojcc_app` have no direct EXECUTE on trigger functions; the integration journey proves PostgreSQL trigger dispatch still performs the authorized work. All other functions remain SECURITY INVOKER. Only `safety_severity_rank(safety_severity)` is directly executable by `ojcc_app`, because merged repository and integrity queries invoke it. Migration 0007 revokes default PUBLIC function execution and sets owner default privileges so later functions are not PUBLIC-executable by default.

### 1.5 Migration 0007 version and downgrade contract

- New revision: `0007_database_least_privilege`.
- Down revision: `0006_navigator_closed_loop`.
- Upgrade changes only role/database/schema/relation/function privileges and function security metadata. It creates no tables, constraints, role/login, password, authorization record, or workflow row.
- Empty 0001→0007 and populated 0006→0007 upgrades must both succeed when the URL pair and pre-provisioned role profile are valid.
- Populated upgrade fixtures include open, assigned, in-progress, completed, and cancelled unbound historical tasks. Their byte-for-byte row snapshots and the zero-violation integrity result remain unchanged.
- URL/role/ownership preflight failure aborts before revision execution and leaves `alembic current` at 0006 with the pre-upgrade ACL snapshot unchanged.
- 0007→0006 downgrade is refused unconditionally before emitting or executing ACL/function changes. The error states that reverting would restore owner-runtime and trigger-table write exposure and directs operators to preserve the database or restore a reviewed pre-0007 snapshot. Online and `--sql` refusal tests assert no `REVOKE`, `GRANT`, `ALTER FUNCTION`, or `UPDATE alembic_version` is emitted before the error.
- At success, `alembic current` reports `0007_database_least_privilege (head)` and `alembic check` reports no metadata drift.

---

## Task 1: Freeze the immutable baseline and add fail-closed URL-pair validation

**Files:**

- Create: `services/api/app/db/targets.py`
- Create: `services/api/tests/test_database_targets.py`
- Modify: `services/api/app/config.py`
- Modify: `services/api/alembic/env.py`
- Modify: `services/api/tests/test_core_domain_migration.py`
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`

**Interfaces:**

- Produces `DatabaseTarget(backend: str, host: str, port: int, database: str, username: str)`.
- Produces `parse_database_target(url_text: str, *, label: str) -> DatabaseTarget`.
- Produces `validate_database_target_pair(*, application_url: str, migration_url: str) -> tuple[DatabaseTarget, DatabaseTarget]`.
- Produces `Settings.require_migration_database_url() -> str`; normal API code continues to consume only `settings.database_url`.

- [ ] **Step 1: Add the six-file immutable migration snapshot guard**

Replace the single-file assertion in `test_core_domain_migration.py` with one mapping and a parameterized test that reads bytes:

```python
IMMUTABLE_MIGRATION_SHA256 = {
    "0001_core_domain.py": "a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391",
    "0002_identity_pathway_submission.py": "6fdf3c15fdf51cb9c3729f7f6d0458b51c88eeee8119a10a1084a425f78f5648",
    "0003_need_task_outcome_lifecycle.py": "25641a9831bf6cce4198cc60636d60a03058ecc179752d32328c7513bcb6b556",
    "0004_safety_approval_lifecycle.py": "301eae2be84c8685b14b0335525fa011cc88015f654cef95212327cf4cee6704",
    "0005_workflow_knowledge_audit.py": "c81f81976dd82fde31eb70b1a271205938b9dc1ecae34e84e5780d09c3fdd5cc",
    "0006_navigator_closed_loop.py": "9b325c30e7bf0ab82925adbcfc2462546866ed07355e8fef740ac1832ee5f91b",
}

@pytest.mark.parametrize(("filename", "expected"), IMMUTABLE_MIGRATION_SHA256.items())
def test_accepted_migration_bytes_are_immutable(filename: str, expected: str) -> None:
    path = MIGRATION_PATH.parent / filename
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
```

Run: `python -m pytest services/api/tests/test_core_domain_migration.py -k immutable -q`

Expected: PASS for all six baseline cases. This is a guardrail snapshot, not RED evidence.

- [ ] **Step 2: Write failing URL parser/pair tests**

Cover missing/blank/malformed URL, non-PostgreSQL backend, blank host/database/username, normalized default port, case-normalized host, different usernames/passwords allowed, same username rejected, and mismatch of host spelling, port, or database rejected. Include the inherited-value regression:

```python
def test_pair_rejects_inherited_migration_url_for_another_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", APP_URL_FOR_DATABASE_A)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", OWNER_URL_FOR_DATABASE_B)
    with pytest.raises(DatabaseTargetMismatch, match="database name"):
        validate_database_target_pair(
            application_url=os.environ["DATABASE_URL"],
            migration_url=os.environ["MIGRATION_DATABASE_URL"],
        )
```

Run: `python -m pytest services/api/tests/test_database_targets.py -q`

Expected RED: import fails because `app.db.targets` does not exist.

- [ ] **Step 3: Implement the pure target contract**

Use `sqlalchemy.engine.make_url`; never connect or resolve DNS. Raise typed `DatabaseTargetError` subclasses whose messages name the mismatched field but redact usernames and credentials. Compare the tuple `(backend, host.casefold(), port or 5432, database)` and separately reject equal usernames.

Run the focused test again.

Expected GREEN: all target parser/pair tests pass.

- [ ] **Step 4: Make settings fail closed without exposing owner credentials to the API**

Remove the persistent `ojcc` default for `DATABASE_URL`; `_required_environment("DATABASE_URL")` raises a concise configuration error. Add nullable `migration_database_url` and a method that raises only when an owner workflow requests it. Do not make FastAPI runtime require `MIGRATION_DATABASE_URL`.

Add tests that instantiate `Settings()` under isolated environments and prove:

- API settings fail when `DATABASE_URL` is missing;
- API settings succeed with only `DATABASE_URL`;
- `require_migration_database_url()` fails when missing and returns the explicit value when set;
- a set `MIGRATION_DATABASE_URL` never replaces `database_url`.

- [ ] **Step 5: Switch Alembic to the validated owner target**

In `alembic/env.py`, call `validate_database_target_pair` before `config.set_main_option`, set `sqlalchemy.url` only to `settings.require_migration_database_url()`, and preserve offline/online behavior. For online mode, after connecting, verify `current_user` equals the decoded migration URL username and that `current_database()` equals the validated database.

Add subprocess tests for missing migration URL, mismatched inherited migration URL, same username, and a matching pair. For refusal cases, point to syntactically valid disposable names but patch the engine entry point so the test proves failure occurs before connection.

Run: `python -m pytest services/api/tests/test_database_targets.py services/api/tests/test_core_domain_migration.py -q`

Expected GREEN: target tests and historical migration tests pass with child environments that explicitly set both URLs.

- [ ] **Step 6: Review, record, and commit**

Run the immutable hash test, `python -m ruff check services/api/app/db/targets.py services/api/app/config.py services/api/alembic/env.py services/api/tests/test_database_targets.py services/api/tests/test_core_domain_migration.py`, and `git diff --check`. Record RED/GREEN commands and hashes in the ledger.

Commit: `feat: separate migration and runtime database targets`

---

## Task 2: Add the privilege-only 0007 and catalog-complete security tests

**Files:**

- Create: `services/api/alembic/versions/0007_database_least_privilege.py`
- Create: `services/api/tests/integration/test_database_privileges.py`
- Modify: `services/api/tests/closed_loop/test_migration.py`
- Modify: `services/api/tests/integration/test_audit_immutability.py`
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`

**Interfaces:**

- Consumes the validated URL pair from Task 1.
- Produces Alembic revision `0007_database_least_privilege`.
- Produces catalog constants for the exact 33-table, four-view, and function matrices in §1.

- [ ] **Step 1: Write failing role/ownership tests at 0006**

Provision the owner/API/group roles explicitly in the disposable fixture, migrate only to 0006, and assert the known gaps: owner URL and API URL identify the same target but different usernames; API login owns no objects; the current 0006 ACL is incomplete; API INSERT on `audit_event`/`workflow_transition_event` is currently inherited; function ownership/security metadata is captured for later comparison.

The test must query `pg_roles`, `pg_auth_members`, `pg_database`, `pg_namespace`, `pg_class`, and `pg_proc`. It asserts all LOGIN/SUPERUSER/CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS properties, membership `USAGE`, `SET`, and `ADMIN` options, and zero owner-role membership reachable by the API login.

Run: `python -m pytest services/api/tests/integration/test_database_privileges.py -k 'role or ownership' -q`

Expected RED: the new test module or 0007 expectations are absent.

- [ ] **Step 2: Write the complete relation/view privilege matrix test**

Generate the actual object set from `pg_class` for `public` and compare exact set equality before checking privileges. For every table/view, check all seven table-like privileges:

```python
TABLE_PRIVILEGES = (
    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"
)

actual = {
    (relation, privilege): connection.scalar(
        text("SELECT has_table_privilege(:role, :relation, :privilege)"),
        {"role": api_role, "relation": f"public.{relation}", "privilege": privilege},
    )
    for relation in EXPECTED_APPLICATION_RELATIONS
    for privilege in TABLE_PRIVILEGES
}
assert actual == EXPECTED_RELATION_PRIVILEGES
```

Also assert PUBLIC has none, no grant option exists in `information_schema.role_table_grants`, all objects are owned by the migration role, no sequences exist, view privileges are SELECT-only, and `alembic_version` is unavailable to the API role.

Expected RED at 0006: object-set or ACL equality fails.

- [ ] **Step 3: Write the database/schema/function matrix tests**

Assert API group/login database privileges are CONNECT true, CREATE/TEMP false; public schema USAGE true/CREATE false; PUBLIC database/schema privileges false. Enumerate `pg_proc` exact names/signatures and assert:

- all application functions owned by the migration role;
- the fourteen named functions are `prosecdef = true` with `proconfig = ['search_path=pg_catalog, public, pg_temp']`;
- all remaining functions are SECURITY INVOKER;
- PUBLIC has EXECUTE on none;
- `ojcc_app` executes only `safety_severity_rank(safety_severity)`;
- no role other than the owner has an unexpected function grant.

Expected RED at 0006: PUBLIC/default and `ojcc_app` execution assertions fail.

- [ ] **Step 4: Implement 0007 without role or data mutation**

At the beginning of `upgrade()`, query and validate the role/ownership profile with bound values derived from the validated URL pair. Refuse before any ACL statement when the owner is superuser/replication/bypass-RLS, the API login has any dangerous capability, the API can SET/ADMIN the group or reach the owner role, or any application object is not owned by the migration login. Query and report `rolcreatedb`/`rolcreaterole`; require the local/CI fixture profile in tests, but do not rewrite a role to meet it.

Then apply the exact matrices from §1 using schema-qualified names. Use fixed repository constants for application object names. Dynamic database identifiers use PostgreSQL `format('%I', current_database())` inside a fixed DO block; configured roles are never interpolated. Revoke PUBLIC defaults and set:

```sql
ALTER DEFAULT PRIVILEGES IN SCHEMA public
REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
```

Do not create/alter/grant membership to a login role. Do not add any task constraint, pre-scan task data, or modify the integrity auditor.

Implement `downgrade()` as the unconditional refusal specified in §1.5 before any `op.execute` call.

- [ ] **Step 5: Prove trigger-authored writes still work without app INSERT/EXECUTE**

Connect as the API login and perform an allowed task transition plus Outcome close through existing services. Assert the trigger inserts `follow_up_request`, `audit_event`, and any required transition row even though direct `has_table_privilege(..., 'INSERT')` and direct trigger-function EXECUTE are false. Direct attempts to insert the two event tables must raise `InsufficientPrivilege`.

Run: `python -m pytest services/api/tests/integration/test_database_privileges.py -q`

Expected GREEN: all catalog and trigger-boundary assertions pass.

- [ ] **Step 6: Add exact upgrade/refusal/version tests**

In `closed_loop/test_migration.py`, test:

- empty 0001→0007;
- populated 0006→0007;
- invalid owner/app role profile leaves version at 0006 and preserves a captured ACL digest;
- matching host/port/database but different usernames succeeds;
- mismatched target fails before connection;
- online and offline downgrade refuse before any partial output;
- `current` is 0007 head and `check` is clean.

In the populated fixture, insert unbound tasks in every historical status under trigger-bypass, capture all columns, upgrade, and assert exact equality and zero new integrity violation. Assert the migration contains none of `ck_navigation_task_binding_required`, `_require_bindable_task_history`, or `unbound_executing_navigation_task`.

- [ ] **Step 7: Review, record, and commit**

Run focused privilege/migration tests, Ruff, immutable hashes, and `git diff --check`. Record role profile and version evidence without credentials.

Commit: `feat: enforce the complete application database privilege surface`

---

## Task 3: Make every disposable migration helper owner/runtime aware and non-destructive to unknown sessions

**Files:**

- Create: `services/api/tests/__init__.py`
- Create: `services/api/tests/database_support.py`
- Modify: `services/api/tests/test_core_domain_migration.py`
- Modify: `services/api/tests/test_demo_seed.py`
- Modify: `services/api/tests/closed_loop/test_migration.py`
- Modify: `services/api/tests/integration/test_audit_immutability.py`
- Modify: `services/api/tests/integration/test_restore_integrity.py`
- Modify: `services/api/tests/integration/test_seeded_application_contracts.py`
- Modify: `services/api/tests/test_approvals.py`
- Modify: `services/api/tests/test_knowledge_governance.py`
- Modify: `services/api/tests/test_outcomes.py`
- Modify: `services/api/tests/test_safety_signals.py`
- Modify: `services/api/tests/test_workflow_lineage.py`
- Modify: `services/api/tests/closed_loop/conftest.py`
- Modify: `services/api/tests/integration/test_canonical_read_repositories.py`
- Modify: `services/api/tests/integration/test_core_domain.py`
- Modify: `services/api/tests/integration/test_identity_pathway_submission.py`
- Modify: `services/api/tests/integration/test_need_task_lifecycle.py`
- Modify: `services/api/tests/integration/test_safety_approval_concurrency.py`
- Modify: `services/api/tests/integration/test_tenant_isolation.py`
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`

**Interfaces:**

- Produces `DisposableDatabase(name: str, migration_url: str, application_url: str)`.
- Produces `disposable_database(*, prefix: Literal[...], migrate_to: str | None) -> ContextManager[DisposableDatabase]`.
- Produces `alembic_environment(database: DisposableDatabase) -> dict[str, str]` that strips inherited `PG*`, `DATABASE_URL`, and `MIGRATION_DATABASE_URL`, then sets both explicit per-database URLs.

- [ ] **Step 1: Write failing support-helper safety tests**

Test exact 32-lowercase-hex suffixes for each existing prefix (`ojcc_migration_test_`, `ojcc_task5_migration_`, `ojcc_task7_`), loopback/5432/query safeguards, same-target validation, distinct usernames, no reuse, and identifier quoting. Seed the parent environment with `MIGRATION_DATABASE_URL` for another database and prove `alembic_environment` overwrites it.

Test cleanup by disposing only engines/connections opened by the helper and issuing an ordinary quoted `DROP DATABASE`. Simulate a remaining connection and assert cleanup reports/leaves the exact database instead of calling `pg_terminate_backend`.

Expected RED: `tests.database_support` does not exist.

- [ ] **Step 2: Implement the shared helper**

Generate the UUID name internally and validate it again immediately before create/drop. Use `psycopg.sql.Identifier` for `CREATE DATABASE`, `DROP DATABASE`, and configured role identifiers. Print/record only `CREATED <name>`, `DROPPED <name>`, or `LEFTOVER <name>: <reason>`; never render URLs or passwords.

The helper connects to the `postgres` maintenance database through the migration credential, creates the database owned by the migration login, derives the application URL by changing only the database component, validates the pair, and refuses if the target exists. It never enumerates/drop-matches a prefix and never terminates connections.

- [ ] **Step 3: Convert all six database-creating test modules**

Replace their local create/drop/Alembic helpers with `DisposableDatabase`. All subprocess environments use `alembic_environment`; no `os.environ | {'DATABASE_URL': ...}` remains. Owner-only setup/trigger-bypass uses `database.migration_url`; API/privilege behavior uses `database.application_url`.

Run:

```powershell
rg -n "CREATE DATABASE|DROP DATABASE|pg_terminate_backend|env=os.environ.*DATABASE_URL" services/api/tests
```

Expected: no local duplicate database lifecycle implementation and no forced termination remain outside the shared helper's negative test fixtures.

- [ ] **Step 4: Separate owner setup from runtime access in remaining database tests**

For the fourteen remaining modules listed under Files, replace setup/corruption/fixture engines that write privileged seed rows or use `session_replication_role` with `settings.require_migration_database_url()`. Keep explicit read-only or runtime behavior on `settings.database_url`. Dependency-overridden domain tests may continue to use owner sessions for fixture isolation, but they must be labeled setup sessions; the dedicated Task 5 journey is the non-owner API boundary proof.

Add a repository guard test that searches test subprocess calls and fails when an Alembic child sets only one URL or inherits either URL implicitly.

- [ ] **Step 5: Run the migrated helper suites and record every database name**

Run the six database-creating modules with `-s` so CREATED/DROPPED/LEFTOVER names are visible. Append every created name and final disposition to the ledger. A leftover is a failed gate requiring investigation; do not terminate its connections or reuse it.

- [ ] **Step 6: Review and commit**

Run Ruff, the URL/helper tests, immutable hashes, and `git diff --check`.

Commit: `test: isolate owner and runtime database credentials`

---

## Task 4: Propagate both targets through reset, seed, live verification, and CI

**Files:**

- Modify: `services/api/scripts/seed_demo.py`
- Modify: `scripts/reset_demo.ps1`
- Modify: `scripts/verify.ps1`
- Modify: `scripts/verify_live_journey.ps1`
- Modify: `apps/web/playwright.live.config.ts`
- Modify: `apps/web/e2e-live/closed-loop-transportation.spec.ts`
- Modify: `.github/workflows/ci.yml`
- Modify: `services/api/tests/test_demo_seed.py`
- Modify: `services/api/tests/test_verify_harness.py`
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`

**Interfaces:**

- `reset_demo.ps1 -MigrationDatabaseUrl <owner> -DatabaseUrl <api> -ConfirmDatabaseName <name>`.
- `seed_demo.py --migration-database-url <owner> --database-url <api>`.
- `verify.ps1 -LiveMigrationDatabaseUrl <owner> -LiveDatabaseUrl <api> -LiveConfirmDatabaseName <name>`; API pair comes from the two process environment variables.
- `verify_live_journey.ps1` accepts the same three live arguments.

- [ ] **Step 1: Write failing reset/seed propagation tests**

Extend `test_demo_seed.py` and `test_verify_harness.py` to prove every entry point rejects before connection when either URL is missing, usernames are equal, targets differ, confirmation differs, target is persistent/remote/query-bearing, or a dirty inherited `MIGRATION_DATABASE_URL` names another database. The fake child executables must print only target field names/current usernames, never URLs.

Assert reset snapshots/restores both URL variables and all case-insensitive `PG*` variables in `finally`, including on a nonzero Alembic/seed/audit child exit.

Expected RED: current scripts accept one URL and do not isolate `MIGRATION_DATABASE_URL`.

- [ ] **Step 2: Make reset and seed explicit**

`reset_demo.ps1` validates both URLs and confirmation before the drop-schema engine. It drops/recreates `public`, runs Alembic, and seeds through `MIGRATION_DATABASE_URL`; seed receives both arguments but connects only with the owner URL. Finish by running read-only integrity once through the owner during seed and once through `DATABASE_URL` to prove the API role can inspect the complete schema.

Before each child, set both process variables to the validated exact pair. Restore both and all `PG*` variables in `finally`. No fallback to settings or inherited values.

- [ ] **Step 3: Make the verifier and live journey explicit**

`verify.ps1` validates the API environment pair and the live argument pair before installation/import/connection. It rejects shared API/live database names. Pass both live URLs to the live wrapper.

For each desktop/mobile iteration, `verify_live_journey.ps1` checks ports and database inactivity through the owner URL, calls reset with both URLs, starts FastAPI with only runtime `DATABASE_URL`, and starts Next with neither database credential. The Playwright config constructs allowlisted child environments instead of spreading `process.env`.

Keep `assertDatabaseJourney()` on the API URL and add `SELECT current_user` to its output; assert it equals the decoded runtime username and differs from the owner username supplied only to the wrapper.

- [ ] **Step 4: Provision distinct CI roles and UUID databases without changing caches**

Keep `actions/setup-node` npm caching, `actions/setup-python` pip caching, and the Next.js build-cache step byte-for-byte unless formatting requires movement. Replace static 16-hex names with two `uuid4().hex` names exported through `GITHUB_ENV`.

Use the PostgreSQL service bootstrap login only in a provisioning step. The Python uses `psycopg.sql.Identifier` to create/validate:

- non-superuser `ojcc_migrator` LOGIN CREATEDB CREATEROLE NOREPLICATION NOBYPASSRLS;
- non-superuser `ojcc_api` LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
- `ojcc_app` NOLOGIN with no dangerous attributes;
- membership `GRANT ojcc_app TO ojcc_api WITH INHERIT TRUE, SET FALSE, ADMIN FALSE`;
- both new databases owned by `ojcc_migrator`.

The step refuses same-named pre-existing roles with different properties and existing database names. CI then exports owner/application URL pairs for API and live targets, runs Alembic as owner after pair validation, and runs the verifier. Synthetic CI passwords remain CI-local examples; no production credential assumption or migration password is embedded.

- [ ] **Step 5: Run harness tests and cached verification dry path**

Run:

```powershell
python -m pytest services/api/tests/test_database_targets.py services/api/tests/test_demo_seed.py services/api/tests/test_verify_harness.py -q
```

Expected GREEN: all failure paths are pre-connection and environment restoration is exact. Inspect `.github/workflows/ci.yml` to confirm the three cache blocks remain.

- [ ] **Step 6: Review, record, and commit**

Run PowerShell parser checks on all three scripts, Ruff on seed/tests, web lint for the Playwright files, immutable hashes, and `git diff --check`.

Commit: `chore: propagate owner and api database credentials`

---

## Task 5: Prove the real API closed loop and negative boundary as the non-owner login

**Files:**

- Create: `services/api/tests/integration/test_non_owner_closed_loop.py`
- Modify: `services/api/tests/integration/test_database_privileges.py`
- Modify: `services/api/tests/closed_loop/test_task_commands.py`
- Modify: `services/api/tests/closed_loop/test_integrity.py`
- Modify: `services/api/tests/closed_loop/test_migration.py`
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`

**Interfaces:**

- Consumes a freshly migrated/seeded `DisposableDatabase` pair.
- Produces one signed-cookie, real-route API journey whose SQL sessions report the API login as `current_user`.
- Produces negative direct-SQL tests for the complete denied privilege surface.

- [ ] **Step 1: Write the failing non-owner journey**

Create/migrate/reset/seed with `migration_url`. Build the FastAPI session dependency from a separate engine using `application_url`; use real signed demo cookies, not an actor override. Execute the existing transportation flow:

1. read queue/workspace;
2. approve the exact pending proposal;
3. claim with future due time;
4. start and complete;
5. open a supporting-actor session and submit the follow-up response;
6. refresh navigator workspace, preview closure, and record Outcome;
7. reload both audience-safe histories and assert queue removal/persistence.

Before and after, query `current_user`, `session_user`, database owner, and task/audit/follow-up/Outcome counts. Assert migration objects are owned by the owner login and every API transaction runs as the distinct non-owner login.

Expected RED at 0006/current configuration: permission or role-boundary assertions fail.

- [ ] **Step 2: Add positive tests for every intended direct write**

Through signed API requests as the non-owner, cover one check-in submission INSERT, one proposal INSERT, one approval-decision INSERT, one safety-signal UPDATE/acknowledgement, one safety-signal-resolution INSERT, navigation-task UPDATE transitions, follow-up-response INSERT, and Outcome INSERT. Owner setup supplies prerequisite rows; API assertions verify no owner session performs the command.

- [ ] **Step 3: Add negative SQL tests for schema/privilege escalation**

As the API login, assert `InsufficientPrivilege` and rollback for:

- `CREATE TABLE`, `CREATE SCHEMA`, `CREATE FUNCTION`, and `CREATE TEMP TABLE`;
- `ALTER TABLE`, `ALTER FUNCTION`, `DROP TABLE`, and `TRUNCATE`;
- `GRANT SELECT ON organization TO PUBLIC`, `GRANT ojcc_app TO <role>`, and any role creation;
- `SET ROLE` to `ojcc_app` or the migration owner;
- DELETE from every table, including protected history;
- INSERT into `audit_event`, `workflow_transition_event`, `follow_up_request`, and every SELECT-only table;
- UPDATE outside `navigation_task`/`safety_signal`, plus disallowed column/state changes already rejected by lifecycle triggers;
- INSERT outside the six intended insert tables.

After each group, assert catalog ACLs and seeded row digests are unchanged. Use safely quoted identifiers from constants and parameters; do not form SQL from untrusted text.

- [ ] **Step 4: Lock the historical-task compatibility contract**

Add fixtures for open, assigned, in-progress, completed, and cancelled unbound historical tasks. Assert:

- all remain readable after 0007;
- assigned/in-progress unbound start/complete return the established `task_unbound` conflict and make no write;
- completed/cancelled history is display-only;
- an open unbound task with an exact approved proposal can be claimed, becomes bound atomically, and then follows normal start/complete;
- neither upgrade nor integrity inspection reports legacy unbound execution history as corruption;
- no historical authorization/task/audit row is added, removed, cancelled, or rewritten merely by migration/audit.

- [ ] **Step 5: Run focused security and closed-loop tests**

Run:

```powershell
python -m pytest `
  services/api/tests/integration/test_database_privileges.py `
  services/api/tests/integration/test_non_owner_closed_loop.py `
  services/api/tests/closed_loop/test_task_commands.py `
  services/api/tests/closed_loop/test_integrity.py `
  services/api/tests/closed_loop/test_migration.py -q -s
```

Expected GREEN: journey completes as the API login; all forbidden operations fail; historical contract remains unchanged. Record each disposable database name and disposition.

- [ ] **Step 6: Review, record, and commit**

Review for accidental owner-engine use in the journey, overly broad grants, sensitive URL output, and any historical binding check. Run Ruff, immutable hashes, and `git diff --check`.

Commit: `test: prove the non-owner closed-loop security boundary`

---

## Task 6: Document provisioning, run the complete gate, and stop for integration review

**Files:**

- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-09-11-database-least-privilege-migration-target-safety.md` only if implementation evidence reveals an approved wording correction
- Modify: `docs/superpowers/progress/2026-09-11-database-least-privilege-migration-target-safety.md`

**Interfaces:**

- Documents local/CI role provisioning and four explicit URLs (API owner/runtime and live owner/runtime) without production-provider assumptions.
- Produces final verification evidence and no deployment/merge action.

- [ ] **Step 1: Write local and CI provisioning documentation**

`.env.example` shows distinct synthetic-only local usernames and no persistent default target. README documents:

- bootstrap admin is used only to create/validate roles, membership, and databases, then removed from application/migration child environments;
- `MIGRATION_DATABASE_URL` belongs to the object owner and `DATABASE_URL` to the non-owner API login;
- both must name identical host/port/database and different usernames;
- immutable migration 0005's CREATEROLE consequence for replaying 0001→head;
- provider/production roles must be provisioned in the platform's credential manager and are not created by Alembic;
- passwords are never committed or printed;
- API/Next runtime receives no owner/admin secret;
- safe cleanup never terminates unknown connections.

Include PostgreSQL 16 primary references for [role/object grants](https://www.postgresql.org/docs/16/sql-grant.html), [database/schema privilege meanings](https://www.postgresql.org/docs/16/ddl-priv.html), [role membership options](https://www.postgresql.org/docs/16/role-membership.html), and [safe SECURITY DEFINER search paths](https://www.postgresql.org/docs/16/sql-createfunction.html).

- [ ] **Step 2: Provision fresh final-gate databases and record names before mutation**

Generate two new `ojcc_demo_<32 lowercase hex>` names, validate each exact name and nonexistence, append both names to the ledger as `planned`, then create them as the migration owner and update the ledger to `created`. Do not use or inspect persistent `ojcc` beyond the existing service health check; never drop it.

Construct four in-memory URLs without printing them:

- API `MIGRATION_DATABASE_URL` (owner/API database);
- API `DATABASE_URL` (API login/API database);
- live owner URL (owner/live database);
- live API URL (API login/live database).

- [ ] **Step 3: Run migration/version/seed gates in order**

With the API pair exported:

```powershell
./scripts/reset_demo.ps1 `
  -MigrationDatabaseUrl $env:MIGRATION_DATABASE_URL `
  -DatabaseUrl $env:DATABASE_URL `
  -ConfirmDatabaseName $apiDatabaseName
python -m alembic -c services/api/alembic.ini current
python -m alembic -c services/api/alembic.ini check
python scripts/export_openapi.py
npx --no-install openapi-typescript contracts/openapi.json -o apps/web/lib/api-types.ts
```

Expected: reset/seed/integrity succeeds; current is `0007_database_least_privilege (head)`; check reports no new upgrade operations. Contract-generation hashes are stable across a second generation and no generated contract diff exists because this milestone changes no HTTP schema.

- [ ] **Step 4: Run the full cached verification path**

Run:

```powershell
./scripts/verify.ps1 `
  -LiveMigrationDatabaseUrl $liveMigrationDatabaseUrl `
  -LiveDatabaseUrl $liveApplicationDatabaseUrl `
  -LiveConfirmDatabaseName $liveDatabaseName
```

The required gate includes locked dependency installation using existing caches, repository-wide Ruff/Pyright, read-only integrity as the API login, the entire pytest suite, web lint/Vitest/build, three mocked Playwright journeys, and the real desktop/mobile browser-to-PostgreSQL journey. The live database is reset by the owner before each viewport; FastAPI runs as the non-owner; post-journey integrity runs as the non-owner.

- [ ] **Step 5: Run final targeted security evidence**

Rerun the six immutable hashes, complete catalog matrix, role/ownership tests, upgrade/refusal/downgrade matrix, non-owner API journey, and negative direct-SQL suite. Run:

```powershell
git diff --check
git status --short --branch
```

Record commands, exit codes, test counts, current revision, role property booleans, ACL/function matrix counts, `current_user` evidence, every disposable database name, and any leftover database. Never record credentials.

- [ ] **Step 6: Clean up only owned disposable databases and finish the ledger**

After all owned engines/servers stop, revalidate each exact recorded name and issue ordinary `DROP DATABASE` through the owner. If a drop reports an active connection or ownership uncertainty, leave that database intact and report it; do not force terminate. Mark each ledger row `dropped` or `leftover` accurately.

- [ ] **Step 7: Final self-review and commit**

Review every Global Constraint and matrix row against the diff. Confirm migrations 0001–0006 unchanged, 0007 contains no task-binding/history logic, CI caches remain, the API/live server environments exclude owner/admin URLs, and no deferred milestone began.

Commit: `docs: document least-privilege database operations`

Stop and present the verified feature branch for the user's integration decision. Do not merge, push, deploy, clean worktrees, or begin Milestone 2.

---

## 3. Plan self-review result

- **Scope coverage:** Every requested credential, target-safety, propagation, privilege, role-property, function-security, historical-contract, non-owner journey, negative test, downgrade/refusal, provisioning-documentation, and disposable-database requirement maps to a task above.
- **Rejected-draft isolation:** No binding CHECK, upgrade task pre-scan, or legacy-unbound integrity violation is included. Deterministic audit-ID/FIPS and demo-session/cardinality findings remain deferred.
- **Object completeness:** The plan names all 33 ORM tables, all four canonical views, all 32 application-defined functions observed at the merged baseline, schema/database privileges, role memberships, and absence of sequences.
- **Target completeness:** Alembic, config, reset, seed, integrity invocation, verify, live browser, CI, all six database-creating helpers, and remaining database fixture call sites are explicit.
- **Safety:** No production code changes begin without approval; every destructive operation is limited to a freshly named, recorded, validated disposable database; unknown connections are never terminated.
- **No placeholders:** The plan contains exact paths, interfaces, assertions, commands, expected outcomes, and commit boundaries.
