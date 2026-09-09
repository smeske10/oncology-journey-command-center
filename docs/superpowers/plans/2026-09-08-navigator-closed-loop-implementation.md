# Navigator closed loop: implementation plan

Created: 2026-09-08. Reviewed and rewritten against the repository: 2026-09-09.

Status: **Proposed; awaiting user approval. This document does not authorize implementation.**

Baseline: master and origin/master at 5f12cd613d6aea0f75caec963d4f83a49ce73379.

**Goal:** Make one seeded, synthetic transportation need operable from evidence review through approved work, patient follow-up, and navigator-recorded closure, with durable, audience-safe history.

**Architecture:** Extend the existing FastAPI/SQLAlchemy/PostgreSQL domain and Next.js surfaces. Keep approval decisions and Outcome closure database-authoritative. Bind executable work to an exact approved proposal; add immutable follow-up records; project timelines from existing records rather than introducing another event store.

**Stack:** Existing locked Python/FastAPI/Pydantic/SQLAlchemy/Alembic dependencies; PostgreSQL 16; Next.js 16/React 19/TypeScript; pytest, Ruff, Pyright, Vitest, Playwright. Use Python 3.12 as CI does and the repository-supported Node version. Do not upgrade dependencies as part of this milestone.

**Specification:** This document supplies the implementation decisions for the bounded slice within [product-design.md](../../product-design.md), especially §§4, 8, 10, 13–15. The completed [reconciliation plan](2026-08-18-oncology-domain-schema-reconciliation-implementation.md) and its completion gate are historical prerequisites, not a work backlog.

## 1. Execution contract for a fresh coding session

After explicit approval, use the applicable planning-execution, worktree, TDD, and verification skills. Execute the work packages below sequentially in the same session by default; a different model does not change the acceptance criteria. Do not dispatch agents unless the user authorizes delegation. Do not reinterpret this as reconciliation “Task 8,” or resume superseded historical Tasks 6–12.

Before editing production code:

1. Read applicable AGENTS.md files, the complete current plan, README.md, product-design.md §14, and .superpowers/sdd/2026-08-18-oncology-domain-schema-reconciliation-implementation/progress.md.
2. Confirm approval is present in the conversation. A request to review this plan is not approval.
3. Confirm HEAD, origin/master, status, and worktree list. Stop for unexpected overlapping edits or a changed baseline; investigate before replacing any user work. An untracked copy of this plan is expected in the main checkout.
4. Only now create an isolated feature branch/worktree with the worktree skill. Leave master, the retired branches, and the completed reconciliation ledger alone. Never automatically push, merge, or delete branches.
5. Preserve this reviewed document in the new worktree before coding. Untracked files are not carried by Git worktree creation: add the same content with apply_patch, compare its hash with the main-checkout original, and commit only that exact plan path on the feature branch. Do not commit it to master or remove the main copy.
6. Install from npm lockfile and services/api/requirements.lock using README commands. Before UI edits, read apps/web/AGENTS.md and the relevant installed Next.js guides under node_modules/next/dist/docs. Reuse the existing UI style and controls; do not introduce a design-system or framework rewrite.
7. Establish the disposable-database baseline in §8. Record actual results; the historical 336 API / 19 web / 3 browser gate is evidence about the baseline, not a new test run.
8. Create docs/superpowers/progress/2026-09-08-navigator-closed-loop.md in the feature worktree. Record branch/worktree, baseline SHA, exact disposable database names (no secrets), migration hashes, package status, RED/GREEN command and result, contract hashes, checkpoint commits, and the next exact step.

For each numbered work package: write a small test, run it and inspect a behavior-related failure, implement only that behavior, rerun, refactor, run the focused regression group, review the diff, update the ledger, and commit only explicit reviewed files. Repeat for each assertion group rather than implementing the entire package before testing. Missing dependencies, undefined fixtures, import typos, and unreachable services are setup failures, not accepted RED evidence.

On restart, read the ledger and current diff, verify the database name and migration head, then resume the first incomplete step. Do not rerun a destructive reset on an unknown target or discard incomplete code. Stop and request direction if implementation requires a breaking contract change, modification of migrations 0001–0005, new dependencies/services, altered approval semantics, or scope outside this plan. Routine file/function naming can be adjusted with an explained ledger entry; acceptance criteria cannot.

## 2. Why this milestone, and what the repository already has

This is the smallest useful *human-operated closed loop*, not completion of every Week 2 feature. It proves the operational contract before Week 3 orchestration/retrieval generates more work, before Week 4 evaluation measures end-to-end outcomes, and before deployment hardens the product. Those later phases depend on knowing what humans authorize, what actions actually happened, and what counts as closure. Normal testing, authorization checks, and failure tests are required now; “evaluation later” does not defer them.

| Capability | Implemented at the baseline | Missing slice |
|---|---|---|
| Prioritized queue | Tenant-scoped queue, effective need state, operational priority, owner/due fields | Refresh after commands and retain a closed case for history |
| Patient evidence | Immutable check-ins, correction chains, stored need evidence | Explicit source lineage and a typed selected-need projection |
| Change views | Case reads active submissions; seed includes a correction | Distinguish correction deltas from change between independent check-ins |
| Proposal review | Proposal/revision/policy/decision records, effective state, decision API, resource snapshots | Read and review an exact task proposal in the UI |
| Task ownership/due/state | Task columns and lifecycle guards; no claim/start/complete API or controls | Exact approved-proposal binding plus self-claim, start, complete |
| Resolution | Outcome preview and command; atomic closure/cancellation and audit | Navigator UI using those existing commands |
| Follow-up | No request/response domain or UI | One immutable request per newly completed bound task; one patient response |
| Timeline | Historical source records exist; no journey projection/UI | Typed navigator history and separately allowlisted patient history |

Important evidence and corrections to the original draft:

- services/api/app/domain/check_ins.py persists check-ins, but submission does not durably invoke NeedFactory or create proposals. This journey **starts at a seeded need and pending proposal**, not a newly submitted check-in. Automatic intake-to-queue is deferred.
- services/api/app/db/repositories.py reads active submissions for the current case. A superseding submission corrects a prior report; it is not a later clinical observation. Never label the seed's correction as deterioration or improvement over time.
- services/api/app/domain/approvals.py has application validators for task proposal schema v1; migration 0005 also supports resource-bearing v2 at the database layer. Read/review seeded v2, preserve v1, and do not silently expand proposal creation to v2.
- Approval is per proposal/revision chain, not “the latest task proposal.” Several roots can target one task. Also, approval does not apply a task's title or bind its execution to that proposal. The decision response's applied flag is not the task authorization signal.
- The decision API already validates a supplied qualifying role assignment against the authenticated actor. This is not inherently insecure. Preserve that compatibility while allowing the new UI to omit the role-assignment identifier.
- The seeded recurrence has inherited history and reason-shaped evidence, not a new source submission. Preserve it honestly; add a separate source-backed demonstrator rather than relabeling or rewriting history.
- Existing apps/web/e2e tests intercept the API. They are useful UI tests, but cannot prove browser-to-PostgreSQL integration. Add a separate live journey.

### Bounded story and exclusions

Navigator opens seeded transportation evidence → reviews a pending task proposal/resource snapshot → approves → claims with a future due time → starts → completes. Completion creates a follow-up request. The linked synthetic patient responds. The navigator reviews that evidence, previews closure, and records an Outcome. The queue removes the closed need while both audiences retain appropriate history.

Also test decline and closed-unresolved branches. A patient response never resolves a need automatically. A navigator may close without waiting for a response; the UI must make missing follow-up evidence clear, not impose a new prerequisite on the existing Outcome API.

Out of scope: automated need/proposal creation, model/provider calls, orchestration/jobs, retrieval/matching, revision UI, reassignment, due-date editing after claim, arbitrary task edits, new message delivery, notifications, recurrence creation, safety-review UI, EHR integrations, evaluation dashboards, deployment, and real patient data. Do not broaden this into generic workflow infrastructure or retrofit every historical task.

## 3. Acceptance criteria and ownership

| ID | Required observable result | Work packages |
|---|---|---|
| A1 | Source IDs/values and correction vs independent-check-in comparisons are truthful, deterministic, same-patient/episode, and non-clinical | 2, 5 |
| A2 | Exact task proposal/policy/decisions/resources are reviewable; approve/decline obey current authority, revocation and self-approval policy | 2, 5 |
| A3 | Claim binds exactly one approved proposal, materializes its approved title, sets authenticated owner and future aware due_at; unapproved/stale/wrong-target claims fail atomically | 1, 3, 5 |
| A4 | Only the assignee can start/complete a bound task; retries never duplicate or reverse work; tenant, ownership, revocation, closure and concurrency tests pass | 1, 3 |
| A5 | First completion writes its audit event and exactly one immutable follow-up request in the same database transaction | 1, 3 |
| A6 | Only the currently linked supporting_actor patient can answer their request once; matching retry succeeds, changed retry conflicts; no response closes a need | 1, 4 |
| A7 | Existing Outcome remains sole closure/cancellation authority; closure without response works; late first responses fail; exact prior retries remain safe | 3, 4, 5 |
| A8 | Timelines are stable, source-attributed and tenant-scoped; patient projection contains no internal notes, proposal rationale, raw audit payloads or unapproved copy | 4, 5 |
| A9 | Controls show pending/error/conflict/empty states, keyboard labels and timezone; successful commands refresh canonical data; closed selection survives queue removal | 5 |
| A10 | Migrations 0001–0005 retain accepted bytes; additive 0006 upgrades empty and populated databases without rewriting history; new invariants appear in the read-only auditor | 1, 6 |
| A11 | Synthetic-only warnings and shared PHI validation protect every newly exposed free-text write without scanning identifiers as content | 2, 4, 5 |
| A12 | OpenAPI/TypeScript regenerate reproducibly; all existing tests plus new API, concurrency, UI and live-browser journeys pass in safe disposable databases | 2–6 |

## 4. Fixed domain and persistence decisions

All new tables use UUID identity, organization scoping, aware UTC timestamps, explicit FK/constraint names, and the repository's metadata conventions. All new cross-entity references include the organization and, where needed, patient/need scope. A global UUID FK alone is insufficient. Add matching ORM metadata and migration constraints. Add only indexes supporting actual joins/list order; avoid a speculative indexing project.

### 4.1 Exact task authorization

Add nullable navigation_task.authorized_proposed_change_id. Add a suitable unique key to ProposedChange on (organization_id, navigation_task_id, id), then a composite FK from task (organization_id, id, authorized_proposed_change_id) to that key.

Claim requires the exact proposed_change_id from the UI. In the transaction, verify its effective state is approved, change type is authorize_navigation_task, target is this task, and schema is supported v1 or v2. Copy the validated approved title into task.title, set owner/due/status, and bind the FK atomically. For v2, use existing materialized proposal resource snapshots; do not rerun retrieval or copy resources from another root. Do not use proposed_at/MAX(id) as an authorization rule.

Unbound historical tasks remain valid and readable. They do not acquire executable approval by migration/backfill. The new claim command can bind an unbound open task; start/complete require a binding. Historical assigned/in-progress tasks without a binding are read-only in the new controls and explicitly labeled as such.

For a bound task, freeze its binding, title, owner and due_at after claim. Forbid deletion or insertion already bound; bound work enters via open → assigned and cannot be reassigned, retitled, rebound, or have due_at changed. Enforce transitions assigned → in_progress → completed, while preserving existing Outcome-triggered cancellation. completed_at is set on first completion using a server/database instant and cannot be rewritten on retries.

Extend guards in 0006 (do not edit old migration files). Validate active navigator authority for the task owner at first claim/start/complete; the API also derives actor identity exclusively from the signed session. Database relational guards do not replace authentication or make a shared SQL connection know the logged-in human. Preserve the existing closure-trigger exception exactly. Do not introduce a general cancellation command or duplicate its cancellation audit.

### 4.2 Follow-up records

Create app/db/models/follow_ups.py with these entities:

| Entity | Required fields and constraints |
|---|---|
| FollowUpRequest | id; organization_id; patient_id; care_episode_id; reported_need_id; navigation_task_id; requested_by_user_id; requested_at; prompt_version (initially 1). Unique (organization_id, navigation_task_id). Composite FK (org,patient,episode,need) to ReportedNeed; composite FK (org,patient,need,task) to NavigationTask; tenant identity key |
| FollowUpResponse | id; organization_id; follow_up_request_id; submitted_by_user_id; patient_identity_link_id; response; note nullable max 2000; submitted_at. Unique (organization_id, follow_up_request_id). Tenant-scoped FK to request; author/link relation validated against request.patient_id; tenant identity key |

Add the supporting unique task key (organization_id, patient_id, reported_need_id, id). Store prompt_version rather than arbitrary message text; render a fixed non-clinical question: “Did this navigation support address your reported need?” It is a workflow form, not newly approved PatientMessage delivery.

Response enum values: resolved, unresolved, still_needs_help. Reject unknown values/extra fields; trim optional note and normalize blank to null before validation, replay comparison and storage. Keep response wording as patient-reported evidence.

Completion's database AFTER transition trigger creates the request and user-attributed audit event atomically; services do not create a second copy. Use the repository's deterministic ID pattern for one task-transition event and one request per task. requested_at equals completed_at; requester equals task.assignee_user_id. Request creation must validate completed bound task, matching patient/need/episode/owner, timestamp and supported prompt_version. No requests for historical unbound tasks. An error rolls back task completion too.

The same new transition-audit mechanism records first claim and first start. Use event_type navigation_task_claimed, navigation_task_started and navigation_task_completed, entity_type navigation_task, entity_id task.id, actor_type user and actor_user_id task.assignee_user_id. Minimal typed payload: need_id, authorized_proposed_change_id, from_status, to_status, assignee_user_id and due_at; add follow_up_request_id only for completion. created_at is the database transition instant, not a client timestamp. Freeze these events with existing AuditEvent guards; replay emits none. Timeline adapters map these event types to the shorter kinds in §5.3. Keep the established cancellation event type unchanged.

Requests/responses are append-only under normal roles and protected by mutation-rejection triggers. Use the established SECURITY DEFINER/search_path/grant pattern where triggers need to write protected records. No public execute or broad grants; test with ojcc_app, not just the migration superuser. Reuse reject_append_only_mutation where appropriate.

Add a unique PatientIdentityLink key (organization_id, user_id, id) and the matching response FK (organization_id, submitted_by_user_id, patient_identity_link_id). Response insertion locks the parent need before its request; validates no Outcome, active link at submitted_at, matching request patient, active user and current supporting_actor authority. submitted_at must be a server instant at or after requested_at, not client input. The API selects link and author; clients cannot supply either or timestamps. Preserve referenced link organization/patient/user/linked_at against rekeying and reject backdated revocation that would invalidate accepted response attribution. A legitimate later revocation remains allowed and blocks subsequent writes. Do not rewrite unrelated identity history.

Request display status is derived: answered if a response exists; otherwise unavailable_need_closed if an Outcome exists; otherwise awaiting_response. Do not mutate the request when its need closes, and do not leave an unanswered closed request actionable.

### 4.3 Transactions, races, and replay

Reuse the existing service-flush / route-commit pattern; helpers never commit independently. Use UTC-aware server time; reject naive due dates. A future due time means strictly after server now on the initial claim, not on a replay.

Acquire the need row FOR UPDATE before task mutation to align with Outcome's existing need → ordered-task locks. Claim then locks its exact proposal and task; start/complete lock the task; response locks its request and identity link. Acquire any qualifying role lock last. Always recheck state/authority after waiting. Never start by UPDATEing the task and rely on its BEFORE trigger to establish lock order: the UPDATE already takes a task-row lock.

The existing approval path locks proposal then role and does not mutate tasks. Keep that path; do not add a task lock to approval and create a proposal/task cycle. Inspect and test actual database trigger interactions. Direct SQL using a conflicting lock order may be aborted by PostgreSQL, but must never commit inconsistent state. Map known deadlock/serialization failures to a retryable conflict after rollback; do not swallow unrelated DB errors as “stale.”

| Command | First success | Matching replay after authentication/tenant/owner checks | Conflict |
|---|---|---|---|
| Claim | open + unbound + exact approved proposal + future due → assigned | Same actor/proposal/normalized due and assigned, in_progress or completed: 200 with current task, replayed=true; no changes, even if due is now past or need later closed | Different owner/proposal/due; non-open unbound task; cancelled task; first claim on closed need |
| Start | assigned bound task → in_progress | Same owner, already in_progress or completed: 200, replayed=true; no writes | open/unbound/cancelled; wrong owner; first transition after closure |
| Complete | in_progress bound task → completed + audit + request | Same owner, completed: 200, same request ID and timestamps, replayed=true, including after Outcome | assigned/open/unbound/cancelled; wrong owner; first completion after closure |
| Follow-up response | awaiting_response → one response, 201 | Same authenticated linked author, same normalized response/note: 200, same ID/time, replayed=true, including after Outcome | Different content/author after first response; first response after Outcome |

Task transitions and a response are one-shot operations, so use the natural identity described above instead of a new generic idempotency ledger. After revocation, a previous response does not grant access: normal current authentication/link checks still apply. For Outcome keep its existing Idempotency-Key contract. For approval keep existing duplicate-decision/conflict behavior; the UI refetches to resolve an uncertain response rather than blindly resubmitting.

Concurrent complete/complete commits one request/event. Complete/Outcome either completes first (request may then be unavailable) or Outcome cancels first (complete conflicts). Response/Outcome either records evidence before closure or rejects the first late response. No reopening or fabricated successful response. Test both orderings with independent connections and barriers, not arbitrary sleeps.

### 4.4 Restore integrity and migration safety

Add 0006_navigator_closed_loop.py, down_revision=0005_workflow_knowledge_audit. Do not revise hashes or contents of accepted migrations 0001–0005. Do not rewrite historical proposals, tasks, submissions, decisions, resources or Outcomes to fit the new slice. Nullable binding is intentional compatibility, not missing data to infer.

Verify: empty→head; representative populated 0005→head with legacy unbound task and approved v2 resources preserved; seed twice at head; Alembic current/check; new grants/constraints/guards present. Preserve existing older-upgrade/refusal tests. Do not run head-aware new seed code against 0005; use the baseline-compatible fixture before upgrade.

0006 downgrade must refuse with an actionable message if bound tasks or follow-up records exist; an empty extension can downgrade by removing only 0006 objects and restoring any functions it replaced to exact 0005 behavior. Never silently drop new history.

Extend app/db/integrity.py to find invalid bound proposal/target/title/state, inconsistent bound lifecycle, missing/duplicate/mismatched completion requests, bad response attribution/time, request/response scope violations, and missing/duplicate/inconsistent new transition audit records. An unanswered request after closure is valid. Legacy unbound tasks need no synthetic audit/request. The audit is read-only and emits identifiers plus minimal diagnostic evidence, never “repairs.”

Corruption fixtures may bypass triggers or temporarily remove an exact constraint only inside disposable test databases. Restore enforcement and rollback/tear down in finally. Normal application fixtures must not bypass guards.

## 5. API, evidence and timeline contracts

### 5.1 Fixed routes and schemas

All new writes use Pydantic extra=forbid. Return typed OpenAPI models, not dict[str,Any] placeholders for new projections. A JSON answer value may be a recursive JSON type; model its containing evidence structure explicitly. All repository entry points require organization_id; patient reads also require the actor's resolved patient_id. Do not accept organization_id, owner, author, role, or timestamps from new command bodies.

| Route | Request / response |
|---|---|
| GET /v1/navigator/needs/{need_id}/workspace | New NavigatorNeedWorkspaceRead: selected need including effective state, patient/episode IDs, evidence, comparisons, tasks, task proposals/policy/decisions/resources, follow-ups, Outcome nullable, navigator timeline |
| POST /v1/navigator/proposed-changes/{id}/decisions | Existing route/response; make qualifying_role_assignment_id optional. When omitted, resolve an active matching assignment for this actor/org/required role; deterministic ordering granted_at desc, id asc. When supplied, preserve full current validation. Lock/recheck; use existing record_decision and canonical state |
| POST /v1/navigator/tasks/{task_id}/claim | {proposed_change_id: UUID, due_at: aware datetime}; TaskCommandRead |
| POST /v1/navigator/tasks/{task_id}/start | Empty object; TaskCommandRead |
| POST /v1/navigator/tasks/{task_id}/complete | Empty object; TaskCommandRead, including generated follow_up_request_id |
| GET /v1/patient/follow-ups | PatientFollowUpListRead {items}; only current linked patient's requests, newest requested_at/id first |
| POST /v1/patient/follow-ups/{request_id}/responses | {response: enum, note?: string}; FollowUpResponseRead plus replayed |
| GET /v1/patient/journey-timeline | PatientTimelineRead {events}; actor's own patient only |
| Existing GET outcome-preview and POST outcomes | Keep paths, status codes, cancellation semantics and Idempotency-Key; expose through UI |

TaskCommandRead includes task_id, need_id, status, assignee_user_id, due_at, authorized_proposed_change_id, completed_at, follow_up_request_id nullable, replayed. 200 for successful task commands including first transition.

New errors use a documented detail object with code and message. 401/403 retain auth semantics; missing or cross-tenant/cross-patient identifiers return indistinguishable 404. Wrong assignee with otherwise permitted navigator visibility returns 403. Malformed fields return 422. Business conflicts return 409 with one of: need_closed, task_state_conflict, task_claim_mismatch, task_unbound, proposal_not_approved, follow_up_already_answered, concurrent_change. Do not expose foreign IDs or database error text. Existing approval/Outcome error payloads need not be broken to match; the web client handles both old and new forms.

### 5.2 Evidence and comparison rules

The new workspace is need-scoped and remains readable after closure. Keep current case/queue paths compatible; use the new workspace for selected-need actions/history rather than overloading every patient case with unbounded nested data.

Evidence entries include source_submission_id nullable, field/question identifier, exact typed value where present, display text, and provenance kind (source_submission, inherited_history, stored_need_evidence). A source ID must actually identify the submission used for that evidence. Preserve both current stored evidence shapes; do not coerce reason into a nonexistent question_id. For a recurrence, trace reopened_from_need_id for inherited context but label it inherited; do not claim it came from a fresh submission.

Return two separate comparison sections:

- correction: predecessor→successor inside one immutable correction chain, labeled “Correction to the same check-in.” Retain IDs/timestamps and supersession lineage.
- between_check_ins: select the two most recent independent submission roots in the same patient/episode by root submitted_at then root id; use each root's active leaf as its corrected answer content. A later correction neither creates a root nor moves that root later in the observation order. Compare only if those two leaves have the same questionnaire identity/version.

Each section has status available, insufficient_history, or not_comparable, plus current/previous IDs when applicable and typed field deltas. Resolve questionnaire identity/version through each CheckInDefinition; do not assume CheckInSubmission has a questionnaire_version column. Different questionnaires/versions are not_comparable; do not silently skip a newer incompatible record to invent a trend. Distinguish absent from explicit null with previous_present/current_present. Compare exact normalized JSON values without converting everything to strings. No inference such as “clinical improvement.” Tests must include independent roots, correction-only seed, a late correction to an older root, missing values, same-time ordering, foreign episode/patient, and incompatible versions.

### 5.3 Audience-safe timeline

Create typed discriminated event models, not raw AuditEvent serialization. Common envelope: event_id (kind plus source UUID), kind, occurred_at, source_type/source_id, need_id nullable, task_id nullable, controlled summary, and a kind-specific typed detail. Submission events can legitimately lack need_id. Stable oldest-first order is (occurred_at, causal_rank, source_id).

Define ranks explicitly: check_in_submitted=10, check_in_corrected=20, need_reported=30, proposal_created=40, proposal_decided=50, task_claimed=60, task_started=70, task_completed=80, follow_up_requested=90, follow_up_responded=100, outcome_recorded=110, task_cancelled=120. Preserve actual timestamps; ranks resolve ties only. Order Outcome before its same-instant trigger-authored cancellations.

Navigator workspace history includes selected need/predecessor source lineage, relevant exact proposal decisions, new transition audits, follow-up and Outcome, including actual cancellations that the routine case task list hides. Mark inherited records; do not mix unrelated patient needs. Bounded demo results may be returned as complete lists; do not silently truncate or invent pagination.

Patient history is a separate server-side allowlist. Include own check-ins/corrections, controlled practical-need labels, controlled task-progress labels for bound authorized work, follow-up prompt/own response, and controlled Outcome labels (“Navigation support closed as resolved” or “Navigation support closed without resolution”). Do not expose pending/declined proposals, raw task titles, proposal rationale, staff IDs/role-assignment IDs, internal Outcome notes, agent payloads, audit payloads, or hidden reasoning.

Task approval is not patient-message authorization. In this slice, resource snapshots are navigator-only; the patient receives controlled workflow labels, not arbitrary approved task copy or resource metadata. Do not claim a ride was booked or delivered merely because a task completed. No new resource-release/message-delivery policy is implicit here.

### 5.4 Synthetic-data and text safeguards

Reuse PUBLIC_DEMO_PHI_WARNING and the existing email/phone/MRN content check from domain/check_ins.py. Extract a small shared domain/public_demo.py helper with regression coverage; preserve current check-in import/behavior compatibility.

Apply it to new response.note and newly UI-exposed approval reason/Outcome note. Do not scan UUIDs, role identifiers, idempotency keys, or timestamp fields as prose (the phone pattern can false-positive on them). Keep existing max lengths: follow-up note 2000, approval reason/Outcome note 4000. Decline needs a nonblank reason. Do not expand this into a clinical classifier or promise it detects all PHI.

Warnings appear beside new free-text controls. Fixtures contain invented synthetic narratives and reserved/example URLs only. No copied clinical records, real phone numbers, API keys, external notifications, or screenshots/logs containing real data. Never log free-text request bodies or SQL bind values; use minimal error messages and synthetic test artifacts. The regex is a demo guardrail, not authorization to use real health data.

## 6. Work packages: explicit RED → GREEN sequence

Paths below are repository-relative. “Create” means the file does not exist at the baseline. Do not assume examples in this plan name pre-existing fixtures or functions.

### Package 1 — Persistence and invariant tests (A3–A7, A10)

Modify:

- services/api/app/db/models/needs.py, approvals.py, identity.py, __init__.py
- services/api/app/db/integrity.py
- services/api/tests/test_core_domain_metadata.py and test_model_package.py

Create:

- services/api/app/db/models/follow_ups.py
- services/api/alembic/versions/0006_navigator_closed_loop.py
- services/api/tests/closed_loop/conftest.py
- services/api/tests/closed_loop/test_persistence.py
- services/api/tests/closed_loop/test_migration.py
- services/api/tests/closed_loop/test_integrity.py

Steps:

1. Build a local test fixture contract first. Existing tests have module-local session/client helpers, not a universal db_session or client. Read tests/test_outcomes.py, test_approvals.py, test_core_domain_migration.py and integration/test_need_task_lifecycle.py before adapting their patterns.
2. In closed_loop/conftest.py define closed_loop_session (rollback-safe Session), closed_loop_case (typed IDs for org/patient/episode/navigator/role/link/submission/need/open task/pending v1 proposal), approved_closed_loop_case, and closed_loop_client (httpx.AsyncClient + ASGITransport through asyncio.run, following existing tests). Fixture setup includes valid pathway/definition/submission lineage and patient role/link; do not bypass their guards. A different qualified navigator proposes the fixture action when self-approval is forbidden. Keep these multiple-actor test tenants separate from the seeded demo tenant; wrong-owner tests still need two navigators in the SAME test tenant. Define separate independent-connection fixtures for committed concurrency data; do not reuse the rollback-only fixture for races.
3. Write reflection/SQL persistence tests that collect without importing absent new models, then observe missing binding/table/guard behavior. Add minimal model/migration code to pass each group.
4. Test valid chain; invalid foreign org/patient/need/task combinations; initial bound insert; unapproved binding; changed binding/title/owner/due; skipped/reversed transitions; premature/duplicate requests; response on closed need; request/response mutation; identity rekey/backdated revocation; permitted later revocation. Exercise ojcc_app privileges and normal trigger enforcement.
5. Add rollback tests proving a request/audit failure leaves task in_progress. Add the empty/populated upgrade and guarded downgrade matrix from §4.4.
6. Write corruption/auditor tests before adding each new audit query. Test zero violations for valid history and legacy unbound history. Keep reports content-minimal.

Focused command from repository root:

~~~powershell
python -m pytest services/api/tests/closed_loop/test_persistence.py services/api/tests/closed_loop/test_migration.py services/api/tests/closed_loop/test_integrity.py services/api/tests/test_core_domain_metadata.py services/api/tests/test_model_package.py -q
~~~

Checkpoint: review SQL/ORM parity and actual lock order; migration hashes unchanged; commit explicit files with message “feat: persist approved task execution and follow-up history”.

### Package 2 — Navigator reads and compatible review (A1, A2, A11, A12)

Create:

- services/api/app/domain/navigator_workspace.py
- services/api/app/api/navigator_workspace.py
- services/api/app/domain/public_demo.py
- services/api/tests/closed_loop/test_workspace.py
- services/api/tests/closed_loop/test_review.py

Modify repositories.py, domain/approvals.py, api/proposed_changes.py, domain/check_ins.py, app/main.py; generated contracts/openapi.json and apps/web/lib/api-types.ts.

Steps:

1. RED: exact source lineage and typed workspace route absent; correction-only data must report insufficient independent history. Add comparison logic/read models and tenant-scoped repository reads.
2. RED: omitted role assignment on decision rejected. Implement optional server resolution without weakening supplied-ID validation. Test supplied valid/foreign/wrong-user/revoked IDs, no eligible role, multiple eligible historical intervals, self-approval rejection, approved/declined/superseded state, stale decision and compatibility of existing response fields.
3. Test v1 and seeded v2 projection, wrong schema handling, several proposal roots on one task, and exact resource ownership. UI must choose an explicit proposal; no “latest approved” shortcut. Unsupported schema is visible as unsupported and non-actionable.
4. Extract/test PHI helper and apply to approval reason; existing check-in tests must still pass unchanged in meaning.
5. Regenerate API/TypeScript contracts using §8 commands; inspect schema names, nullability, errors and route types. Keep all existing approval tests green.

Run new test_workspace.py and test_review.py plus existing test_approvals.py, test_check_ins.py and test_navigator_queue.py. Checkpoint commit: “feat: expose evidence and governed task proposal review”.

### Package 3 — Task command services and concurrency (A3–A5, A7)

Create:

- services/api/app/domain/navigation_tasks.py
- services/api/app/api/navigation_tasks.py
- services/api/tests/closed_loop/test_task_commands.py
- services/api/tests/closed_loop/test_task_concurrency.py

Modify app/main.py and generated contracts.

Steps:

1. RED: POST claim route missing for a valid approved fixture. Implement typed claim with need-first locking, exact proposal binding and server owner.
2. Add failing tests for every claim rejection/replay row in §4.3; then implement each rule. Include approved title differing from old task title, multiple roots, v1/v2 and superseded proposal.
3. RED: start then complete commands unavailable. Implement owner-only transitions; verify returned request/audit were authored by the database trigger, not inserted again by the service.
4. Test no partial writes after validation/constraint errors; revoked role; foreign task; completed task replay after Outcome; due date becoming past between retries; changed claim tuple conflicts.
5. Add real two-session barrier tests for competing claims, complete/complete, complete/Outcome and start/Outcome. Assert final database rows/counts, not just status codes. Recheck under both transaction orderings and bounded lock timeout so failures do not hang indefinitely.
6. Regenerate contracts; keep existing test_outcomes.py and integration/test_need_task_lifecycle.py green, including the sole existing cancellation audit.

Checkpoint commit: “feat: operate approved navigator tasks safely”.

### Package 4 — Patient response, timelines and resolution safety (A6–A8, A11)

Create:

- services/api/app/domain/follow_ups.py, journey_timeline.py
- services/api/app/api/patient_follow_ups.py, patient_journey.py
- services/api/tests/closed_loop/test_follow_ups.py, test_timeline.py, test_follow_up_concurrency.py

Modify repositories.py, api/navigator_workspace.py, api/navigator_outcomes.py, app/main.py and generated contracts.

Steps:

1. RED: patient request list and response routes absent. Implement signed supporting_actor identity/link resolution, response normalization, one-shot replay and derived status.
2. Test unlinked/revoked actor, role mismatch, foreign patient/org, body-injected author/link/time, repeated same content, changed content, null/blank note normalization and all three response values. Assert no Outcome/task/need mutation from a response.
3. RED: typed timeline endpoints/projections absent. Implement explicit navigator vs patient projection; test every allowed kind and forbidden field/copy with sentinel strings in internal task title/rationale/Outcome note/audit payload. No “serialize then hide with CSS.”
4. Test stable equal-timestamp ordering, inherited source labels, closed need selection, actual cancellation events, and no unrelated need leakage.
5. Add response/Outcome concurrency and exact replay-after-closure tests; verify unanswered request becomes unavailable, while resolved/unresolved evidence never blocks a human Outcome. Add PHI validation to new note and newly surfaced Outcome note with existing Outcome contract regression coverage.
6. Include real signed-cookie auth tests modeled on test_auth.py, with fresh sessions rechecking revocation. Dependency-overridden unit tests alone are insufficient authorization evidence.

Run the three new modules plus test_auth.py, test_outcomes.py, test_check_ins.py and tenant-isolation integration tests. Checkpoint commit: “feat: capture patient follow-up and audience-safe journey history”.

### Package 5 — Seeded UI and actual browser-to-database journey (A1–A9, A11, A12)

Create:

- apps/web/components/navigator/need-workspace.tsx
- apps/web/components/patient/follow-up-panel.tsx
- apps/web/components/journey-timeline.tsx (typed display data only; no client filtering of privileged payload)
- apps/web/tests/closed-loop.test.tsx (Vitest includes tests/**/*.test.tsx; confirm config before writing)
- apps/web/playwright.live.config.ts
- apps/web/e2e-live/closed-loop-transportation.spec.ts
- scripts/verify_live_journey.ps1

Modify:

- apps/web/app/demo/navigator/page.tsx and demo/patient/page.tsx
- apps/web/lib/api-client.ts; existing patient/check-in-flow.tsx only if needed to keep one main landmark
- apps/web/package.json; services/api/scripts/seed_demo.py
- services/api/tests/test_demo_seed.py and integration/test_seeded_application_contracts.py

Steps:

1. RED seed assertions: add a distinct source-backed synthetic transportation need, open task, pending v2 task proposal and resource snapshot using stable DEMO_IDS. Do not retarget the old approved task or rewrite the recurrence's evidence. Preserve every existing safety/workflow/approval history. Keep one demo navigator and one linked demo patient: bootstrap currently uses one_or_none. Additional-actor tests belong in separate fixtures/tenants.
2. Implement only the new story. The legacy seed section uses replica mode; insert the new valid story after trigger enforcement is restored, within the same transaction, then audit. Test double seeding before and after exercising the story: it must not restore pending status, erase responses, duplicate history or reopen a closed need. Extend seed summary coverage to new tables; existing stable data remains stable.
3. Before UI production edits, write Vitest tests using installed render/screen/fireEvent and vi. Do not reference undefined user.click or add @testing-library/user-event casually. RED should be a missing control/behavior, not a missing helper. Mock API types from generated contracts.
4. In the same RED stage, build the live harness/config and write the complete live browser scenario while controls are missing. Verify it reaches seeded data and fails at the first missing control; a network/config error is not RED.
5. Implement navigator evidence/comparisons, explicit proposal selection, resource/policy review, approve/decline, claim due input, start, complete, follow-up evidence, Outcome preview/confirm and history. Use canonical proposal_state, not decision.applied. Do not hide closure when no response exists.
6. Implement patient follow-up panel and safe timeline alongside existing check-in. Convert local datetime input explicitly to an aware ISO instant, display timezone, preserve entered due value across uncertain retries, and do not claim overdue means clinical danger.
7. Keep selection as need/patient IDs independently of queue membership. After closure remove the queue item but display the selected closed workspace and confirmation. Refetch canonical workspace/queue after success or 409; reject stale fetches using AbortController/request identity. On 401/403 show role/session recovery, not a silent role switch.
8. Disable repeat submission while pending, show inline validation/status with accessible labels and focus, require decline reason, preview actual current cancellation candidates, and preserve one Outcome idempotency key for a retry of the same payload. Generate a new key only for a new intentional command. A preview is informational, not a guaranteed snapshot; display actual cancelled_task_ids returned by Outcome.
9. Add UI failure tests: invalid due/naive API dates, unsupported/no-approved proposal, non-owner/read-only historical task, double-click, slow response, 409 refetch, revoked session, blank/PHI note, already answered/closed request, empty queue with retained history, keyboard flow and narrow viewport.

Live harness contract (not mocked integration):

- Add npm script test:e2e:live using playwright.live.config.ts; retain the existing three mocked tests and default config.
- The PowerShell wrapper takes mandatory DatabaseUrl and ConfirmDatabaseName, validates the same explicit loopback disposable-name policy **before any connection**, isolates/restores all inherited PG* values and task env in finally, then resets/seeds only that named database using the existing reset script.
- Use a database separate from the API test database; reset only when no process is using this owned live database. Do not point at ojcc or silently reuse DATABASE_URL defaults.
- Configure two owned webServer entries: uvicorn app.main:app with cwd services/api on 127.0.0.1:8011; Next dev from apps/web on 127.0.0.1:3011, OJCC_API_ORIGIN=http://127.0.0.1:8011. API gets validated DATABASE_URL, APP_ENV=local, a test-only session secret and seeded DEMO_ORGANIZATION_ID. No reload; reuseExistingServer=false for both. Busy ports are a clear failure, not permission to kill unknown processes.
- Use actual same-origin Next rewrites and signed demo cookies. No page.route interception or mocked fetches in e2e-live. Use independent browser contexts for patient and navigator because role bootstrap replaces the session cookie. Refresh the navigator workspace explicitly after the patient response.
- One live scenario performs approve→claim→start→complete→patient response→navigator Outcome, checks queue removal and safe retained history, reloads both views and confirms persistence. Test database assertions verify one request/response/Outcome, exact binding and audit counts. A separate API journey covers decline, closed_unresolved and early closure.
- Run desktop and mobile configurations sequentially with a fresh reset before each, not two mutating projects concurrently against the same seed. Only the wrapper owns those resets. Browser shutdown precedes the next reset.
- Live execution is required by the final gate and CI, not an optional skip. It may be a separate verifier step so API tests never share its mutable database.

Run new UI tests, all existing UI tests, mocked Playwright and both live viewport runs. Checkpoint commit: “feat: deliver the synthetic navigator closed-loop journey”.

### Package 6 — Reproducible verification and handoff (A10–A12)

Modify scripts/verify.ps1, .github/workflows/ci.yml, README.md, and the new progress ledger. Update product-design.md only with a bounded delivered-scope note if needed; do not rewrite Week 3/4 as accomplished.

1. Write failing harness safety tests for refused persistent/remote/query-override targets, inherited PG* isolation/restoration, nonzero child exit, and busy ports before extending harness behavior. Reuse test_demo_seed.py's no-connection testing patterns where applicable.
2. Add mandatory LiveDatabaseUrl and LiveConfirmDatabaseName parameters to scripts/verify.ps1 and pass them to verify_live_journey.ps1 as DatabaseUrl and ConfirmDatabaseName. Local verification must fail fast when target configuration is missing, equal to the API target, or unsafe, not silently skip live tests. Do not reset the API test database from the live stage. Verify the API DATABASE_URL is explicit and disposable before importing application code or running tests.
3. Fix CI configuration for this path: its current DATABASE_URL and service POSTGRES_DB are ojcc. Keep the service/admin connection if useful, but create explicitly named disposable API/live databases before migrations/tests and set their URLs explicitly. Never weaken reset safeguards to accommodate CI's old ojcc name. .github/workflows/ci.yml is the existing file; do not create a fictional verify.yml.
4. Run all §8 gates in order, inspect output and record evidence. Resolve new warnings/failures rather than lowering thresholds or deleting tests.
5. Self-review changed files against every acceptance row, especially SQL guard/grant parity, authority/replay, timeline leakage and live-test authenticity. Request independent review only if the user authorizes a reviewer; otherwise explicitly report the review as self-review, not independent.
6. Commit explicit reviewed files, record remaining limitations, and stop for the user's integration decision. No automatic deployment, merge, branch cleanup, or next milestone.

## 7. Minimum test inventory (do not substitute happy-path coverage)

Every row requires an executable test and ledger reference; a test name alone is not evidence.

| Area | Required negative / boundary cases |
|---|---|
| Evidence | Correction is not independent observation; recurrence has no fabricated source; version mismatch; absent vs null; foreign patient/episode; timestamp ties |
| Approval | Optional and explicit role IDs; revocation while waiting; wrong target; pending/declined/superseded; exact v1/v2; multiple roots; self-approval; old clients compatible |
| Task | Claim exact title/binding; different owner/tuple; past/naive due; skipped transition; unbound historical task; post-binding field mutation; same retry after time/closure; cancellation remains authoritative |
| Follow-up | Completed bound task only; exactly one request; DB rollback atomicity; immutable response; matching vs changed retry; link rekey/revoke; no automatic Outcome |
| Races | Claim/claim; complete/complete; start/Outcome; complete/Outcome; response/Outcome, both orderings and final row counts |
| Tenant/auth | Foreign org/patient IDs; same-tenant wrong owner; signed-cookie expiry/revocation; injected identity fields; ojcc_app direct writes/denied mutations |
| Timeline | No privileged sentinel values in patient JSON; stable causal ties; actual cancellations; closed history persists; patient wording not clinical/booking claims |
| Operations | 0005 populated upgrade; legacy bytes; guarded downgrade; audit corruption categories; seed twice including post-journey; fail-closed DB target; generated-contract stability |
| UI/live | Loading/error/conflict/empty/keyboard/mobile; one main; timezone; no duplicate click; real API/cookies/DB; refresh persistence; no mocked live network |

## 8. Environment and verification runbook

### Safety and baseline

From the main checkout, Docker commands must address the existing project:

~~~powershell
docker compose -p feature-oncology-command-center -f docker-compose.yml ps
~~~

Do not start another Compose project from the feature worktree; do not stop/remove the existing project or its volume. Persistent ojcc must never be reset, dropped or reseeded.

New reset/seed targets must match the repository validator: ojcc_demo_<8–32 lowercase hex> or ojcc_task7_<8–32 lowercase hex>, explicit loopback host and port 5432, PostgreSQL driver, no query parameters. A prefix is a safety allowlist, not a continuation of the old Task 7. Existing historical migration tests own separate ojcc_migration_test_* databases with their own validator; leave those safeguards intact and never feed their names to reset_demo.ps1.

For this milestone, prefer two new UUID-suffixed ojcc_demo_ databases, one API and one live. Generate a name, validate the full name, and create only that exact database using the existing Compose service. Example creation from the main checkout:

~~~powershell
$closedLoopDbName = "ojcc_demo_" + [guid]::NewGuid().ToString("N")
if ($closedLoopDbName -cnotmatch '^ojcc_demo_[0-9a-f]{32}$') { throw "Unsafe database name" }
docker compose -p feature-oncology-command-center -f docker-compose.yml exec -T db createdb -U ojcc $closedLoopDbName
if ($LASTEXITCODE -ne 0) { throw "Database creation failed; do not reuse or reset an existing name" }
~~~

Repeat creation with a separate variable for the live database. Record exact names. Assign the first created name to closedLoopApiDbName and the second to closedLoopLiveDbName; construct closedLoopApiUrl and closedLoopLiveUrl from those names and the known local synthetic credentials in docker-compose.yml. Set env:DATABASE_URL to closedLoopApiUrl. Do not log secrets or substitute an inherited production URL. Set DATABASE_URL before Python imports app.config (which otherwise defaults to ojcc). Reset creates schema, not the database itself.

Keep all environment overrides task-scoped, snapshot/restore them in finally, and isolate inherited PG* variables before connecting as the existing reset/seed scripts do. Native-command failures require checking LASTEXITCODE; PowerShell ErrorActionPreference alone does not make them fail fast.

If Git reports dubious ownership, use invocation-scoped safe.directory for this exact resolved repo/worktree only; never safe.directory=* or blanket global changes. Use apply_patch for edits, scoped Git staging, and read-only status/diff checks. Never reset --hard, checkout --, or broadly remove node_modules/worktrees to fix an unrelated issue.

### Commands and order

All commands below run from the feature-worktree root unless stated. DATABASE_URL must already be the explicitly created API disposable target.

~~~powershell
./scripts/reset_demo.ps1 -DatabaseUrl $env:DATABASE_URL -ConfirmDatabaseName $closedLoopApiDbName
if ($LASTEXITCODE -ne 0) { throw "Disposable reset failed" }
python -m alembic -c services/api/alembic.ini current
if ($LASTEXITCODE -ne 0) { throw "Alembic current failed" }
python -m alembic -c services/api/alembic.ini check
if ($LASTEXITCODE -ne 0) { throw "Schema drift detected" }
python scripts/export_openapi.py
if ($LASTEXITCODE -ne 0) { throw "OpenAPI export failed" }
npx --no-install openapi-typescript contracts/openapi.json -o apps/web/lib/api-types.ts
if ($LASTEXITCODE -ne 0) { throw "Type generation failed" }
~~~

Capture SHA-256 for contracts/openapi.json and apps/web/lib/api-types.ts, run both generators again with exit checks, and compare both hashes for equality. Inspect generated diffs for unintended removal/widening of old routes. Never hand-edit api-types.ts to make compilation pass.

Before Package 6 exists, run the baseline verifier without new parameters and record its existing-suite results; do not claim it covers the future live gate. After Package 6, run:

~~~powershell
./scripts/verify.ps1 -LiveDatabaseUrl $closedLoopLiveUrl -LiveConfirmDatabaseName $closedLoopLiveDbName
if ($LASTEXITCODE -ne 0) { throw "Required verification failed" }
~~~

The verifier retains locked installation, repository-wide Ruff, Pyright, read-only integrity, all pytest, web lint, Vitest, build and existing Playwright. It adds required live verification without weakening existing checks. The live wrapper runs the two viewport projects separately, resetting its own database between them, then checks integrity before returning success.

The final gate must demonstrate:

1. Immutable migration snapshot tests and direct SHA-256 comparison to the accepted 0001–0005 baseline; LF rule retained.
2. Empty and populated 0005 upgrade matrix, Alembic head and no metadata drift; guarded downgrade tests.
3. Two idempotent seed runs, post-journey reseed preservation, and read-only integrity with zero violations on valid data.
4. Full API suite (baseline 336 tests plus additions), Ruff and Pyright across all services/api; no accepted new warnings.
5. Web lint, all unit tests (baseline 19 plus additions), production build, all three existing mocked Playwright journeys.
6. New real API closed-loop journey and live browser-to-PostgreSQL journey at desktop and mobile, each on fresh owned data, plus post-journey integrity.
7. Byte-stable generated contracts, git diff --check, explicit changed-file review, and a clean feature branch after intended commits.

Record commands, exit codes, counts and key assertions; do not replace evidence with “should pass.” A failed required live test is not a pass because mocked tests succeed.

Cleanup only exact disposable databases created and recorded by this run after owned test servers/connections stop. Revalidate each name and connection target before dropping; never use wildcard enumeration, persistent ojcc, or force-terminate unknown connections. If ownership is uncertain, leave the database and report it. Confirm persistent ojcc is still present; report cleanup accurately.

## 9. Approval and implementation handoff

Plan review changes: exact proposal binding replaces ambiguous “current proposal”; correction/longitudinal views are separated; old role-ID API compatibility is retained; task/response retries and closure races are specified; patient copy is explicitly allowlisted; incomplete history is not fabricated; real live tests supplement mocks; setup/fixtures/CI/reset/runbook are explicit.

The PostgreSQL guidance influenced tenant-key indexes and consistent need-first locking. It does not introduce Supabase or a new database service.

Known limits after delivery: seeded entry point only; no automated check-in→need/proposal flow; no proposal revision/reassignment, external outreach or transport booking; historical unbound tasks are not retrospectively executable; patient timeline uses controlled labels rather than arbitrary task/resource copy; real-data operation and production deployment remain out of scope.

Suggested instruction to give the coding model **only when approving**:

> I approve the reviewed 2026-09-09 version of docs/superpowers/plans/2026-09-08-navigator-closed-loop-implementation.md. Execute that plan sequentially in a fresh feature worktree, starting with its approval/baseline checks and progress ledger. Use failing-first tests and the specified database safeguards. Preserve master and migrations 0001–0005; never reset/drop/reseed ojcc. Stop for a material scope or contract conflict. Do not merge, deploy, or begin the next milestone. Do not resume the completed or superseded reconciliation tasks.

Completion means every acceptance row has fresh evidence, all verification gates pass, and no unresolved important self-review finding remains. Report whether independent review was actually performed. Hand back the verified feature branch and documented limitations for the user's integration decision.
