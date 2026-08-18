# Oncology Domain Schema Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before each commit.

**Goal:** Replace the simplified Task-5 schema with the approved, tenant-safe domain model while preserving the working patient check-in and navigator queue journeys.

**Architecture:** Keep the relational database as the source of truth. Establish terminal states through immutable authorizing child records, expose lifecycle state through canonical views, use explicit tenant-aware foreign keys for every state-authorizing target, and make PostgreSQL triggers the single authoritative enforcement point for cross-row invariants. Adapt repositories and APIs only after the schema foundation is green.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16+, React, TypeScript, Next.js, pytest, Ruff, Pyright, Vitest, Playwright, Docker Compose

**Canonical product design:** [`docs/product-design.md`](../../product-design.md), especially §8.1–§8.14

**Schema specification:** [`docs/superpowers/specs/2026-08-18-oncology-domain-schema-reconciliation-design.md`](../specs/2026-08-18-oncology-domain-schema-reconciliation-design.md)

**Supersedes:** Tasks 6–12 of the August 17 implementation plan. Tasks 1–5 are completed historical baseline work.

## Completed baseline

| Completed step | Accepted result | Commit evidence |
|---|---|---|
| 1 | Repository foundation, CI, database, web shell, and browser smoke test | `74306b6`, `80beb82` |
| 2 | Core synthetic domain and repository boundaries | `41365ac`, `5d9a9ad` |
| 3 | Tenant-scoped demo authentication | `47a3514`, `ad749cb` |
| 4 | Immutable patient check-in journey and FHIR-shaped export | `faf9d92`, `2df8a36`, `b931e9a` |
| 5 | Explainable navigator work queue and priority policy | `da7b51c` through accepted fix head `cea8d5b` |

These commits are evidence of working vertical slices, not approval to preserve the simplified Task-5 relationships. The migrations below adapt those journeys to the reconciled model before new feature development.

## Global constraints

- Do not edit `services/api/alembic/versions/0001_core_domain.py`; it is the immutable Task-5 baseline.
- Every migration must upgrade both an empty database and a database already at revision `0001` with representative synthetic rows.
- Do not retain superseded relationships through permanent dual writes. Add, validate, migrate, switch reads, then drop the obsolete column in the same bounded migration sequence.
- Do not silently invent clinical authorship, tenant membership, approval, or terminal state during data migration. Fail with a precise diagnostic when a disposable demo row is ambiguous; the documented demo reset is the recovery path.
- All organization-scoped repositories and services receive `organization_id` explicitly. Session state selects an active organization; a user's primary organization never authorizes access.
- PostgreSQL integration tests are required for composite foreign keys, exclusion constraints, triggers, row locks, append-only guards, and views. SQLite or fake sessions may supplement but may not replace them.
- Each behavior change follows RED → GREEN → REFACTOR. Record the failing assertion before production changes.
- Regenerate `contracts/openapi.json` and `apps/web/lib/api-types.ts` whenever an API contract changes. Never hand-edit generated types.
- Keep all public fixtures synthetic. No production credentials, live patient data, or real clinical messaging.
- Each task ends with its focused checks, full API checks, `git diff --check`, a self-review against the schema specification, and one intentional commit.

**Command convention:** Run Python, pytest, Ruff, Pyright, and Alembic commands from `services/api/`. Run Git, npm workspace, OpenAPI export, Docker Compose, and root PowerShell scripts from the repository root.

## Migration sequence

| Revision | Boundary |
|---|---|
| `0002_identity_pathway_submission` | Multi-organization identity, patient identity bridge, pathway history, immutable submission provenance and correction chain |
| `0003_need_task_outcome_lifecycle` | Need origins, task lifecycle, outcome-derived closure, automatic task cancellation |
| `0004_safety_approval_lifecycle` | Deterministic/effective signal severity, resolutions, policies, proposals, decisions, approval application |
| `0005_workflow_knowledge_audit` | Workflow lineage, manual review, resources, knowledge approval, four-form audit actors, append-only protections |

---

### Task 1: Split the model module without changing the Task-5 schema

**Files:**

- Create: `services/api/app/db/models/__init__.py`
- Create: `services/api/app/db/models/shared.py`
- Create: `services/api/app/db/models/identity.py`
- Create: `services/api/app/db/models/pathways.py`
- Create: `services/api/app/db/models/submissions.py`
- Create: `services/api/app/db/models/needs.py`
- Create: `services/api/app/db/models/safety.py`
- Create: `services/api/app/db/models/approvals.py`
- Create: `services/api/app/db/models/workflow.py`
- Create: `services/api/app/db/models/knowledge.py`
- Create: `services/api/app/db/models/audit.py`
- Delete: `services/api/app/db/models.py`
- Modify: `services/api/alembic/env.py`
- Modify: `services/api/app/db/repositories.py`
- Test: `services/api/tests/test_model_package.py`
- Test: `services/api/tests/test_core_domain_metadata.py`

- [ ] **Step 1: Write a failing package-contract test**

```python
from app.db import models


def test_model_package_preserves_task_five_table_set() -> None:
    assert hasattr(models, "__path__")
    assert set(models.Base.metadata.tables) == EXPECTED_TASK_FIVE_TABLES
    assert models.Organization.__tablename__ == "organization"
    assert models.SafetySignal.__tablename__ == "safety_signal"
```

Run:

```powershell
python -m pytest tests/test_model_package.py -q
```

Expected: FAIL because `app.db.models` is still a single module and the package contract does not exist.

- [ ] **Step 2: Move models by domain and preserve public imports**

`models/__init__.py` must import every mapped class exactly once and re-export the names used by repositories, tests, and Alembic. `shared.py` owns `Base`, UUID/timestamp helpers, and shared naming conventions. Do not rename tables, columns, indexes, constraints, or enums in this task.

- [ ] **Step 3: Make Alembic load the complete metadata**

```python
from app.db.models import Base

target_metadata = Base.metadata
```

Run:

```powershell
python -m pytest tests/test_model_package.py tests/test_core_domain_metadata.py -q
python -m alembic check
```

Expected: PASS with no generated schema difference.

- [ ] **Step 4: Run the full API safety net and commit**

```powershell
python -m pytest tests -q
python -m ruff check app tests
python -m pyright app
git diff --check
git add services/api
git commit -m "refactor: split domain model package"
```

---

### Task 2: Reconcile identity, pathway history, and submission provenance

**Files:**

- Modify: `services/api/app/db/models/identity.py`
- Modify: `services/api/app/db/models/pathways.py`
- Modify: `services/api/app/db/models/submissions.py`
- Modify: `services/api/app/domain/enums.py`
- Modify: `services/api/app/auth/models.py`
- Modify: `services/api/app/auth/dependencies.py`
- Modify: `services/api/app/api/demo_sessions.py`
- Modify: `services/api/app/api/patient_check_ins.py`
- Modify: `services/api/app/domain/check_ins.py`
- Create: `services/api/alembic/versions/0002_identity_pathway_submission.py`
- Create: `services/api/tests/integration/test_identity_pathway_submission.py`
- Modify: `services/api/tests/test_auth.py`
- Modify: `services/api/tests/test_check_ins.py`
- Modify: `services/api/tests/test_core_domain_migration.py`

- [ ] **Step 1: Write failing tests for the identity boundary**

Add tests proving:

```python
assert patient_actor.user_id != patient_actor.patient_id
assert patient_actor.organization_id == link.organization_id
```

Also prove that a link from another organization, a revoked role assignment, or an absent patient link cannot create a patient session or submit a check-in.

Run:

```powershell
python -m pytest tests/test_auth.py tests/integration/test_identity_pathway_submission.py -q
```

Expected: FAIL because `CurrentActor` has no separate `patient_id` and the link table does not exist.

- [ ] **Step 2: Add temporal organization authority and patient identity links**

Implement these contracts:

```python
class CurrentActor(BaseModel):
    user_id: UUID
    organization_id: UUID
    role: Role
    patient_id: UUID | None = None


async def resolve_patient_actor(
    session: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    at: datetime,
) -> CurrentActor: ...
```

`User.organization_id` becomes nullable `primary_organization_id`. `RoleAssignment` gains `granted_at` and `revoked_at`; revocation closes an interval and never deletes a row. Add organization-scoped `PatientIdentityLink` with `linked_at` and nullable `revoked_at`; partial unique indexes allow at most one non-revoked link per user and patient.

- [ ] **Step 3: Add pathway and submission migration tests**

Prove that overlapping pathway assignments fail, adjacent half-open assignments succeed, imports require external provenance, human submissions require an author, and two submitted corrections cannot supersede the same predecessor.

The migration must enable `btree_gist` and create:

```sql
EXCLUDE USING gist (
  organization_id WITH =,
  care_episode_id WITH =,
  tstzrange(effective_from, effective_to, '[)') WITH &&
)
```

`CheckInSubmission` gains `submission_source`, `submitted_by_user_id`, import provenance fields, and `supersedes_submission_id UNIQUE`. Drafts remain outside this table. Create `active_check_in_submission` so reports count only rows with no submitted successor.

- [ ] **Step 4: Switch patient authorization to the explicit link**

Replace every `actor.user_id == submission.patient_id` assumption with `actor.patient_id`. Preserve the same public patient experience and require organization, patient, episode, and exact questionnaire version on every submission.

Run:

```powershell
python -m pytest tests/test_auth.py tests/test_check_ins.py tests/integration/test_identity_pathway_submission.py tests/test_core_domain_migration.py -q
python -m alembic upgrade head
```

Expected: PASS, including upgrade from revision `0001` fixtures.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest tests -q
python -m ruff check app tests
python -m pyright app
git diff --check
git add services/api
git commit -m "feat: reconcile identity and submission provenance"
```

---

### Task 3: Make need closure outcome-driven and task-safe

**Files:**

- Modify: `services/api/app/db/models/needs.py`
- Modify: `services/api/app/db/models/audit.py`
- Modify: `services/api/app/domain/enums.py`
- Modify: `services/api/app/domain/needs.py`
- Create: `services/api/app/domain/outcomes.py`
- Create: `services/api/app/api/navigator_outcomes.py`
- Modify: `services/api/app/main.py`
- Create: `services/api/alembic/versions/0003_need_task_outcome_lifecycle.py`
- Create: `services/api/tests/test_outcomes.py`
- Create: `services/api/tests/integration/test_need_task_lifecycle.py`
- Modify: `services/api/tests/test_navigator_queue.py`

- [ ] **Step 1: Write failing lifecycle tests**

Cover these assertions:

```python
assert closed_need.effective_state == "closed"
assert all(task.state == "cancelled" for task in open_tasks)
assert all(task.cancellation_reason == "need_closed" for task in open_tasks)
assert completed_task.state == "completed"
```

Prove there is one task-level AuditEvent per automatic cancellation; actor and timestamp equal the Outcome recorder and timestamp. Prove task creation, assignment, or restart fails after closure and reopening creates a new need with no inherited tasks.

- [ ] **Step 2: Replace closure columns with authorizing records**

`ReportedNeed` stores only `open|in_progress` and exactly one origin:

```sql
CHECK (num_nonnulls(source_submission_id, reopened_from_need_id) = 1)
```

Make `reopened_from_need_id` unique. Remove `ReportedNeed.outcome_id`, stored terminal values, and closure timestamps. Make `Outcome.reported_need_id NOT NULL UNIQUE`. Create `effective_need_state` and make all queues and exports read it rather than raw status.

- [ ] **Step 3: Implement one authoritative closure trigger**

The `AFTER INSERT ON outcome` trigger must lock the need and its non-terminal tasks, cancel those tasks using `need_closed`, attribute cancellation to the Outcome recorder, and insert one AuditEvent per task in the same transaction. Do not duplicate this logic in the service layer. Add separate database guards for task creation and active-state transitions on a closed need.

- [ ] **Step 4: Add closure preview and outcome command**

Expose a read-only preview listing the tasks that closure will cancel, followed by one idempotent outcome command. The UI may warn but must not block closure. Repeated commands with the same idempotency key return the existing Outcome.

Run:

```powershell
python -m pytest tests/test_outcomes.py tests/test_navigator_queue.py tests/integration/test_need_task_lifecycle.py -q
python -m alembic upgrade head
```

Expected: PASS, including the database guard tests.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest tests -q
python -m ruff check app tests
python -m pyright app
git diff --check
git add services/api
git commit -m "feat: derive need closure from outcomes"
```

---

### Task 4: Implement safety signals and risk-based approval

**Files:**

- Modify: `services/api/app/db/models/safety.py`
- Modify: `services/api/app/db/models/approvals.py`
- Modify: `services/api/app/domain/enums.py`
- Create: `services/api/app/domain/safety.py`
- Create: `services/api/app/domain/approvals.py`
- Create: `services/api/app/api/safety_signals.py`
- Create: `services/api/app/api/proposed_changes.py`
- Create: `services/api/alembic/versions/0004_safety_approval_lifecycle.py`
- Create: `services/api/tests/test_safety_signals.py`
- Create: `services/api/tests/test_approvals.py`
- Create: `services/api/tests/integration/test_safety_approval_concurrency.py`

- [ ] **Step 1: Write failing severity and terminal-state tests**

Prove that automation cannot set `effective_level < deterministic_level`, an approved human override can, resolution requires acknowledgement, dismissal requires acknowledgement and an approved proposal, and neither terminal path can be reversed.

Also start two database transactions against one signal: one inserts `SafetySignalResolution`; the other completes dismissal approval. Assert exactly one commits.

- [ ] **Step 2: Reconcile signal origins and resolution**

Every SafetySignal has exactly one source submission or unique predecessor signal. Both deterministic and human-escalated signals reference a versioned SignalRule; the reserved human-escalation rule keeps provenance non-null. Add immutable `deterministic_level`, materialized `effective_level`, rule ID/version, acknowledgement fields, and append-only `SafetySignalResolution(safety_signal_id UNIQUE, resolved_by_user_id, resolved_at, resolution_reason)`.

- [ ] **Step 3: Add policies, proposals, and decisions**

Use explicit target foreign keys and enforce:

```sql
CHECK (num_nonnulls(safety_signal_id, navigation_task_id, patient_message_id) = 1)
```

A CASE constraint maps each `change_type` to its target. ProposedChange owns proposer, rationale, proposed JSONB, schema identity/version, policy snapshots, and `supersedes_proposed_change_id UNIQUE`. ApprovalDecision stores only `approved|declined`, authorizer, qualifying RoleAssignment, role snapshot, timestamp, and decline reason. Add `UNIQUE(proposed_change_id, authorized_by_user_id)`.

For signal changes, create parent key `UNIQUE (proposed_change_id, safety_signal_id, organization_id, change_type)`. Both signal-side proposal pointers use composite foreign keys to this key and are individually unique, preventing one proposal from applying to multiple signals.

- [ ] **Step 4: Implement derived proposal state and final application**

`effective_proposed_change_state` derives `superseded`, `declined`, `approved`, then `pending`. Any qualifying decline is terminal. The revision trigger rejects successors to approved, applied, or already superseded proposals.

The final decision path locks the proposal and target, revalidates that the RoleAssignment belongs to the same organization and covers `authorized_at`, then applies the change atomically. High-risk dismissal tests use immutable `deterministic_level`; at or above threshold require two distinct qualified humans and exclude the proposer. Agents cannot propose or approve dismissal.

- [ ] **Step 5: Create effective safety state and verify both exclusion directions**

`effective_safety_signal_state` derives dismissal from an applied dismissal proposal, resolution from the resolution row, then acknowledged/open. Resolution insertion rejects an applied dismissal; final dismissal approval rejects an existing resolution. The current override pointer is a convenience only; approved proposals targeted to the signal are history and source of truth.

Run:

```powershell
python -m pytest tests/test_safety_signals.py tests/test_approvals.py tests/integration/test_safety_approval_concurrency.py -q
python -m alembic upgrade head
python -m pytest tests -q
python -m ruff check app tests
python -m pyright app
git diff --check
git add services/api
git commit -m "feat: add risk-based safety approval lifecycle"
```

---

### Task 5: Add workflow lineage, governed knowledge, and complete audit actors

**Files:**

- Modify: `services/api/app/db/models/workflow.py`
- Modify: `services/api/app/db/models/knowledge.py`
- Modify: `services/api/app/db/models/audit.py`
- Modify: `services/api/app/domain/enums.py`
- Create: `services/api/alembic/versions/0005_workflow_knowledge_audit.py`
- Create: `services/api/tests/test_workflow_lineage.py`
- Create: `services/api/tests/test_knowledge_governance.py`
- Create: `services/api/tests/integration/test_audit_immutability.py`

- [ ] **Step 1: Write failing lineage and actor-shape tests**

Prove WorkflowRun owns ordered append-only transition events, one transition may contain multiple AgentRuns, dead-lettered automation owns a ManualReviewTask rather than a NavigationTask, withdrawn knowledge cannot support new runs, and historical citations remain readable.

For AuditEvent, test all four valid actor forms and reject every mixed or incomplete form:

```sql
CASE actor_type
  WHEN 'user' THEN user_actor_id IS NOT NULL AND agent_run_actor_id IS NULL
  WHEN 'agent' THEN agent_run_actor_id IS NOT NULL AND user_actor_id IS NULL
  WHEN 'policy' THEN policy_name IS NOT NULL AND policy_version IS NOT NULL
  WHEN 'system' THEN system_name IS NOT NULL AND system_version IS NOT NULL
  ELSE false
END
```

The production constraint must additionally require all nonmatching identity columns to be null and component strings to be nonblank.

- [ ] **Step 2: Replace flat workflow transitions**

WorkflowRun stores durable identity and materialized current state. WorkflowTransitionEvent is the append-only event stream and records from-state, to-state, actor, reason, and timestamp. AgentRun optionally belongs to a transition. ManualReviewTask stores the operational failure, retry context, state, assignment, and resolution without impersonating patient work.

- [ ] **Step 3: Govern resources and knowledge**

Add organization scope to Resource. NavigationTaskResource snapshots proposed matching facts; its approval comes from `authorize_navigation_task`, while delivery is a later operational event. OrganizationKnowledgeApproval governs exact immutable KnowledgeDocument versions with effective and withdrawal timestamps. AgentRunCitation keeps exact document version and passage even after withdrawal.

- [ ] **Step 4: Enforce append-only records**

Revoke UPDATE and DELETE from the application role and add BEFORE UPDATE OR DELETE guards for CheckInSubmission, ProposedChange, ApprovalDecision, Outcome, SafetySignalResolution, AuditEvent, and WorkflowTransitionEvent. Tests must exercise the application database role rather than only ORM behavior.

Run:

```powershell
python -m pytest tests/test_workflow_lineage.py tests/test_knowledge_governance.py tests/integration/test_audit_immutability.py -q
python -m alembic upgrade head
python -m pytest tests -q
python -m ruff check app tests
python -m pyright app
git diff --check
git add services/api
git commit -m "feat: add governed workflow and audit lineage"
```

---

### Task 6: Adapt repositories, APIs, FHIR, and the two existing journeys

**Files:**

- Modify: `services/api/app/db/repositories.py`
- Modify: `services/api/app/api/patient_check_ins.py`
- Modify: `services/api/app/api/navigator_queue.py`
- Modify: `services/api/app/domain/check_ins.py`
- Modify: `services/api/app/domain/needs.py`
- Modify: `services/api/app/fhir/check_in_mapper.py`
- Modify: `services/api/tests/test_check_ins.py`
- Modify: `services/api/tests/test_navigator_queue.py`
- Modify: `services/api/tests/test_fhir_mapping.py`
- Modify: `apps/web/lib/api-client.ts`
- Modify: `apps/web/components/patient/check-in-flow.tsx`
- Modify: `apps/web/app/demo/navigator/page.tsx`
- Modify: `apps/web/e2e/patient-check-in.spec.ts`
- Modify: `apps/web/e2e/navigator-command-center.spec.ts`
- Regenerate: `contracts/openapi.json`
- Regenerate: `apps/web/lib/api-types.ts`

- [ ] **Step 1: Write failing compatibility tests against canonical views**

Patient reads must resolve active submissions and explicit patient identity. Navigator queue reads must exclude closed needs and routine `need_closed` task cancellations. No repository may compare raw need or signal status to a terminal value.

- [ ] **Step 2: Update repositories and typed API contracts**

Require `organization_id` on every repository method. Join `effective_need_state`, `effective_safety_signal_state`, and `effective_proposed_change_state` for public reads. Keep the existing patient and navigator URLs stable unless the generated contract demonstrates a necessary breaking change.

- [ ] **Step 3: Expand the FHIR R4 boundary**

Test and implement:

- One QuestionnaireResponse per immutable submission
- Superseded responses rendered as `amended`
- Correction edge in Provenance.entity with role `revision`
- Patient-supplied Observation tags
- Profiled DetectedIssue for safety signals, including declared dual-severity and dismissal extensions
- Provenance for applied proposals with separately typed proposer and all qualifying authorizers

Pending, declined, and superseded proposals, AuditEvent, and ManualReviewTask remain internal.

- [ ] **Step 4: Regenerate contracts and verify both journeys**

```powershell
python scripts/export_openapi.py
npx --no-install openapi-typescript contracts/openapi.json -o apps/web/lib/api-types.ts
npm --workspace apps/web run test
npm --workspace apps/web run lint
npm --workspace apps/web run build
npm --workspace apps/web run test:e2e -- patient-check-in.spec.ts navigator-command-center.spec.ts
```

Expected: the patient completes and corrects a check-in, and the navigator sees explainable open work without closed-need leakage.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest tests -q
python -m ruff check app tests
python -m pyright app
git diff --check
git add services/api apps/web contracts
git commit -m "feat: adapt journeys to reconciled domain model"
```

---

### Task 7: Add restore integrity, deterministic reset, and release evidence

**Files:**

- Create: `services/api/app/db/integrity.py`
- Create: `services/api/scripts/check_integrity.py`
- Create: `services/api/scripts/seed_demo.py`
- Create: `scripts/reset_demo.ps1`
- Modify: `scripts/verify.ps1`
- Create: `services/api/tests/integration/test_restore_integrity.py`
- Create: `services/api/tests/test_demo_seed.py`
- Modify: `README.md`
- Modify: `docs/product-design.md`

- [ ] **Step 1: Write a failing restore-audit test matrix**

Inject each violation with triggers disabled, then assert the checker reports its exact category: closed need with active task, task mutated after closure, missing cancellation AuditEvent, signal with both terminal paths, applied proposal without qualifying approvals, cross-organization approval, invalid audit actor, overlapping pathway assignments, and forked successor chains.

- [ ] **Step 2: Implement a read-only integrity command**

```python
def inspect_integrity(session: Session) -> list[IntegrityViolation]: ...
```

The command exits nonzero when violations exist. It must report evidence and identifiers, never repair, fabricate approval, or choose between conflicting terminal records.

Document and test that this command is mandatory after restores, ETL, or bulk loads that set `session_replication_role = replica` and thereby bypass trigger enforcement.

- [ ] **Step 3: Rebuild the deterministic synthetic seed**

Seed separate User and SyntheticPatient identities joined by PatientIdentityLink, temporal RoleAssignments, one current pathway assignment, versioned submissions, open and closed needs, task cancellations, safety states, approval histories, workflow lineage, governed citations, and all four AuditEvent actor shapes. Run the seed twice and prove idempotent output.

- [ ] **Step 4: Verify fresh and upgraded databases**

Run the documented matrix against:

1. Empty PostgreSQL → Alembic head → seed → integrity check.
2. `0001` PostgreSQL with representative Task-5 rows → Alembic head → integrity check.
3. Head PostgreSQL → reset → seed twice → integrity check.

```powershell
docker compose up -d db
python -m alembic upgrade head
python scripts/check_integrity.py
powershell -ExecutionPolicy Bypass -File scripts/reset_demo.ps1
powershell -ExecutionPolicy Bypass -File scripts/verify.ps1
git diff --check
```

- [ ] **Step 5: Review documentation and commit**

Confirm the implementation still matches §8.1–§8.14, record any intentionally deferred fork in `docs/product-design.md`, and document the trigger-bypass restore audit in the README.

```powershell
git add services/api scripts README.md docs/product-design.md
git commit -m "test: verify reconciled domain integrity"
```

## Completion gate

Do not resume the superseded feature Tasks 6–12 until all seven reconciliation tasks pass. Completion requires:

- `0001` remains byte-for-byte unchanged.
- Alembic reaches head from empty and Task-5 databases.
- Metadata, views, triggers, composite keys, and exclusions match the approved specification.
- Cross-tenant and concurrency tests fail safely.
- Patient check-in and navigator command-center journeys pass in a browser.
- OpenAPI and generated TypeScript are current.
- The restore integrity command is clean on the deterministic public seed.
- A final reviewer finds no Critical or Important discrepancy against the schema specification.

After this gate, write fresh bounded plans for closed-loop product behavior, orchestration, retrieval, evaluation, and deployment rather than reviving the historical plan.
