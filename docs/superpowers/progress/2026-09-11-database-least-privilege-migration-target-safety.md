# Database least privilege and migration-target safety progress ledger

Created: 2026-09-11

Status: **Original Tasks 1–6 and corrective Tasks 1–4 complete; corrective Task 5 next.**

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

## Task 1 — complete

Implemented the immutable migration guard, pure database-target parser and pair validator, fail-closed settings contract, and Alembic owner-target selection with online connected-identity verification.

### Test-first evidence

- Baseline guard: `python -m pytest tests/test_core_domain_migration.py -k immutable -q` → 6 passed.
- URL contract RED: `python -m pytest tests/test_database_targets.py -q` → 15 intended failures because `app.db.targets` was absent.
- URL contract GREEN: the same command → 15 passed after the pure target implementation.
- Settings RED: `python -m pytest tests/test_database_targets.py -k settings -q` → 4 intended failures for the missing required/optional credential behavior.
- Settings GREEN: the full target file → 19 passed after the settings implementation.
- Alembic RED: `python -m pytest tests/test_database_targets.py -k alembic -q` → 3 intended failures and 1 pass because the old environment ignored `MIGRATION_DATABASE_URL`.
- Alembic GREEN: the full target file → 23 passed after owner-target validation and selection.
- Online transaction regression RED: the first database-backed migration test migrated without error but its schema was absent (`NoSuchTableError`) because the connected-identity preflight opened an implicit transaction that was rolled back on connection close.
- Online transaction regression GREEN: ending that read-only preflight transaction before Alembic begins its transaction made the same test pass.
- Complete Task 1 focused gate: `python -m pytest tests/test_database_targets.py tests/test_core_domain_migration.py -q -s` → 47 passed in 42.54 seconds.
- Ruff on all Task 1 Python files → passed.
- `git diff --check` → passed.

### Environment diagnosis

On this Windows host, `localhost` stalled before a TCP session while `127.0.0.1` reached the same healthy PostgreSQL service immediately. The database-backed gate therefore used the plan-approved numeric loopback spelling consistently for both credentials. No host aliases were treated as equivalent by application code.

### Disposable database record

All names below are synthetic UUID-suffixed databases. No connection was made to persistent `ojcc`; its configured URL was used only as the source of the local synthetic owner credential and replaced with `postgres` or a fresh generated database name before connection.

| Database | Disposition |
|---|---|
| `ojcc_migration_test_492015350bb64031b29bc15f5a9fe15d` | dropped normally after the failing transaction-regression test |
| `ojcc_migration_test_2a0e7278c4b949d682270652d6c08f66` | dropped normally after the regression fix passed |
| `ojcc_migration_test_6e87ca3d8fad4498a746201e65e8a265` | dropped normally |
| `ojcc_migration_test_44f43a6cb9eb47f3b967195630dd7ac4` | dropped normally |
| `ojcc_migration_test_fffeb1caadcc4876972e76cd37bc2561` | dropped normally |
| `ojcc_migration_test_58a90b9fb0d5449f9f72175968eee8f8` | dropped normally |
| `ojcc_migration_test_3f195c54489f47fbb06b4321c4eb3f95` | dropped normally |
| `ojcc_migration_test_f7cab902c7234ebb8d1c4c1a05d93ad7` | dropped normally |
| `ojcc_migration_test_a955d839264c4b2baf4d1db1dedae679` | dropped normally |
| `ojcc_migration_test_5662382a75fc47b99b29bdddfe6f1204` | dropped normally |
| `ojcc_migration_test_f716faf1296f4c0eaf162c15535e72ed` | dropped normally |
| `ojcc_migration_test_74d81de157de43099b8f035bdb4ca2db` | dropped normally |
| `ojcc_migration_test_01bd6d821fb74a3089ca40de33b18fd9` | dropped normally |
| `ojcc_migration_test_49df5f75f2f24a00a3e458bac2e55d57` | dropped normally |
| `ojcc_migration_test_27aedf90f5224d8b9abec9e2d1df137d` | dropped normally |
| `ojcc_migration_test_d446ebfa532b4c6da5732837bdace7d1` | dropped normally |
| `ojcc_migration_test_f333b119a7c24793b33f423c3388e041` | dropped normally |
| `ojcc_migration_test_1c7d3d26610c4b68ad94fb3fd9cb6a1d` | leftover observed after an interrupted `localhost` diagnostic run; zero sessions; not dropped because exact provenance is uncertain |
| `ojcc_migration_test_e51d4f44b6a4475688c57eaaf82e4753` | leftover observed after an interrupted `localhost` diagnostic run; zero sessions; not dropped because exact provenance is uncertain |

## Task 2 — complete

- Synthetic local `ojcc_migrator` and `ojcc_api` login roles were provisioned through the bootstrap credential with the approved non-superuser profiles.
- Membership `ojcc_app` → `ojcc_api` was verified as INHERIT true, SET false, ADMIN false.
- Disposable database `ojcc_privilege_test_591589cc9392494193cbb76c354a8b27`: recorded as planned before mutation, created with owner `ojcc_migrator`, migration attempt rolled back, and dropped normally with zero active sessions.
- Fresh replay reached immutable migration 0005 and failed on `ALTER ROLE ojcc_app ... NOSUPERUSER` with PostgreSQL `InsufficientPrivilege`: only a superuser may change the SUPERUSER attribute, even when setting it false. `CREATEROLE` is insufficient.
- This proves the approved combination “immutable 0005 + wholly non-superuser 0001→head replay” is not executable on PostgreSQL 16. No production migration was edited, and no partial revision remained in the disposable database.
- Recommended compatibility bridge: run 0001–0004 as `ojcc_migrator`; run only immutable 0005 as the existing bootstrap superuser; immediately reassign the exact allowlist of application tables, functions, and enums created by 0005 to `ojcc_migrator`; remove the bootstrap credential; then run 0006→head as the non-superuser owner. Broad `REASSIGN OWNED` is intentionally excluded because the bootstrap role also owns extension objects required by the system. Alternative: explicitly elevate `ojcc_migrator` only for 0005 and revoke SUPERUSER before 0006/0007. Both required user approval because each changes the fixed credential model.
- User approved proceeding with the recommended 0005-only bootstrap bridge. The implementation plan now requires an explicit third bootstrap URL only for fresh replay/reset/provisioning, keeps it out of API and web environments, and removes CREATEROLE from the final migration-owner profile because 0005 no longer runs under that role.
- `ojcc_migrator` was explicitly reduced from CREATEROLE to NOCREATEROLE through the bootstrap credential and its final non-superuser/CREATEDB/NOCREATEROLE profile was verified.
- Planned Task 2 RED/GREEN database before creation: `ojcc_privilege_test_66114af51c514d3281aeff903fa914df`.
- `ojcc_privilege_test_66114af51c514d3281aeff903fa914df`: created, replayed through 0006 with the exact 0005 ownership allowlist, and dropped normally after zero-session/owner verification.
- RED catalog databases `ojcc_privilege_test_3885f599b462436fa6504d4e45166eee`, `ojcc_privilege_test_9a70b499916f4478ac9777d6010318c6`, `ojcc_privilege_test_22e76f679ea04634a131be1c9f1736b0`, `ojcc_privilege_test_352c718db11b4c329ce1b8c4bf3ce5d9`, and `ojcc_privilege_test_5fb7c0710e5d40ba84163855c61b05a8`: each created by the test fixture and dropped normally; the final clean RED was 1 passed and 5 intended privilege failures.
- GREEN catalog database `ojcc_privilege_test_c4c44434064e4c2b8dcfcce2eec58c68`: created by the test fixture and dropped normally; all 6 catalog tests passed against migration 0007.
- Runtime-boundary development databases `ojcc_privilege_test_f19f82cb9fe14daa909b9ffba504cbb6`, `ojcc_privilege_test_3552c388185849c289d6e5ba9404e8cb`, `ojcc_privilege_test_58b0e70f95e9465a9bfc14b934d0cf26`, `ojcc_privilege_test_5cde646d94024d149e7fcabe8b5c2ab5`, and `ojcc_privilege_test_8499a00de5724141815e67370421363b`: each created and dropped normally while test-data assertions were corrected and the real row-lock privilege incompatibility was isolated.
- PostgreSQL requires UPDATE privilege for `SELECT ... FOR UPDATE`, including locks on relations intentionally classified as SELECT-only. The runtime now acquires a stable transaction-scoped advisory lock for the reported-need aggregate, retains row locks on updateable `navigation_task`, and performs ordinary reads of immutable proposal/authority records. This preserves both the approved relation matrix and task/Outcome serialization.
- Runtime-boundary GREEN database `ojcc_privilege_test_a5053f9b99a54d568b461dea3bc10cc0`: created and dropped normally; the API login completed claim → start → complete → Outcome through trigger-authored protected writes, while direct event inserts were denied.
- Full Task 2 GREEN databases `ojcc_privilege_test_b73226b65d4a498ab71527dba72f91c1`, `ojcc_privilege_test_9a62a330fdb44556856d334e3f025dae`, and `ojcc_privilege_test_1cd1cbf92f3948c898108e985ca8cdff`: each created and dropped normally. The 12-test suite passed fresh replay, populated preservation, failure-atomic preflight, exact ACL/function catalogs, non-owner runtime commands, head/check, and online/offline downgrade refusal.
- Task 2 final gates: immutable migrations 0001–0006 retained their approved SHA-256 hashes; 6 immutable-hash tests passed; Ruff passed on all Task 2 Python files; `git diff --check` passed.

## Task 3 — complete

- Added one shared disposable-database helper with exact UUID-name validation, quoted identifiers, explicit owner/API Alembic environments, the bounded 0005 bootstrap bridge, refusal to reuse a name, and ordinary-drop cleanup that reports rather than terminates unknown sessions.
- Converted all six database-creating modules to the shared helper. Repository search found no duplicate lifecycle or forced-termination implementation outside the helper's deliberate live-session negative test cleanup.
- Remaining database fixtures now label and use `MIGRATION_DATABASE_URL` for privileged setup and rollback. The dedicated privilege journey continues to execute through `DATABASE_URL` as `ojcc_api`.
- Added a repository guard that parses test subprocess calls and requires every real Alembic child to receive an explicit two-target environment. The invalid-target unit test module is the sole intentional exemption.
- Replaced the demo seed's superuser-only `session_replication_role` switch with owner-authorized USER-trigger disable/enable over the fixed seed table allowlist. Foreign-key and system-trigger enforcement remains active.
- The legacy 0005 concurrency test now observes lock state through the explicitly bounded bootstrap connection because PostgreSQL hides another role's query text from a non-superuser observer.
- Core migration suite: 24 passed. Immutable audit migration cases: 7 passed. Closed-loop migration suite: 3 passed. Restore-integrity suite: 13 passed. Seeded application contract: 1 passed.
- Shared helper plus full privilege suite: 21 passed. Immutable hashes: 6 passed. Ruff: passed.
- `test_demo_seed.py`: 33 passed; the one reset-wrapper test failed at the intentionally not-yet-implemented Task 4 three-target propagation boundary. Its exact leftover had zero sessions and was ordinarily dropped. This is the expected RED that opens Task 4, not a Task 3 helper failure.

### Task 3 disposable database record

Every database below was fresh, UUID-suffixed, and ordinarily dropped. Rows marked “deliberate leftover” were intentionally held open to prove that cleanup never terminates unknown sessions, then closed and ordinarily dropped by the owning test. No persistent database was connected to or mutated.

| Database | Disposition |
|---|---|
| `ojcc_migration_test_74fd3ac4a26b44d597f3e1edbb67b038` | dropped normally after helper development failure |
| `ojcc_migration_test_0bfdc570438e46fd96b673722b282778` | dropped normally after helper development failure |
| `ojcc_migration_test_e566f3e30904489aac9a51b1768d1d68` | dropped normally |
| `ojcc_migration_test_6abc3d16f6d04ea19e69721b3493e37c` | deliberate leftover; connection closed; dropped normally by the test |
| `ojcc_migration_test_2cf3a25d716444a09b50c6d45bcb7c3e` | dropped normally |
| `ojcc_migration_test_c3d71b29d88e4a2b93eb97ce37c2a73d` | dropped normally |
| `ojcc_migration_test_9c7e44a59a234d8cbc3263f55ffea36f` | dropped normally |
| `ojcc_migration_test_27a868b64984496ab3e676c8367467d1` | dropped normally |
| `ojcc_migration_test_f00ab6ff0cf54a6080d060513611d5c8` | dropped normally |
| `ojcc_migration_test_2bdab45a558846ca98a34bf2ac9e742d` | dropped normally |
| `ojcc_migration_test_26cb25ae13404afca8229c403d381e66` | dropped normally |
| `ojcc_migration_test_0491b27f3b264f1cafcfbbb941e94528` | dropped normally |
| `ojcc_migration_test_1773e3ee49c445b586cdc22bb522e3c2` | dropped normally |
| `ojcc_migration_test_165630835b3a4a64a3ae8dba55c69b19` | dropped normally |
| `ojcc_migration_test_1d2a398efe174b88ae679c9de140b7ff` | dropped normally |
| `ojcc_migration_test_40131a766db34f6d97278d9a50e14948` | dropped normally |
| `ojcc_migration_test_ca430752cd1c45e0bd594006ad864814` | dropped normally |
| `ojcc_migration_test_97fd12c2a7334d50a8459bf5ee61ad86` | failed test left open; zero sessions verified; dropped normally |
| `ojcc_migration_test_da7df96cec8e4d29a5b876c27c6a8245` | failed test left open; zero sessions verified; dropped normally |
| `ojcc_migration_test_c304ce62be764d70baf9805917db02c7` | failed test left open; zero sessions verified; dropped normally |
| `ojcc_migration_test_67a5feb7c8f64b03bee546eed9c6fde3` | failed test left open; zero sessions verified; dropped normally |
| `ojcc_migration_test_94b3c930a56f4c0583aa6d32ad078f84` | failed test left open; zero sessions verified; dropped normally |
| `ojcc_migration_test_d3d78ddf8c7c4f89829b7024b504af1b` | dropped normally |
| `ojcc_migration_test_477e5f0a02434066b2db72b806cb9c91` | dropped normally |
| `ojcc_migration_test_d7a5009d84874f13bd50e0b018f57b75` | dropped normally |
| `ojcc_migration_test_b5bc7977fcec4199bbaf2020bde2b970` | dropped normally |
| `ojcc_migration_test_ea1fb7596bc2433996e73f0cfe92a9df` | dropped normally |
| `ojcc_migration_test_cb69d748822d4b9d96872a932e844fbf` | dropped normally |
| `ojcc_task7_63765c54f8c04f38b89797378e5a88a5` | failed seed left open; zero sessions verified; dropped normally |
| `ojcc_task7_43d1f472ce764616b25926092f59435e` | dropped normally |
| `ojcc_task7_20a5b64065e24fbfba1386212bb3f6e0` | dropped normally |
| `ojcc_task7_1b6b00a75a034d0382b82575fd2ffe41` | dropped normally |
| `ojcc_migration_test_8cc21991aa404960a9f0d4a2ca814044` | dropped normally |
| `ojcc_migration_test_6d5210c439a944d691c863e07b93ee3a` | dropped normally |
| `ojcc_migration_test_6f164dd0b01748af89cfd6a9e8e1b864` | dropped normally |
| `ojcc_task5_migration_8e4777db6a8e481f8bb40ac8a9a1b1f3` | dropped normally |
| `ojcc_task5_migration_66f12a348cb941b6af19604d3ec4f3f1` | dropped normally |
| `ojcc_task5_migration_d391697686504f75ac2f475093acd52d` | dropped normally |
| `ojcc_task5_migration_be17228a8abf4e07ad61d263f8c7c2c2` | dropped normally |
| `ojcc_task5_migration_493cfd0a38984d2ca5d74099c5f22e6e` | dropped normally |
| `ojcc_task5_migration_9c1d7a58de724245968326d51441f825` | dropped normally |
| `ojcc_task5_migration_ab7c33e170914f7cbdf422d8ad6e9b1c` | dropped normally |
| `ojcc_task5_migration_949eff2cb06c4783b3a8e682ab7c17a0` | dropped normally |
| `ojcc_task5_migration_04b129c3cbc249c3a9eb2d895b86fbd5` | dropped normally |
| `ojcc_task5_migration_91637c5bd8e74cccb296309d8b35063b` | failed test left open; zero sessions verified; dropped normally |
| `ojcc_task5_migration_01bd97ac2b6f4c6aa4297af4ca94a619` | dropped normally |
| `ojcc_migration_test_6d825141395f4b55ae23aef56b251f03` | dropped normally |
| `ojcc_migration_test_35efe6d0c6d14da988370fedfa1b169a` | dropped normally |
| `ojcc_migration_test_6cccb1fd21a94b5aaaa7ced089dafb43` | dropped normally |
| `ojcc_migration_test_66fdce2642154ba2aa3f9921f1e2ae11` | dropped normally |
| `ojcc_migration_test_a7360b615ab34000b8eeadc3b9edb3b9` | dropped normally |
| `ojcc_migration_test_10f75f6d5abd4c10baf74ff2f034dafd` | dropped normally |
| `ojcc_migration_test_470ab5ef99d5471f9c67bb911ec0d51f` | dropped normally |
| `ojcc_migration_test_4a7c82f4e1a44d85955c66f8ed5469c0` | dropped normally |
| `ojcc_migration_test_678d596c531d4a6aa1b0be391904facf` | dropped normally |
| `ojcc_migration_test_f1f3a06c86a84ee9a47ef82ae0d25ce3` | dropped normally |
| `ojcc_migration_test_3f4baa4f68904980ac71cfdb605cb218` | dropped normally |
| `ojcc_migration_test_8af50b02e18d4f8ca34b9903ce9ceb6a` | dropped normally |
| `ojcc_migration_test_a7c7c6f314744508b0c08c22f6592c94` | dropped normally |
| `ojcc_migration_test_30a0082d6fa444408b720b314a6290d5` | dropped normally |
| `ojcc_migration_test_fcd0a014804e4572be47f96360ac538c` | dropped normally |
| `ojcc_task5_migration_84e10020b10c4ebebebd45b55a5e9996` | dropped normally |
| `ojcc_task5_migration_cb9426cce74a44b6b75b2287b6cb59bb` | dropped normally |
| `ojcc_task5_migration_39c3339029474f82b5bbe348d5196f05` | dropped normally |
| `ojcc_task5_migration_5026de879ffc4f9c8930ef619773f96b` | dropped normally |
| `ojcc_task5_migration_3c796c0d4bdb4ab8a966b8e2289fc1fa` | dropped normally |
| `ojcc_task5_migration_b8310ce9a8d04ebfaac650802b0300fb` | dropped normally |
| `ojcc_task5_migration_71ececd4954140c59be8a422e1f92209` | dropped normally |
| `ojcc_migration_test_ae46a1199b6b4c7a89b062d901025c84` | dropped normally |
| `ojcc_migration_test_3c20777a4b6141dfad986fdb8dea635a` | dropped normally |
| `ojcc_migration_test_6d2d70fcceb449649081ea7c72ad5acc` | dropped normally |
| `ojcc_task7_c9236f1105564ae5ae674c8a123d0d36` | dropped normally |
| `ojcc_task7_8c3d476ec47a41c498aff2e49327c23d` | dropped normally |
| `ojcc_task7_2937633a6fce4423a6229d81ee6cd1fd` | dropped normally after one transient concurrent replay failure |
| `ojcc_task7_5c1ac046c09843e19beae3e83eb33dd3` | dropped normally |
| `ojcc_task7_f284ebce38ed4c0183e1ba1455ea77b6` | dropped normally |
| `ojcc_task7_a3e250f3d3974cde9b6ef4401d4de6c9` | dropped normally |
| `ojcc_task7_5fbc51f5aefa4a0ab7d482b4bf88a618` | dropped normally |
| `ojcc_task7_4e58d46b91ed42f9bae00fe48d0bf58f` | dropped normally |
| `ojcc_task7_4fb96ab6b1a04af5bcc7e9ff0e80cce2` | expected Task 4 reset RED left open; zero sessions verified; dropped normally |
| `ojcc_migration_test_32275bb543c84f75990d1c0fbe9bb338` | dropped normally |
| `ojcc_migration_test_58db170f81f545ebbdd6009c98d5e2d5` | deliberate leftover; connection closed; dropped normally by the test |
| `ojcc_migration_test_9c929ed53e954adeab869ba63c0479b3` | dropped normally |
| `ojcc_migration_test_248d2e5b822e49e4a387ebe4fd101683` | dropped normally |
| `ojcc_migration_test_57e1a1111f5c41049a6eb6205621bdf2` | dropped normally |

## Task 4 — complete

- Added `scripts.replay_schema`, which validates the exact bootstrap/owner/API triple, runs 0001–0004 as the non-superuser owner, runs only immutable 0005 as bootstrap, transfers the exact 0005 object allowlist, strips bootstrap state from Alembic children, and completes 0006→requested revision as owner.
- Reset now validates all three loopback disposable targets and confirmation before connection, drops/recreates `public` only through the owner, replays through the bounded bridge, seeds twice through the owner, and runs integrity through both owner and API credentials.
- Seed now requires an explicit owner/API pair, validates same target and distinct usernames, verifies its connected owner identity, and never connects through the API URL.
- Verify and live-browser wrappers validate API and live triples before any install/import/connection. The live wrapper uses owner only for inactivity/reset, launches FastAPI with only runtime `DATABASE_URL`, and supplies no database URL to Next.
- Playwright child environments are allowlisted rather than copied wholesale. The live SQL assertion now records `current_user` and `session_user`, requires both to match the runtime URL username, and proves they differ from the owner username supplied separately without an owner URL.
- CI now generates two `uuid4().hex` database names, provisions/validates distinct `ojcc_migrator`, `ojcc_api`, and `ojcc_app` role profiles plus exact membership options with quoted identifiers, and exports API/live credential triples. The npm, pip, and Next.js cache blocks remained unchanged.
- RED evidence: seed rejected the new owner argument as unknown; reset rejected the new bootstrap parameter as unknown; verifier/live wrappers rejected the new live bootstrap parameter as unknown.
- GREEN evidence: target/reset/verify combined gate 79 passed; demo reset/seed suite 37 passed; verify harness 18 passed; PowerShell parsers passed for all three scripts; Ruff passed; web ESLint passed; immutable hashes passed; CI YAML parsed successfully; `git diff --check` passed.

### Task 4 disposable database record

| Database | Disposition |
|---|---|
| `ojcc_task7_f585fe708c264a688483974180b88077` | replay path-resolution failure left open; zero sessions verified; dropped normally |
| `ojcc_task7_7ff50cac72074dea84ad1ec73221a4e3` | seed path-resolution failure left open; zero sessions verified; dropped normally |
| `ojcc_task7_6ae12dc483bf4f66a1485830a0296f88` | dropped normally after first complete three-target reset |
| `ojcc_task7_298073e99be2490c925fcfa62d5123c5` | dropped normally |
| `ojcc_task7_33f7a844be2e40a7912a019ab5143126` | dropped normally |
| `ojcc_task7_f68ca1d186d74945b4e4d51b77f74507` | dropped normally |
| `ojcc_task7_12148e5dda1548dc8f7591636431e9b9` | dropped normally |
| `ojcc_task7_f59807666475425688a69665dd9775f2` | dropped normally |
| `ojcc_task7_e67de74da66d434cb0e98be86a4553fb` | dropped normally |
| `ojcc_task7_9834bc9cb0c44bce974f995292502858` | dropped normally |
| `ojcc_task7_1bbe76c0dba94aca8b6185ecf53b3a2f` | dropped normally |
| `ojcc_task7_6a1d3128eac04829bed2b3018ee13d3b` | dropped normally |
| `ojcc_task7_e84e977cf3994f8c9c288963f9c71c62` | dropped normally |
| `ojcc_task7_b118a477e6cb4c77b8cb1eae16277840` | dropped normally |
| `ojcc_task7_655310a775dd4d5e8a857dec7e5baafa` | dropped normally |
| `ojcc_task7_6341e4a031444b6f8cf429c3a9a28728` | dropped normally |
| `ojcc_task7_b6892975ed6447b880c0610524bdd94c` | dropped normally |
| `ojcc_task7_3065ddaf82d24ca4bc557feaa3d57646` | dropped normally |
| `ojcc_task7_768d161bbac14876997c8659e19a446e` | dropped normally |
| `ojcc_task7_3b8c1d85782f4d5fa6794694c7f2f411` | dropped normally |
| `ojcc_task7_164b6f13a31344a59a222bf95a2e788f` | dropped normally |
| `ojcc_task7_f29844cfcae24eda854941f4244c9955` | dropped normally |
| `ojcc_task7_8a495fbc93314ac89dfcdebf53319ef4` | dropped normally |

## Task 5 — complete

- Added a real signed-cookie journey that connects every FastAPI session as `ojcc_api`, corrects a check-in, creates a proposal, acknowledges and resolves a safety signal, approves/claims/starts/completes the transportation task, records a supporting-actor response and Outcome, and reloads both audience-safe histories. Exact before/after row deltas prove all eight intended write capabilities while database and object ownership remain `ojcc_migrator`.
- RED evidence found an unapproved privilege dependency: approval and follow-up commands used `SELECT ... FOR UPDATE` on read-only aggregate roots and authority rows, which PostgreSQL correctly rejected for `ojcc_api`. Those false row locks now use stable transaction advisory locks on the proposal, reported need, and follow-up request; mutable `navigation_task` and `safety_signal` rows retain row locks.
- Added a complete negative SQL boundary: schema/function/table creation and mutation, temporary tables, role creation/grants/switching, every DELETE, every out-of-matrix INSERT, and every out-of-matrix UPDATE are denied. PostgreSQL's ungrantable object-level `GRANT` is a warned no-op rather than an exception, so its unchanged ACL is asserted directly. Catalog ACL and full relation digests remain unchanged after every group.
- Added open, assigned, in-progress, completed, and cancelled historical unbound-task coverage. 0007 preserves their exact rows and authorization/audit counts; integrity accepts the history; assigned/in-progress commands return `task_unbound` without a write; terminal rows remain readable; the existing atomic open-task claim contract remains covered.
- Replaced residual superuser-only trigger bypasses encountered in approval/concurrency regression tests with an owner-safe, identifier-validated user-trigger context. The committed closed-loop teardown also breaks the approved task/proposal cycle explicitly before ordinary scoped deletes.
- GREEN evidence: the required focused gate passed 52 tests; the approval/follow-up/advisory-lock regression gate passed 79 tests; Ruff, immutable hashes, and `git diff --check` passed.

### Task 5 disposable database record

| Database | Disposition |
|---|---|
| `ojcc_task7_296c649845a6400186a6d10c2e9cc105` | initial journey-shape RED; dropped normally |
| `ojcc_task7_918bea57cfd44232b21c7aa3d662e672` | intended non-owner lock RED; dropped normally |
| `ojcc_task7_476e2782b42d4e20b853c81962b52783` | timeline assertion RED; dropped normally |
| exact UUID hidden by pytest capture on one intermediate successful run | fixture reported ordinary drop; no database remained |
| `ojcc_task7_24332e1e33c145da986f4a3610ab6981` | production-session fidelity RED; dropped normally |
| `ojcc_task7_9124a2144fdb402f9964cc56748509fd` | dropped normally |
| `ojcc_migration_test_f84379e98dee4a23b65133318c54f482` | PostgreSQL object-GRANT semantics RED; dropped normally |
| `ojcc_migration_test_8284ef27d9e74d209cbdb5236837ca53` | composite-key negative-test RED; dropped normally |
| `ojcc_migration_test_dcaf646937604540b00b04b462db87f8` | dropped normally |
| `ojcc_task7_79d639e474dd4d4f93ae66cdb47f2add` | historical transaction-scope RED; dropped normally |
| `ojcc_task7_06b632079b384cea91661d48a545d8b3` | dropped normally |
| `ojcc_migration_test_286d0cb97ea84604906af6ff7368b52c` | dropped normally |
| `ojcc_task7_b7da247fe2454153b2b56384e9fb1923` | required focused gate wrapper; dropped normally |
| `ojcc_migration_test_850ccc53a12c42d29e131883ea66569b` | dropped normally |
| `ojcc_migration_test_129dfb09b267467b88408c4fff7bf766` | dropped normally |
| `ojcc_migration_test_63b11e85c3a4496ca3195f9e86385058` | dropped normally |
| `ojcc_task7_d82f4568687c43b8a68bb54ac9f83d16` | dropped normally |
| `ojcc_migration_test_94e5009427b74c2aa0ac5723857c48f5` | dropped normally |
| `ojcc_migration_test_f4d96655757d4bf2b55da3f8449a35a7` | dropped normally |
| `ojcc_migration_test_074add1411bd47b09c8adf91515d42b1` | dropped normally |
| `ojcc_migration_test_c5389a36458f4dbfa27faa4403eb34a9` | dropped normally |
| `ojcc_task7_f4dc052303544209ad4e472b624633ad` | regression collection RED; dropped normally |
| `ojcc_task7_b501cb97362b4a769c8ccb2455023cb4` | residual superuser-fixture RED; dropped normally |
| `ojcc_task7_9b713710523a490f890143ac1a939020` | cyclic teardown ordering RED; dropped normally |
| `ojcc_task7_3cf50914d7504376839935c5be4a74be` | cyclic teardown ordering RED; dropped normally |
| `ojcc_task7_7fdc159cd0904031a64553e5e250b80a` | cyclic teardown ordering RED; dropped normally |
| `ojcc_task7_3b61e5978ff243f2ba4bc3328af5bb49` | dropped normally |
| `ojcc_task7_de50c0928ce74c0eb51ceb06b50432f2` | final required focused gate wrapper; dropped normally |
| `ojcc_migration_test_99ddfac780934458bd40084cd374e131` | dropped normally |
| `ojcc_migration_test_fe6dca428a374cb0bc04533c461f14d9` | dropped normally |
| `ojcc_migration_test_72e59ac015c444b1aeb6d9a784980236` | dropped normally |
| `ojcc_task7_95baa38c3e264c9ebdbf9af51bb04206` | dropped normally |
| `ojcc_migration_test_7fd57f03148741fbbeb53e36aa527332` | dropped normally |
| `ojcc_migration_test_7cef6a77b28e4ae193d7f3441502182f` | dropped normally |
| `ojcc_migration_test_de65a063ccc642b0930897c78a31400b` | dropped normally |
| `ojcc_migration_test_fc0413e43cc14d92b193dac81f36d44a` | dropped normally |
| `ojcc_task7_1e82400ab48c47f4b39c2c00d8d4bcf3` | final approval/follow-up regression gate; dropped normally |

## Next exact step

Implement corrective Task 5 from the
[Database Privilege Closure Implementation Plan](../plans/2026-09-12-database-privilege-closure-implementation.md):
consolidate fail-closed role provisioning into one callable command used by local instructions and
CI, then prove creation, idempotence, and drift refusal behavior. Push and PR remain after the
corrective verification gates and independent review; merge, deployment, worktree removal, and
deferred milestones remain out of scope.

### Pre-PR corrective design — implementation pending

- Two gap closures: reject every unexpected outgoing role membership and include sequences and
  foreign tables in the application catalog boundary.
- Two new capabilities: fail-closed runtime attestation at startup and execution-time preflight
  for offline SQL. Runtime attestation is a startup snapshot; `/health` remains liveness only.
- Fresh offline execution is an owner/bootstrap/owner three-stage bundle, with ownership transfer
  inside the bootstrap transaction. Atomicity is per stage, not across the whole bundle. Raw fresh
  Alembic SQL is inspection-only; the supported bundle must be executed in tests.
- Supporting work: freeze the v0007 contract and share one provisioning script between local
  instructions and CI. Keep independent security-test expectations.
- Require real sequence/foreign-table refusal tests without conditional skips, startup-process
  tests, and version/ACL preservation assertions. Cluster-wide role fixtures require isolation.
- This document revision changes no application code or database state. All Task 6 evidence below
  predates these corrective requirements; the new tests and final gate have not yet been run.

## Corrective Task 1 — complete

- Added the connection-free, versioned `v0007` contract and an independently literal-pinned unit
  expectation for its application relation surface.
- Migration 0007 now validates the complete outgoing membership rows for the API login,
  `ojcc_app`, and migration owner. The only accepted row is API → `ojcc_app` with INHERIT true,
  SET false, and ADMIN false.
- The application catalog scan now consumes exactly relation kinds `r`, `p`, `v`, `m`, `S`, and
  `f`, compares `(name, kind)` pairs, retains extension dependency exclusion, and reports the
  unexpected object and kind with an operator review remedy.
- RED evidence: the contract test failed because the versioned module was absent; an all-options-
  false extra API membership upgraded successfully; and a mutation omitting sequence/foreign-table
  kinds let an API-granted sequence upgrade successfully.
- GREEN evidence: the focused contract and privilege gate passed 20 tests in 46.19 seconds; Ruff
  passed. The three membership cases and real sequence/`postgres_fdw` foreign-table cases each used
  a fresh recorded disposable database and were dropped normally.

## Corrective Task 2 — complete

- Migration 0007 now branches explicitly between online catalog reads and offline PostgreSQL `DO`
  assertions. Raw `head --sql` generation uses no connection and keeps role identifiers as quoted
  literals without rendering either credential password.
- The offline assertions cover required role profiles, the exact outgoing membership graph,
  current owner identity, database/schema/object ownership, the six-kind relation set, and the
  complete function set before the first 0007 ACL change.
- Real execution of the generated `0006:0007` artifact reaches head for a valid database. Extra
  membership, sequence, and `postgres_fdw` foreign-table drift raise inside its transaction and
  preserve revision 0006 and the independently captured ACL snapshot.
- RED evidence: raw head generation failed by attempting `.mappings()` on Alembic's offline mock
  result. The first executing artifact then exposed a PostgreSQL `text` versus internal `char`
  comparison mismatch, which the execution test caught before completion.
- GREEN evidence: the final focused offline generation, membership-equivalence, execution, and
  downgrade gate passed 35 tests in 53.31 seconds; Ruff passed. Every new disposable database
  reported ordinary cleanup.

## Corrective Task 3 — complete

- Added a sanitized runtime attestation boundary selected explicitly for the frozen v0007 contract.
  It verifies connected identity/database, schema revision, login/group profiles, exact outgoing
  memberships, non-ownership, catalog shape, and effective database, schema, relation, column, and
  function privileges including inherited/PUBLIC exposure and grant options.
- FastAPI runs the snapshot once in its startup lifespan and disposes the engine on startup failure
  and shutdown. Module import and OpenAPI generation remain connection-free; `/health` remains the
  unchanged liveness response and is never served by a rejected process.
- The group receives read-only access to `alembic_version` so the runtime login can attest the exact
  schema revision without receiving owner credentials or any new write capability.
- RED evidence: the runtime attestation module was absent, owner credentials remained able to keep
  Uvicorn running, and the first valid attestation exposed that the runtime could not read the
  otherwise protected Alembic revision table.
- GREEN evidence: 13 focused direct and real-process tests passed in 58.03 seconds, covering valid
  startup, owner/bootstrap rejection, extra membership, direct relation/column/function grants,
  sanitized errors, health, connection-free import/OpenAPI, and the revised read-only relation
  matrix. Ruff passed after import normalization. Each disposable database reported ordinary
  cleanup.

## Corrective Task 4 — complete

- `scripts.replay_schema` now has an explicit `--sql-output-directory` mode that validates the
  credential triple without connecting and refuses non-head bundles or an existing output path.
- The generated manifest records database, role names, credential order, filenames, and exact
  starting/ending revisions without URLs or passwords. Each SQL file has one transaction and checks
  connected identity, target database, and starting revision before mutation.
- The bootstrap stage contains the established exact 0005 table/function/type ownership allowlist
  before commit. It never uses `REASSIGN OWNED`.
- RED evidence: the CLI rejected the new output argument. The first generated artifact test also
  caught an assertion that failed to account for deliberately quoted object identifiers.
- GREEN evidence: 12 replay and existing database-support tests passed in 21.23 seconds; Ruff and
  Pyright passed. Real empty-database execution reached an attested 0007, while deliberate
  stage-three sequence drift retained the committed 0005 stage and rolled back only stage three.
  Both `ojcc_task7_` databases reported ordinary cleanup.

## Task 6 — complete

- Documented bootstrap, migration-owner, application-login, and group-role provisioning; exact
  API/live target separation; the immutable-0005 bootstrap bridge; runtime secret isolation;
  provider credential management; and no-force cleanup. `.env.example` now contains only blank
  database URL slots and distinct synthetic local role examples.
- Reset replayed the API database to `0007_database_least_privilege (head)`, seeded twice with
  identical counts, and returned zero integrity violations as both owner and API login. Alembic
  `current` reported head and `check` reported `No new upgrade operations detected`.
- OpenAPI and TypeScript contracts were generated twice with stable hashes and no generated
  contract diff.
- The first complete regression exposed legacy tests that assumed the migration owner could use
  `SET ROLE` or the superuser-only `session_replication_role`. Deliberate invalid-row fixtures now
  use explicit bootstrap-only rollback connections; ordinary mutation/cleanup fixtures use the
  identifier-validated owner-safe user-trigger helper; and the guarded follow-up test connects
  through the actual API credential. App privilege assertions now match the approved direct-write
  matrix, where audit and workflow-transition history are read-only.
- Installing the locked web workspace exposed a Next.js `ProcessEnv` augmentation requiring an
  explicit `NODE_ENV` member. The Playwright child allowlist now includes that field without
  forwarding database or unrelated process secrets.
- Final complete `scripts/verify.ps1` gate: Ruff passed; Pyright reported zero errors; API integrity
  was clean; 520 API tests passed in 221.58 seconds; ESLint passed; 34 Vitest tests passed; the
  production Next.js build passed; 3 mocked Playwright journeys passed; real desktop and mobile
  cookie-authenticated browser-to-PostgreSQL journeys each passed; every pre/post live integrity
  audit reported zero violations.
- Final targeted security gate passed 20 tests: all six immutable migration hashes, complete
  relation/function/database/schema catalog coverage, role and ownership checks, populated
  upgrade/failure-atomicity/head/downgrade-refusal coverage, the real non-owner API journey, and
  the full negative direct-SQL boundary. Its four databases were all dropped normally:
  `ojcc_migration_test_d7bb965b84454f26847e0ea1036b61ba`,
  `ojcc_migration_test_6e746526035643d3b5e79261cfc40e7b`,
  `ojcc_migration_test_1905c9a6a59547dfade3595765a0c77f`, and
  `ojcc_task7_dab6e1ec0c8e4ab09a4aa78900de47a1`.
- Final catalog evidence: `ojcc_migrator` is LOGIN/INHERIT/CREATEDB and otherwise non-privileged;
  `ojcc_api` is LOGIN/INHERIT without CREATEDB; `ojcc_app` is NOLOGIN/INHERIT; none is superuser,
  CREATEROLE, replication, or BYPASSRLS. API membership is INHERIT true, SET false, ADMIN false.
  The group has 37 SELECT, 6 INSERT, 2 UPDATE, zero DELETE relation grants and one direct function
  EXECUTE grant. All 34 public tables are owner-owned. Runtime `current_user` and `session_user`
  were both `ojcc_api`.
- `git diff --check` passed before final cleanup. No persistent `ojcc` database was inspected,
  reset, or dropped; no session was terminated.

### Final-gate owned database record

| Database | Purpose | Disposition |
|---|---|---|
| `ojcc_demo_0a86123af492466481211bba59c36307` | API/reset/full pytest gate | owner and zero sessions reverified; dropped normally |
| `ojcc_demo_89395435125f41f8b22df3ff06182e28` | live desktop/mobile browser gate | owner and zero sessions reverified; dropped normally |
