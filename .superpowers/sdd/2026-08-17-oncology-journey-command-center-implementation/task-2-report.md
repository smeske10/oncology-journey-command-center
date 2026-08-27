# Task 2 implementation report: relational domain and migration boundary

## Scope

Implemented the PostgreSQL relational-domain boundary for the Oncology Journey Command Center. The API now has UUIDv7 SQLAlchemy entities, PostgreSQL JSONB and native enum mappings, explicit state check constraints, organization-first tenant query indexes, Alembic configuration and an initial migration, a transaction interface, and integration/metadata coverage.

`app.config.Settings` now owns `database_url`, sourced from `DATABASE_URL` with the local synthetic PostgreSQL URL as its development default. The existing health-only API route was not changed.

## RED evidence

The lifecycle persistence test was created before the persistence implementation and run with:

```powershell
python -m pytest services/api/tests/integration/test_core_domain.py -q
```

It failed at collection because the required persistence dependency/model boundary did not exist:

```text
E   ModuleNotFoundError: No module named 'sqlalchemy'
```

This is the expected pre-implementation failure: no SQLAlchemy models or persistence dependencies were available.

## GREEN and offline validation evidence

After the implementation, the isolated locked Python environment ran:

```powershell
python -m alembic -c services/api/alembic.ini upgrade head --sql
```

The offline PostgreSQL SQL emitted all expected native enum types (including `need_status`), JSONB columns, the `reported_need` table, its explicit lifecycle constraint, and its organization-leading query index:

```text
CREATE TYPE need_status AS ENUM ('open', 'in_progress', 'resolved', 'closed');
CREATE TABLE reported_need (
    evidence JSONB NOT NULL,
    CONSTRAINT ck_reported_need_status_state CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
CREATE INDEX ix_reported_need_org_patient_status_created ON reported_need (organization_id, patient_id, status, created_at);
```

The final non-Docker API validation was:

```powershell
python -m ruff check .
python -m pyright --pythonpath C:\tmp\ojcc-domain-venv\Scripts\python.exe
python -m pytest -q -p no:cacheprovider
```

Output:

```text
All checks passed!
0 errors, 0 warnings, 0 informations
3 passed, 1 skipped in 2.72s
```

The pure metadata test verifies all 17 required model tables, PostgreSQL JSONB rendering, and that every tenant-scoped declared query index starts with `organization_id`.

## Integration-test environment skip

Docker Desktop and a local PostgreSQL service are unavailable in this environment, so no SQLite replacement was used. The real PostgreSQL lifecycle test remains unchanged and skips only when its configured database host cannot be reached:

```powershell
python -m pytest services/api/tests/integration/test_core_domain.py -q -rs -p no:cacheprovider
```

```text
SKIPPED [1] services\api\tests\integration\test_core_domain.py:95: PostgreSQL DATABASE_URL is not reachable for core-domain integration test
1 skipped in 2.36s
```

With a reachable PostgreSQL `DATABASE_URL`, run the migration normally and then the same integration test without changes:

```powershell
python -m alembic -c services/api/alembic.ini upgrade head
python -m pytest services/api/tests/integration/test_core_domain.py -q
```

## Files changed

- `services/api/app/config.py`
- `services/api/app/domain/enums.py`
- `services/api/app/domain/types.py`
- `services/api/app/db/base.py`
- `services/api/app/db/models.py`
- `services/api/app/db/repositories.py`
- `services/api/app/db/session.py`
- `services/api/alembic.ini`
- `services/api/alembic/env.py`
- `services/api/alembic/versions/0001_core_domain.py`
- `services/api/tests/integration/test_core_domain.py`
- `services/api/tests/test_core_domain_metadata.py`
- `services/api/pyproject.toml`
- `services/api/requirements.lock`

## Self-review

- Confirmed all requested model classes are present: `Organization`, `User`, `RoleAssignment`, `SyntheticPatient`, `CareEpisode`, `PathwayDefinition`, `CheckInDefinition`, `CheckInSubmission`, `ReportedNeed`, `SafetySignal`, `NavigationTask`, `ApprovalDecision`, `Resource`, `KnowledgeDocument`, `AgentRun`, `Outcome`, and `AuditEvent`.
- Confirmed each entity uses a UUIDv7 default and each tenant-scoped query index begins with `organization_id`.
- Confirmed native PostgreSQL enums have matching explicit check constraints.
- Confirmed `ReportedNeed` contains both `source_submission_id` and `source_submission`, with the relationship exercised by the lifecycle test.
- Confirmed the lifecycle fixture creates organization, patient, pathway, and check-in-definition scope.
- Confirmed `git diff --check` reported no whitespace errors before the commit review.

## Concerns

- No reachable PostgreSQL instance was available, so the online Alembic upgrade and persistence assertion could not be executed here. The offline SQL was generated from the real PostgreSQL migration and the integration test retains an explicit, environment-only skip; it will execute unchanged against PostgreSQL.
- A temporary isolated virtual environment under `C:\tmp` was used only to resolve the regenerated hash lock and run local verification because the shared Python installation is read-only. It is not part of the repository.

---

## Review fix round: immutable migration and tenant integrity

### Findings addressed

1. **Immutable initial revision:** `0001_core_domain.py` no longer imports `app.db.base`, `app.db.models`, or calls `Base.metadata.create_all()` / `drop_all()`. It now owns a static Alembic schema snapshot: explicit PostgreSQL enum creation, `op.create_table` operations for all 17 tables, named foreign-key/check/unique constraints, explicit organization-leading indexes, reverse index/table drops, and enum drops.
2. **Tenant-safe references:** tenant-owned entities now expose `UNIQUE (organization_id, id)` and every practical tenant-owned reference is a composite `FOREIGN KEY (organization_id, related_id) REFERENCES related_table (organization_id, id)`. This rejects a row in one organization that points to an otherwise valid related row in another.
3. **Scoped repository boundary:** `SqlAlchemyUnitOfWork` now requires an `organization_id` at construction, exposes only scoped `add` and `get` operations, validates an added entity’s organization, and applies organization filtering to lookups. Its raw SQLAlchemy session is private. `SessionLocal` remains available in the database session module for infrastructure-only Alembic/seed/setup use.

### New RED evidence

The new focused tests were written against the reviewed implementation and failed before the fixes with the intended contracts:

```text
FAILED test_initial_migration_is_an_immutable_explicit_schema_snapshot
assert 'app.db.models' not in migration source

FAILED test_tenant_owned_relationships_use_organization_aware_foreign_keys
assert {'organization'} >= {'user_account'}

FAILED test_unit_of_work_requires_scope_and_rejects_cross_tenant_operations
TypeError: SqlAlchemyUnitOfWork.__init__() takes 2 positional arguments but 3 were given
```

### GREEN evidence

The focused fix suite was run after the changes:

```powershell
python -m pytest tests/test_core_domain_migration.py tests/test_core_domain_metadata.py tests/test_repositories.py -q -p no:cacheprovider
```

Controller-confirmed result:

```text
4 passed in 0.95s
```

The locally run focused suite including the unchanged real PostgreSQL integration contract reported:

```text
4 passed, 1 skipped in 3.01s
SKIPPED [1] tests\integration\test_core_domain.py:95: PostgreSQL DATABASE_URL is not reachable for core-domain integration test
```

The migration immutability test reads the revision source to reject live ORM imports/metadata delegation, executes `python -m alembic -c services/api/alembic.ini upgrade head --sql`, and verifies every current table, named constraint, and declared index appears in the generated PostgreSQL SQL. The metadata test verifies the organization-first composite foreign keys. The repository test proves scope is mandatory and a mismatched-organization entity is rejected before it reaches the session.

Offline migration command also completed during the focused verification and emitted all 17 `CREATE TABLE` statements, PostgreSQL enum types, and organization-first indexes. It emits composite FKs such as:

```text
FOREIGN KEY(organization_id, source_submission_id)
REFERENCES check_in_submission (organization_id, id)
```

### Static checks and verifier status

The following completed after the fix:

```text
python -m ruff check .
All checks passed!

python -m pyright --pythonpath C:\tmp\ojcc-domain-venv\Scripts\python.exe
0 errors, 0 warnings, 0 informations
```

`git diff --check` was run immediately before the fix commit; it returned no whitespace findings (Git emitted only its ambient fsmonitor/line-ending warnings).

The full non-Docker verifier did **not** complete in this fix round. The first sandboxed invocation of:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

failed while hash-installing `alembic==1.19.1` because sandboxed network access was denied. Two approved reruns were interrupted by the controller before completion to keep the fix loop bounded. No successful full-verifier claim is made here.

### Self-review and tradeoffs

- The migration is intentionally verbose: it captures the revision’s exact DDL rather than tracking future ORM metadata changes.
- Composite `(organization_id, id)` uniqueness is retained alongside UUID primary keys solely to permit database-enforced tenant-aware foreign keys; UUIDs remain the identity and public lookup value.
- Nullable tenant-owned references retain nullable composite FK behavior in PostgreSQL: when the related id is absent, no relationship is asserted; when present, the organization must match.
- No SQLite substitute was used. The real PostgreSQL lifecycle test remains narrowly environment-skipped until a reachable `DATABASE_URL` exists.
