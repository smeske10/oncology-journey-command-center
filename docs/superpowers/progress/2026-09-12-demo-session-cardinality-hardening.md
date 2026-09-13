# Demo-session/cardinality hardening progress ledger

**Created:** 2026-09-12, America/New_York
**Status:** Architecture and amended implementation plan approved. Task 1 complete and awaiting its execution checkpoint; Tasks 2–5 not started.
**Milestone:** Navigator closed-loop post-merge security and operational hardening; Week 1 auth gaps / Week 4 readiness.

## Approved task boundary

Planning was initially limited to read-only investigation, baseline checks, an isolated feature worktree, and dated planning documents. The user subsequently approved the amended implementation plan and explicitly authorized Task 1 with `executing-plans`. Task 1 makes configuration/interface/test changes only. No database migration, seed, reset, deployment, merge, push, worktree removal, or PostgreSQL access occurred.

## Repository baseline

- Repository: `C:\Users\smesk\Claude Cowork\Projects\Personal Brand\Job Search — AI Product Roles\oncology-journey-command-center`.
- `git fetch origin`: succeeded; origin/master advanced from the prior locally known PR #6 merge to requested PR #7 merge.
- `git rev-parse origin/master` and later `git ls-remote origin refs/heads/master`: `0a3cda82203d850992f2efc3653b653c8253459c`.
- `git merge-base --is-ancestor 7cef17aff4ea07bc68e92aba74c2279687ae187c origin/master`: succeeded.
- [PR #7](https://github.com/smeske10/oncology-journey-command-center/pull/7): MERGED; head `7cef17aff4ea07bc68e92aba74c2279687ae187c`; merge `0a3cda82203d850992f2efc3653b653c8253459c`; mergedAt 2026-09-13T03:43:46Z (2026-09-12 local).
- [PR-head Verify](https://github.com/smeske10/oncology-journey-command-center/actions/runs/34714219997/job/103608314761): completed SUCCESS.
- [Merge-commit Verify](https://github.com/smeske10/oncology-journey-command-center/actions/runs/34736195909): initially in progress; subsequently completed SUCCESS, exact merge SHA. Verify job 103668002149 completed at 2026-09-13T03:51:57Z. The job's complete repository verifier step passed, including the live verification command.
- This is GitHub CI baseline evidence, not a locally rerun full verifier and not evidence that proposed new tests pass.

## Isolation and preservation

- Main checkout remains master at `5f12cd6` with its pre-existing untracked `docs/superpowers/plans/2026-09-08-navigator-closed-loop-implementation.md`.
- Existing `.worktrees/database-least-privilege` remains on `feature/database-least-privilege` at `7cef17a`. No development, reset, clean, deletion, checkout or tests performed there.
- Existing `.worktrees/navigator-closed-loop` remains at `df8bc01`; no changes made there.
- Fresh worktree: `C:\Users\smesk\Claude Cowork\Projects\Personal Brand\Job Search — AI Product Roles\oncology-journey-command-center\.worktrees\demo-session-cardinality-hardening`.
- Fresh branch: `feature/demo-session-cardinality-hardening`.
- Created explicitly from `0a3cda82203d850992f2efc3653b653c8253459c`.
- Git main/common directory checks established the source was the ordinary checkout; `.worktrees` was already ignored. Native self-worktree creation was not available, so authorized Git fallback was used.
- Ownership mismatch in the old checkout handled with exact invocation-scoped safe.directory. No global Git configuration was changed.

## Immutable migration SHA-256 baseline

Read from clean fresh worktree files at the verified merge. Existing LF policy applies.

| File | SHA-256 |
|---|---|
| 0001_core_domain.py | `a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391` |
| 0002_identity_pathway_submission.py | `6fdf3c15fdf51cb9c3729f7f6d0458b51c88eeee8119a10a1084a425f78f5648` |
| 0003_need_task_outcome_lifecycle.py | `25641a9831bf6cce4198cc60636d60a03058ecc179752d32328c7513bcb6b556` |
| 0004_safety_approval_lifecycle.py | `301eae2be84c8685b14b0335525fa011cc88015f654cef95212327cf4cee6704` |
| 0005_workflow_knowledge_audit.py | `c81f81976dd82fde31eb70b1a271205938b9dc1ecae34e84e5780d09c3fdd5cc` |
| 0006_navigator_closed_loop.py | `9b325c30e7bf0ab82925adbcfc2462546866ed07355e8fef740ac1832ee5f91b` |
| 0007_database_least_privilege.py | `7bbe68eeb878fcb6418c62354e9ee323f46e1750ee36d293f69e978ae06018f7` |

The automated immutable-byte guard now covers all seven accepted migrations. No migration file was changed and no migration 0008 was created.

## Investigation record

Read the complete product design, merged database-privilege closure design/implementation plan, original privilege plan and progress ledger, and the full requested auth service/dependencies/route/identity models/auth tests. Read relevant identity integration, seed identity/idempotence, migration identity/index/history, non-owner journey, connection lifecycle, verifier and desktop/mobile live-journey sources. Inspected call sites and privilege/restore guards. Evidence and exact behavior conclusions are recorded in the design.

The user-provided Skills catalog path under `Codex Cowork\Codex Skills` was absent. Located and read the current catalog at `C:\Users\smesk\Claude Cowork\Skills\CLAUDE.md` and Coding Superpowers routing instructions. Applied installed using-superpowers, brainstorming, using-git-worktrees, systematic-debugging, writing-plans and verification-before-completion guidance. No subagents used. User instruction to deliver all planning artifacts before one approval gate governs this context.

## Local verification performed

1. Ran existing auth/token/cookie tests and immutable migration tests from the fresh worktree's services/api:
   `python -m pytest tests/test_auth.py tests/test_core_domain_migration.py -k "demo_session or patient_facing_supporting_actor or accepted_migration_bytes" -q -p no:cacheprovider`.
   Executed inside an in-memory Python wrapper that set a synthetic unreachable URL, removed owner/bootstrap environment values in that child, disabled bytecode/cache output, and made Engine.connect/raw_connection raise if called.
   **Result: 18 passed, 18 deselected in 1.07 seconds; exit 0.** No database connection attempted. This includes six immutable cases and twelve existing auth cases.
2. Connection-free diagnostic: supplied two navigator rows to real SQLAlchemy IteratorResult and invoked the unchanged SqlAlchemyActorRepository.find_active_actor.
   **Result: uncaught MultipleResultsFound.**
3. Repeated diagnostic with two identical patient actor projections.
   **Result: uncaught MultipleResultsFound.**
4. Diagnostics used supplied rows, not a live database. They establish Result/exception behavior only. PostgreSQL acceptance of interval fixtures, HTTP refusal after changes, grant/link replacement rejection, and real runtime journey must be proven failing-first after approval.
5. Read official SQLAlchemy Result and PostgreSQL 16 range-exclusion documentation; sources are linked next to their claims in the design.

## Approved architecture

- Server-only DEMO_ACTORS_JSON, explicit role-to-user roster and patient identity.
- Strict effective grant and bidirectional patient-link cardinality; single-statement shared resolution.
- Role-appropriate patient semantics; no primary-organization access check.
- Token version 2 with exact grant/link provenance; legacy tokens refuse with 401 and can be reissued.
- 503 issuance/config/database failures; 401 revoked/ambiguous session authority; preserved 403/422.
- Existing cookie guarantees and history intact.
- No migration 0008; no global interval-enforcement claim.
- Explicit seed roster output, nonrepairing identity validation, real API/PostgreSQL red-green gates and desktop/mobile full verification.

## Deliverables

- [Design](../specs/2026-09-12-demo-session-cardinality-hardening-design.md)
- [Implementation plan](../plans/2026-09-12-demo-session-cardinality-hardening-implementation.md)
- This ledger.

The documents and Task 1 changes are pending the Task 1 commit. No PostgreSQL database has been created, inspected, connected to, seeded, reset, migrated, or dropped. Persistent ojcc was not accessed. No credentials were printed.

## Execution record after approval

Final planning checks: all seven worktree migration files compared byte-for-byte with their exact merge-commit Git blobs and matched the recorded SHA-256 values. The initial `git show` form encountered Windows path-length handling; read-only `git cat-file blob` comparison succeeded for every file. All three documents passed local-link, fenced-block and placeholder checks. `git diff --check` passed; no tracked or staged changes exist. Git status contains exactly these three new planning documents. Worktree HEAD and all pre-existing worktree heads remain as recorded above.

The user approved the amended implementation plan and authorized Task 1 on branch
`feature/demo-session-cardinality-hardening` at starting commit
`0a3cda82203d850992f2efc3653b653c8253459c`.

### Task 1 — explicit roster and invariant interfaces

- Baseline immutable guard: the first focused invocation could not collect because the local shell
  had no `DATABASE_URL`. It was rerun with a synthetic unreachable loopback URL; the hash-only
  path made no connection. Result: **7 passed, 18 deselected**.
- TTL import RED: `test_non_integer_ttl_does_not_break_config_import` failed because eager
  `int(...)` raised `ValueError` during `app.config` import and printed the malformed value.
- TTL import GREEN: import-safe optional integer parsing produced **1 passed**. Missing TTL retains
  30; blank/non-integer values become invalid optional state. Optional UUID parsing likewise returns
  invalid optional state rather than raising at import. Raw `DEMO_ACTORS_JSON` remains unparsed in
  settings.
- HTTP configuration RED: the TTL/organization selection produced **2 failed, 5 passed**. A parsed
  `None` TTL escaped both service factories as `TypeError`; numeric 0/121 and invalid organization
  were already sanitized.
- HTTP configuration GREEN: both factories now reject invalid TTL state before construction. The
  route returns exact 503 detail with no Set-Cookie, and the current-session factory returns the
  same sanitized configuration failure. Result: **7 passed, 12 deselected**.
- Roster parser RED: collection failed with `ModuleNotFoundError` for the planned
  `app.auth.demo_actors` interface.
- Roster parser GREEN: strict duplicate-preserving parsing, exact role-specific fields, UUID
  validation, subset rosters, absent/malformed input rejection, and intentional cross-role user
  reuse produced **26 passed, 1 deselected**. Errors do not echo raw roster contents.
- Added frozen `ResolvedAuthority` and `VerifiedDemoSession` interfaces while leaving
  `CurrentActor` unchanged.
- Focused verification: direct TTL parsing coverage proves absent input retains 30 while blank and
  malformed input becomes import-safe invalid state. The complete
  `tests/test_demo_actor_configuration.py tests/test_auth.py` run produced **50 passed**. Pyright
  produced **0 errors, 0 warnings, 0 informations**. Ruff initially found one
  import-order issue in the new test, which was corrected; the fresh rerun produced
  **All checks passed**. The seven-migration hash guard produced **7 passed, 18 deselected**.
  `git diff --check` passed with only line-ending notices.
- No database connection was attempted, and no disposable database was created or reserved.

Tasks 2–5 remain pending. Do not start Task 2 without the next execution checkpoint.

## Conditional-approval amendment

The user approved the architecture and retained the explicit actor roster, strict cardinality,
authority-bound version-2 tokens, controlled legacy-token 401 behavior, and no-migration-0008
boundary. Implementation remains unauthorized until the amended plan is reviewed.

Three review gaps were verified and corrected in planning only:

1. `Settings.demo_session_ttl_minutes` used eager `int(...)` parsing, so malformed text could abort
   `app.config` import before the sanitized 503 boundary. The design and Task 1 now require separate
   failing import and HTTP tests for non-integer, blank, absent, and out-of-range values.
2. Token compatibility was descriptive rather than executable. Task 3 now enumerates failing tests
   for missing/legacy/invalid-value/wrong-type `ver`, missing/invalid/wrong-type `ra`, and the full
   supporting-actor/staff `pil` and `patient` shape matrix; Task 4 proves controlled HTTP 401s.
3. Original Tasks 2–5 bundled multiple behaviors per checkbox. Their amended steps now separate
   each failing test, observed RED, minimal implementation, focused GREEN, static verification,
   evidence update, and commit. Role resolution, patient cardinality, token parsing, issuance,
   reauthorization, error mapping, cookie preservation, seed intent, and live propagation have
   explicit checkpoints.

At this conditional-approval checkpoint, no production or test file had been changed and no
database had been accessed. The later Task 1 execution record above supersedes that historical
working-tree statement.

**Next exact step:** Commit the verified Task 1 changes, report the Task 1 execution checkpoint, and
wait for authorization before Task 2.
