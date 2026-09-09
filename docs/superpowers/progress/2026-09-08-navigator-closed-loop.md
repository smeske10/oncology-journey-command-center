# Navigator closed loop implementation ledger

Plan: `docs/superpowers/plans/2026-09-08-navigator-closed-loop-implementation.md`

## Approval and preflight

- Approval: explicit user approval received in the 2026-09-09 execution instruction.
- Main checkout baseline: `master` and `origin/master` both `5f12cd613d6aea0f75caec963d4f83a49ce73379`.
- Main checkout status: only the expected untracked approved plan; no overlapping edits.
- Worktree: `C:\Users\smesk\Claude Cowork\Projects\Personal Brand\Job Search — AI Product Roles\oncology-journey-command-center\.worktrees\navigator-closed-loop`.
- Branch: `feature/navigator-closed-loop`.
- Starting baseline: `5f12cd613d6aea0f75caec963d4f83a49ce73379`.
- Preserved-plan commit: `67af3c6` (`docs: preserve approved navigator closed-loop plan`).
- Approved-plan SHA-256 in main checkout and feature worktree: `B0EF547422A16CA010197C899971861A93137BAD57EB649A08B56C9EB25D3319`.
- Existing Docker project: `feature-oncology-command-center`; PostgreSQL 16 service healthy on `127.0.0.1:5432`.
- Persistent database safety: `ojcc` is out of scope and must never be reset, dropped, or reseeded.
- Historical scope: reconciliation is complete; completed/superseded reconciliation tasks will not be resumed.
- Delegation: not authorized by the plan approval; implementation and review will be performed in this session without subagents.
- Skill catalog note: the configured `C:\Users\smesk\Codex Cowork\Codex Skills\` catalog path is absent on this host. Applicable installed Superpowers execution, worktree, TDD, and verification skills are being used as the fallback.

## Immutable migration baseline

- `0001_core_domain.py`: `A177B32040C760E52FFD64872F61104F2064968AA6981295C54728E518CB6391`
- `0002_identity_pathway_submission.py`: `6FDF3C15FDF51CB9C3729F7F6D0458B51C88EEEE8119A10A1084A425F78F5648`
- `0003_need_task_outcome_lifecycle.py`: `25641A9831BF6CCE4198CC60636D60A03058ECC179752D32328C7513BCB6B556`
- `0004_safety_approval_lifecycle.py`: `301EAE2BE84C8685B14B0335525FA011CC88015F654CEF95212327CF4CEE6704`
- `0005_workflow_knowledge_audit.py`: `C81F81976DD82FDE31EB70B1A271205938B9DC1ECAE34E84E5780D09C3FDD5CC`

## Environment and package baseline

- Python: `3.12.8`.
- Node: `20.17.0`.
- npm: `10.9.0`.
- Package install: complete from `package-lock.json` and `services/api/requirements.lock`; editable API install complete; Chromium install complete. Future web commands use bundled Node `24.19.0` because default Node `20.17.0` satisfies the root contract but produced an engine warning for one transitive lint package.
- API disposable database: `ojcc_demo_b0d9d75509c74632bf599458d7cde301` (created explicitly in the existing Compose PostgreSQL service; never used before this run).
- Live disposable database: `ojcc_demo_809e0d91920742a4acf2e26eee1ab341` (created explicitly and distinct from the API target; never used before this run).
- Baseline OpenAPI SHA-256: `2EEC25C621607570D020B3F2EC7E06ED0FBDB80F4D1A6752654BE8A9795D1428`.
- Baseline TypeScript contract SHA-256: `A2D9CE75FF43B123049FBB876B44145CE053BCC3D847934F0F45202E04F751F9` (worktree checkout bytes; reproducibility gate pending).
- Generated OpenAPI SHA-256 after both baseline passes: `2EEC25C621607570D020B3F2EC7E06ED0FBDB80F4D1A6752654BE8A9795D1428`.
- Generated TypeScript SHA-256 after both baseline passes: `192BD4F40AE2541D0B2149455CE3070C523F7E03AAA6D60CE5893939E9CDCDD0`.
- Disposable reset/seed: exit 0; two identical seed summaries; integrity `violation_count: 0`.
- Alembic baseline: current `0005_workflow_knowledge_audit (head)`; check reports `No new upgrade operations detected.`
- Existing verifier baseline before Package 6: exit 0; Ruff clean; Pyright 0 errors/0 warnings; integrity clean; API `336 passed`; web lint clean; Vitest `19 passed`; production build passed; existing mocked Playwright `3 passed`.

## Package status

- Package 1 — Persistence and invariant tests: complete; focused checkpoint gate `34 passed`.
- Package 2 — Navigator reads and compatible review: complete; focused checkpoint gate `117 passed`.
- Package 3 — Task command services and concurrency: complete; focused checkpoint gate `81 passed`.
- Package 4 — Patient response, timelines and resolution safety: complete; focused checkpoint gate `67 passed`.
- Package 5 — Seeded UI and live browser-to-database journey: pending.
- Package 6 — Reproducible verification and handoff: pending.

## RED / GREEN evidence

- Package 1 schema RED: reflection failed because `navigation_task.authorized_proposed_change_id` and follow-up persistence were absent. GREEN: exact composite tenant/patient/need/task keys, both follow-up tables, ORM metadata, and additive `0006_navigator_closed_loop` installed.
- Package 1 authorization RED: pending proposals could bind and approved execution title/owner/due could be rewritten. GREEN: only the exact effective approved v1/v2 task proposal can bind an open task; approved title, owner, due, and binding freeze after claim.
- Package 1 transition RED: completion produced no durable follow-up request. GREEN: claim/start/complete use the strict transition chain, database-owned completion time, one deterministic typed audit per transition, and one deterministic completion request in the same transaction.
- Package 1 boundary RED: bound deletion returned only a generic foreign-key failure. GREEN: a dedicated guard rejects bound deletion, skipped/reversed transitions, and work after navigator authority revocation.
- Package 1 follow-up RED: forged early requests and client response timestamps were accepted. GREEN: request provenance is revalidated; response time is database-owned; request/response rows are append-only; closed needs, revoked roles, duplicate responses, and invalid patient-link attribution are rejected.
- Package 1 identity RED: referenced link history could be rewritten. GREEN: organization/patient/user/link-time attribution and backdated revocation are protected while legitimate later revocation remains allowed.
- Package 1 privilege RED: `ojcc_app` could not use the guarded response append surface. GREEN: it has only SELECT on requests and SELECT/INSERT on responses; trigger functions use the established SECURITY DEFINER/search-path pattern; direct request insertion and history rewrites remain unavailable.
- Package 1 rollback tests: injected request and audit failures both leave task state and side effects fully rolled back.
- Package 1 migration RED: downgrade was an unconditional placeholder refusal. GREEN: empty 0006 upgrades/downgrades cleanly to exact 0005 task-guard behavior, populated 0005 unbound/v2 resource history is preserved, and downgrade refuses actionable history loss.
- Package 1 auditor RED: deliberate title/lifecycle, request, response, and transition-event corruption was invisible. GREEN: five content-minimal diagnostic categories detect invalid/missing/duplicate/mismatched history; valid closed-loop and legacy unbound history remain at zero violations.
- Package 1 focused command: `34 passed in 14.44s`.
- Package 1 static checks: Ruff clean; Pyright on `services/api/app` and `services/api/tests/closed_loop` reports `0 errors, 0 warnings`.
- Package 1 schema/seed checks: Alembic current `0006_navigator_closed_loop (head)`; Alembic check reports no drift; reset plus two identical seed passes succeeded; integrity reports zero violations.
- Immutable migration hashes 0001–0005 rechecked and exactly match the preflight values above.
- Package 2 workspace RED: the need-scoped navigator route returned `404` because no typed evidence/review projection existed. GREEN: the tenant-scoped workspace exposes exact active source lineage, labels corrections separately from independent observations, preserves absent versus null values, uses deterministic root-time/UUID ordering, and leaves inherited recurrence evidence source-free.
- Package 2 proposal RED: multiple proposal roots, revision state, v2 resource snapshots, and unsupported historical schemas had no review surface. GREEN: v1/v2 roots, exact policy/decision/resource snapshots, effective revision states, and unsupported-but-visible non-actionable history are projected without selecting a synthetic "latest" authorization.
- Package 2 authority RED: omitting `qualifying_role_assignment_id` failed request validation. GREEN: the server deterministically resolves the newest active matching role interval while preserving explicit-ID compatibility, tenant/user/role checks, revocation behavior, and the existing self-approval rule.
- Package 2 PHI RED: newly exposed approval reason prose accepted contact details. GREEN: one shared public-demo prose guard protects check-in and approval text while excluding identifier/timestamp fields from content scanning.
- Package 2 focused command: `117 passed in 8.29s` across workspace, review, approvals, check-ins, and navigator queue tests.
- Package 2 static checks: Ruff clean; Pyright on `services/api/app` and `services/api/tests/closed_loop` reports `0 errors, 0 warnings`.
- Package 2 contract review: two consecutive generator passes were byte-identical; the route comparison found no removed paths and exactly one addition, `/v1/navigator/needs/{need_id}/workspace`; old explicit approval-role clients remain accepted.
- Package 3 claim RED: an exact approved proposal received route `404`. GREEN: claim uses need → exact proposal → refreshed task → active-role locking, copies the approved title, derives the signed actor as owner, normalizes an aware future due time, and returns the database-authored claim state/audit.
- Package 3 transition RED: start and complete routes returned `404`. GREEN: only the bound assignee can move assigned → in-progress → completed, and completion returns the trigger-authored immutable request without inserting a service duplicate.
- Package 3 boundary coverage: pending, superseded, wrong-target, foreign, closed, historical-unbound, naive/past due, changed claim tuple, wrong owner, revoked authority, injected identity fields, v1/v2 and multiple-root cases all return the specified safe result without granting execution.
- Package 3 replay coverage: exact claim/start/complete retries return `replayed=true`; a completed task retains the same task/request/timestamps after Outcome and after its due instant; replay emits no duplicate transition audit or request.
- Package 3 concurrency RED: a second transaction reused the pre-lock SQLAlchemy task state, producing `task_state_conflict` instead of `task_claim_mismatch` and misreporting concurrent completion as first-run. GREEN: the post-need-lock task query refreshes the identity map; committed two-session tests now pass for claim/claim, complete/complete, and both orderings of start/Outcome and complete/Outcome with a bounded lock timeout and final-row assertions.
- Package 3 focused command: `81 passed in 8.51s` across task commands, task concurrency, persistence guards, Outcomes, and need/task lifecycle integration.
- Package 3 static checks: Ruff clean; Pyright on `services/api/app` and `services/api/tests/closed_loop` reports `0 errors, 0 warnings`.
- Package 3 test-fixture note: the committed concurrency aggregate and exact organization teardown live in `closed_loop/conftest.py` so real independent connections can observe the same rows; teardown disables triggers only inside its owned synthetic organization and deletes explicit tenant-scoped tables.
- Package 4 follow-up RED: patient follow-up list/response routes returned `404`. GREEN: the currently linked supporting actor sees only their requests, the fixed prompt and derived status; response author/link/time come from the signed session and database, notes normalize before validation, and exact retries return the same identity/time with `replayed=true`.
- Package 4 response boundaries: all three controlled values, null/blank notes, changed content/author, foreign requests, injected identity/time, PHI-like content, link/role revocation, unanswered closure, and response-without-automatic-Outcome are executable tests. A response never mutates task/need/Outcome state.
- Package 4 timeline RED: the patient route returned `404` and navigator workspaces had no history. GREEN: discriminated typed events use exact source IDs/timestamps and stable `(occurred_at, causal_rank, source_id)` ordering; correction, inherited lineage, governance, task transitions, follow-up, Outcome, and actual cancellation are projected without raw event serialization.
- Package 4 audience test: a full closed journey exercises every navigator kind and every patient-allowed kind. Patient JSON excludes sentinel task titles, proposal rationale/IDs, staff/role IDs, internal Outcome notes, and raw audit payloads while retaining the patient's own validated response note and controlled labels.
- Package 4 concurrency/auth: committed two-session response/Outcome tests pass in both orderings with exact final row counts and bounded locks. Real signed-cookie requests succeed before link/role revocation and are rejected as `401` by a fresh database session afterward.
- Package 4 Outcome safeguard: the shared synthetic-demo PHI guard now covers newly exposed Outcome note prose while preserving existing Outcome lifecycle/idempotency tests and never scanning UUID/idempotency/timestamp fields.
- Package 4 focused command: `67 passed in 4.70s` across follow-ups, timelines, response/Outcome races, signed auth, Outcomes, check-ins, and tenant isolation.
- Package 4 static checks: Ruff clean; Pyright on `services/api/app` and `services/api/tests/closed_loop` reports `0 errors, 0 warnings`.

## Contract hashes

- Baseline generation completed twice with identical hashes: OpenAPI `2EEC25C...D1428`; TypeScript `192BD4F...CDCDD0`.
- Package 2 generation completed twice with identical hashes: OpenAPI `B13F5DAE25DE119D999848559E3644B6A9D2A5398915BBE1966517CC48BFB8B0`; TypeScript `08572A570973D0B497CDC718E754CD8456D7A0AEBCB776ECCA805D6CE0B500F8`.
- Package 3 generation completed twice with identical hashes: OpenAPI `9916699F12F2E05DD8805DBB8DEF6810705C7687F546322023000E935DCDB799`; TypeScript `86C8EF5C1893E6B0A3DF116DABF22C596FFE219C781CC71369199F6F71BA502B`. Contract review found no removed paths and exactly the claim/start/complete route additions; task status remains an explicit enum.
- Package 4 generation completed twice with identical hashes: OpenAPI `56EB4AAB2D0E650E121091BE68737CC12B8DCD5254814ECAA1DDD1767B106882`; TypeScript `9C46A9CB6A2654ED0E41A964FD86D7F9479CCD5874EF5F0ADFF188BCF70DDC46`. Contract review found no removed paths and exactly the follow-up list, response, and patient timeline additions; timeline unions include OpenAPI discriminators and command conflicts have documented typed payloads.

## Checkpoint commits

- `67af3c6` — preserved the byte-identical approved plan on the feature branch.
- `eca440a` — started the approval/baseline progress ledger.
- `1e7ee41` — `feat: persist approved task execution and follow-up history`.
- `2a33d37` — `feat: expose evidence and governed task proposal review`.
- `59c0b39` — `feat: operate approved navigator tasks safely`.
- Package 4 checkpoint prepared with message `feat: capture patient follow-up and audience-safe journey history`.

## Decisions and conflicts

- Preflight review found no material scope or contract conflict. The plan status line still says “awaiting user approval,” but the execution contract explicitly requires conversation approval, which is present; the plan itself must remain byte-identical.

## Next exact step

Commit the reviewed Package 4 files explicitly, then read the installed Next.js guidance and begin Package 5 with failing seed, component, and live-browser controls. No Package 6 work may begin before the Package 5 checkpoint.
