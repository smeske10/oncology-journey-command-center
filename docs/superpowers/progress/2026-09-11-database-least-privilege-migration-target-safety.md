# Database least privilege and migration-target safety progress ledger

Created: 2026-09-11

Status: **Approved on 2026-09-11; implementation started in the isolated feature worktree.**

## Milestone boundary

- Milestone: Navigator closed-loop post-merge security and operational hardening — Milestone 1 only.
- Scope: database least privilege and migration-target safety.
- Product placement: Week 4 evaluation, hardening, and public-release readiness, closing Week 1 credential and authorization gaps around the delivered Week 2 closed loop.
- Explicitly excluded: demo-session/cardinality hardening, deterministic audit-ID/FIPS work, broader release review, Week 3 orchestration/retrieval/evaluations, deployment, merge, and branch cleanup.

## Verified repository baseline

- Canonical repository: `C:\Users\smesk\Claude Cowork\Projects\Personal Brand\Job Search — AI Product Roles\oncology-journey-command-center`
- Remote master tip verified with `git ls-remote` and fetched without changing the checked-out local master branch.
- PR #6 merge commit: `47aa43c5cc7a307140e5317240f20d58dbb8b6f5`
- Merge subject: `Merge pull request #6 from smeske10/feature/navigator-closed-loop`
- Merge parents: `5f12cd613d6aea0f75caec963d4f83a49ce73379` and `df8bc0186b169390a68f62c62928f8e5df55fee7`
- Verified `feature/navigator-closed-loop` tip is an ancestor of remote master.
- Local main checkout remains on `master` at `5f12cd6` with its pre-existing untracked navigator plan; it was not fast-forwarded or otherwise modified.
- Fresh worktree: `C:\Users\smesk\Claude Cowork\Projects\Personal Brand\Job Search — AI Product Roles\oncology-journey-command-center\.worktrees\database-least-privilege`
- Fresh branch: `feature/database-least-privilege`
- Fresh branch HEAD: `47aa43c5cc7a307140e5317240f20d58dbb8b6f5`
- `.worktrees/` is ignored by `.gitignore`.
- Git's ownership warning was handled with an invocation-scoped `safe.directory` for the exact repository/worktree; no global Git configuration was changed.

## Immutable migration SHA-256 baseline

`.gitattributes` fixes `services/api/alembic/versions/*.py` to LF. Hashes below were taken from the clean feature worktree at the verified merge commit and are the accepted byte baseline:

| Migration | SHA-256 |
|---|---|
| `0001_core_domain.py` | `a177b32040c760e52ffd64872f61104f2064968aa6981295c54728e518cb6391` |
| `0002_identity_pathway_submission.py` | `6fdf3c15fdf51cb9c3729f7f6d0458b51c88eeee8119a10a1084a425f78f5648` |
| `0003_need_task_outcome_lifecycle.py` | `25641a9831bf6cce4198cc60636d60a03058ecc179752d32328c7513bcb6b556` |
| `0004_safety_approval_lifecycle.py` | `301eae2be84c8685b14b0335525fa011cc88015f654cef95212327cf4cee6704` |
| `0005_workflow_knowledge_audit.py` | `c81f81976dd82fde31eb70b1a271205938b9dc1ecae34e84e5780d09c3fdd5cc` |
| `0006_navigator_closed_loop.py` | `9b325c30e7bf0ab82925adbcfc2462546866ed07355e8fef740ac1832ee5f91b` |

## Required evidence read completely

- `docs/product-design.md`, including the Week 2/Week 4 boundaries, deployment shape, authorization history, audit immutability, restore integrity, public-sandbox security, and release gates.
- `docs/superpowers/plans/2026-09-08-navigator-closed-loop-implementation.md`.
- Review attachment `C:\Users\smesk\.codex\attachments\5d92099e-0172-411d-af41-7540d7823a04\pasted-text.txt`.
- Rejected worktree's uncommitted 0007, new tests, and tracked supporting diffs.
- Current merged connection configuration, Alembic environment, migrations 0001–0006, reset/verification/live scripts, CI workflow, seed CLI, API mutation paths, disposable-database helpers, and database-security tests.

## Rejected worktree preservation

- Path: `C:\Users\smesk\Claude Cowork\Projects\Personal Brand\Job Search — AI Product Roles\oncology-journey-command-center\.worktrees\navigator-closed-loop`
- Branch/HEAD: `feature/navigator-closed-loop` at `df8bc0186b169390a68f62c62928f8e5df55fee7`
- Tracked modifications observed: `.env.example`, `services/api/alembic/env.py`, `services/api/app/config.py`, `services/api/app/db/integrity.py`, `services/api/app/db/models/needs.py`, `services/api/tests/closed_loop/test_migration.py`, and `services/api/tests/integration/test_restore_integrity.py`.
- Untracked review material observed: `services/api/alembic/versions/0007_task_binding_and_app_role.py` and `services/api/tests/closed_loop/test_app_role_and_task_binding.py`.
- No reset, clean, delete, checkout, commit, test execution, or development action was performed in that worktree.

## Independently verified findings

1. `DATABASE_URL` currently defaults to the owning `ojcc` login and Alembic uses the same setting, so the API does not exercise `ojcc_app` privileges.
2. `ojcc_app` is a NOLOGIN group created in immutable migration 0005; no merged local/CI provisioning attaches a distinct API login.
3. Current grants cover only the append-only tables from 0005 and follow-up tables from 0006. They do not define a complete runtime surface across all 33 application tables and four views.
4. `ojcc_app` currently has INSERT on `audit_event` and `workflow_transition_event`, although current closed-loop triggers author those rows.
5. Fourteen existing trigger functions are SECURITY DEFINER with `search_path = pg_catalog, public, pg_temp`, but their owner is whichever role ran the migrations; current local/CI migration credentials are the PostgreSQL bootstrap superuser.
6. Alembic, reset, seed, verify, live verification, CI, and six disposable migration/seed helpers propagate only `DATABASE_URL`. An inherited future `MIGRATION_DATABASE_URL` could therefore select a different migration target unless every child environment overwrites both values after pair validation.
7. The live browser journey currently resets, migrates, seeds, runs the API, and performs database assertions through one owner URL. It does not prove non-owner execution.
8. Existing disposable helpers interpolate validated generated database names but force-terminate connections during cleanup. The implementation plan replaces this with safely quoted identifiers, owned-connection disposal, ordinary drop, and explicit leftover reporting rather than terminating unknown sessions.

## Accepted and rejected review observations

Accepted for independent implementation: separate owner/API credentials, complete ACL coverage, removal of API audit/event INSERT, secure function ownership/configuration, migration-target pair validation, and real non-owner execution.

Rejected for this milestone: the draft binding CHECK, its upgrade pre-scan/refusal, and the new `unbound_executing_navigation_task` integrity category. Those conflict with the approved historical-task contract. Existing API controls remain authoritative: open unbound tasks may be claimed and bound atomically; assigned/in-progress unbound history stays readable but non-executable; no historical row is fabricated, backfilled, cancelled, deleted, or rewritten.

## Planning safety record

- No database was created, reset, dropped, reseeded, migrated, or otherwise mutated during planning.
- Persistent `ojcc` was not connected to or changed.
- No test was run against PostgreSQL because database creation and mutation begin only after plan approval under the recorded disposable-database workflow.
- No production code or migration was changed.
- Documentation changes in this worktree are intentionally uncommitted pending approval.

## Approval and execution checkpoint

- User approval received on 2026-09-11.
- The feature branch and all six immutable migration hashes were re-verified before implementation.
- `DATABASE_URL` and `MIGRATION_DATABASE_URL` were both absent from the parent environment at the checkpoint; no inherited target was trusted.
- No database is needed for Task 1's pure URL/settings work. Fresh UUID-suffixed disposable databases will be created only when a database-backed task first requires them, and each exact name and disposition will be recorded here.

## Next exact step

Commit the approved plan and ledger checkpoint, then begin Task 1 by expanding the immutable migration snapshot test from one accepted migration to all six. Do not create or mutate any database until a database-backed test requires a fresh disposable target.
