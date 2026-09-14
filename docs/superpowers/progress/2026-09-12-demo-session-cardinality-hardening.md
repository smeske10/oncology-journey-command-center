# Demo-session/cardinality hardening progress ledger

**Created:** 2026-09-12, America/New_York
**Status:** Tasks 1–6 and the final review fix wave are complete. Fresh full security and desktop/mobile acceptance passed at repaired source `769eb577110fe34fe0f5507bce10189224259014` on 2026-09-14; awaiting user review. Earlier attempts below remain historical evidence.
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

The planning documents and Task 1 changes were committed as `d952728`. Task 1 did not access PostgreSQL. No credentials were printed.

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

The user accepted the Task 1 checkpoint and explicitly authorized Task 2. Tasks 3–5 remained pending.

### Task 2 — one cardinality-safe authority resolver

- Started from clean Task 1 commit `d952728` in the existing linked worktree. The repository-local
  PostgreSQL container could not start because loopback port 5432 was already held by a compatible
  repository PostgreSQL 16 service. The stopped container, empty volume, and network created by
  that failed bind were removed; the working service was not changed. All three synthetic roles
  from `.env.example` connected successfully without printing credentials.
- Disposable smoke GREEN: committed owner setup was visible to a runtime SELECT session in
  `ojcc_migration_test_e5f137d800c241eba795adf1670cf6f0`; **1 passed**, then ordinary drop.
- Overlap setup initially failed before resolver execution because the test inserted unlinked ORM
  objects in one flush; `ojcc_migration_test_aa974f0118a043f38b98fbe2e1b7beaf` was dropped. After
  correcting only fixture flush ordering, PostgreSQL accepted one finite and one open effective
  grant and the intended RED was missing `resolve_authority`; database
  `ojcc_migration_test_518085ba4c9d489dafd6d9dd48b43201` was dropped.
- Compiled-query GREEN: the literal SQL proves `[granted_at, revoked_at)` predicates and no
  `DISTINCT`/`LIMIT`; **1 passed** without a database. Role-interpreter RED was the missing
  `authority_from_row` interface; zero/one/many role cardinality then produced **4 passed**.
- Repository overlap GREEN: one SELECT delegates to the interpreter and raises the stable
  ambiguity error for multiple grant record IDs; **1 passed** in
  `ojcc_migration_test_bd1a8f7072a442398b635c215295642e`, then ordinary drop.
- Temporal/envelope RED: **12 passed, 1 failed** because an inactive user with one effective grant
  was authorized in `ojcc_migration_test_2101da68fdf14e13a755acd1519914a1`, then ordinary drop.
  Direct invalid-anchor cases also produced **4 intended failures**. Missing organization/user and
  inactive/unknown activity branches were added; the combined temporal/anchor GREEN was
  **17 passed** in `ojcc_migration_test_9eea0435fba447a19e249752e5e0ff70`, then ordinary drop.
- Database-error RED exposed the injected SQLAlchemy sentinel. Translation to
  `AuthorityDatabaseUnavailableError` from None then produced **1 passed** without a database or
  raw context.
- Forward-link RED: both legal overlapping finite-link shapes failed to raise ambiguity (**2
  failed**) in `ojcc_migration_test_adfe0f9f8768414bb963a8f668c7b2bc`, then ordinary drop.
  Record-ID and patient-ID aggregates produced **2 passed** in
  `ojcc_migration_test_fb994f5e1f304359a7d6a1730c6be412`, then ordinary drop.
- Reverse-link RED: an effective conflicting link held by an inactive user was not counted in
  `ojcc_migration_test_8d94bcfdc34e4388b6a71154adf08188`; **1 failed**, then ordinary drop.
  The independent reverse aggregate produced **1 passed** in
  `ojcc_migration_test_481bda78c4cb4ef18f7e22f098a67495`, then ordinary drop.
- Staff-link isolation was already correctly branched: **1 passed** in
  `ojcc_migration_test_91647d38204e415cbb8c87a3732fb404`, then ordinary drop.
- Async parity RED: valid authority passed while overlapping grants escaped as SQLAlchemy
  `MultipleResultsFound` (**1 passed, 1 failed**) in
  `ojcc_migration_test_b833912512b644e9bd30f2312f6598e2`, then ordinary drop. Delegating the
  wrapper to the shared statement/interpreter produced **2 passed**, exactly one awaited query
  each, in `ojcc_migration_test_02ec2f967da94678b357d7a175d80722`, then ordinary drop.
- Complete new authority suite: **31 passed** in
  `ojcc_migration_test_2ff663b65f8a43eea3133ed177a3b6ee`, then ordinary drop.
- Required two-file integration verification used an outer migrated database for the legacy
  rollback suite and a nested authority database: **37 passed, zero skipped**. Both
  `ojcc_migration_test_828a8048fac34728959eec41a1c54d5c` and
  `ojcc_migration_test_0ddd8e2180d44f4dbcc59c030eb908f2` were dropped normally. Persistent
  `ojcc` was not migrated, seeded, or used for test writes.
- Ruff and Pyright were clean before the final evidence update. No migration file changed and no
  migration 0008 was created.
- Fresh completion gate: the required two-file integration run produced **37 passed, zero skipped**
  using outer `ojcc_migration_test_50702354998c4c90a43b1aca99f71a37` and nested
  `ojcc_migration_test_5524492f83b247e78be9dccf7a26e369`; both dropped normally. Task 1
  regression suites produced **50 passed**; Ruff produced **All checks passed**; Pyright produced
  **0 errors, 0 warnings, 0 informations**; the immutable-byte guard produced **7 passed, 18
  deselected**; and `git diff --check` passed with only line-ending notices.
- Async parity tests were then moved, without behavior changes, into the existing identity
  integration file named by the approved plan. The focused refactor check produced **2 passed, 6
  deselected** in `ojcc_migration_test_ce6d86380f5846848951a5f1e835c163`, then ordinary drop.
- Post-refactor completion gate: the exact two-file patch produced **37 passed, zero skipped** using
  outer `ojcc_migration_test_328a66c0b37542ef88aa0bab8c4763d2` and nested
  `ojcc_migration_test_6db59ad9903e47ba9aa4afa3bde9bf17`; both dropped normally. Task 1
  regressions again produced **50 passed**; Ruff, Pyright, seven hashes, and `git diff --check`
  again passed with the same clean results.
- A read-only cleanup audit observed two pre-existing prefixed databases not emitted by this task,
  `ojcc_migration_test_1c7d3d26610c4b68ad94fb3fd9cb6a1d` and
  `ojcc_migration_test_e51d4f44b6a4475688c57eaaf82e4753`. Their ownership was not established,
  so they were left untouched. Every database emitted by Task 2 was dropped normally.

All named disposable databases were newly generated and dropped normally. The user accepted the
Task 2 checkpoint and explicitly authorized Task 3. Tasks 4–5 remained pending.

### Task 3 — explicit issuance and authority-bound reauthorization

- Explicit-issuance RED: two real HTTP cases, with the configured navigator reversed across UUID
  ordering and insertion ordering, both raised baseline `MultipleResultsFound` through org/role
  discovery. The intended RED used
  `ojcc_migration_test_7d6668b551e048bf947a466a60961b09`, which dropped normally. An earlier
  fixture iteration used `ojcc_migration_test_42030e106dfd4fd39d3592a11a9ab675`; its second case
  collided on a deliberately fixed test UUID, so that test-only setup was corrected and the
  database dropped normally.
- Explicit-issuance GREEN: the service now selects only the configured roster identity, resolves
  it through the Task 2 authority envelope, checks configured patient equality, and signs the
  resolved authority. Both navigator order cases passed in
  `ojcc_migration_test_91dbd2bd4a964e8d915a68389931eb46`; administrator and supporting-actor
  selection passed in `ojcc_migration_test_9b307e2343f44f029960ba89b052638d`. Both dropped
  normally.
- Simple revocation was existing baseline behavior: issuance 204, queue 200, committed role
  revocation, then exact no-longer-authorized 401 passed in
  `ojcc_migration_test_952aca5df9df4c3abead7537dd8bd867`, which dropped normally.
- Provenance RED: an adjacent replacement grant revived the old tuple-only cookie in
  `ojcc_migration_test_ad9b2df45b83459b8e199075ad807f06`; an adjacent replacement patient link
  did the same in `ojcc_migration_test_fd9d6621348149e6a4a3e9c343068ca1`. Both returned 200
  instead of 401 and both databases dropped normally.
- Version compatibility RED/GREEN: all ten version cases first failed on the absent
  `verify_session` interface. Version 2 is now emitted as an exact integer; missing, legacy,
  values 1/3, string `"2"`, boolean, null, list, and object versions all refuse. Focused GREEN:
  **10 passed** without PostgreSQL.
- Role-assignment claim RED/GREEN: missing, empty, malformed UUID, integer, boolean, null, list,
  and object `ra` values were initially ignored (**8 intended failures**). Tokens now require and
  decode a UUID string and preserve the issued grant ID. Focused GREEN: **9 passed** without
  PostgreSQL.
- Patient claim-shape RED/GREEN: the supporting-actor and staff matrix produced **17 intended
  failures, 9 passes** before strict role-specific parsing. Supporting actors now require valid
  `patient` and `pil` UUID strings; navigator and administrator tokens reject either field.
  Focused GREEN: **26 passed** without PostgreSQL. Issuance preserves issuer, audience, HS256,
  jti, time bounds, configured TTL, and the two-hour ceiling.
- Roster and current-authority binding: a roster change initially left the old cookie usable in
  `ojcc_migration_test_74f1d35810ff4454a93777fbbc7b5ea2`; it dropped normally. Actor-tuple-only
  current resolution still left both replacement cases RED in
  `ojcc_migration_test_7497991a56b649feac91d4069df30142`. Grant-ID comparison made the role
  replacement GREEN while link replacement remained RED in
  `ojcc_migration_test_99da91f2e12f459381805816242fc0bc`. Nullable link-ID comparison made both
  GREEN in `ojcc_migration_test_2f8edf7da16d4bedb2967e24cf498b84`. All dropped normally.
- Current requests now verify signed claims, match organization/user/role/patient to the current
  roster, execute exactly one fresh cardinality-safe authority resolution, and require exact
  actor/grant/link equality. A valid role in another organization does not interfere. Signed
  org/user/role/grant and patient/link drift all refuse. Post-issuance revocation and inactive-user
  cases passed; an ambiguity case first escaped as the stable internal ambiguity exception in
  `ojcc_migration_test_80042068c2fa4f3aae19a01c4ca60b8e`, then produced the exact 401 after narrow
  translation in `ojcc_migration_test_1fc1dd3acde14263a96834d01a461da5`. Both dropped normally.
- Removed both legacy discovery methods and the token-only `current_actor` parser after repository
  and test call-site searches were empty. Updated auth, identity, outcome, safety-signal,
  concurrency, and non-owner fixtures to create authority-bound version-2 tokens. Focused existing
  checks: auth/navigator **77 passed**; identity **8 passed** in
  `ojcc_migration_test_5290af82447a4f32bf47a5c2e2f349e0`; revoked outcome/safety/concurrency
  **4 passed** in `ojcc_migration_test_5c316748dce4461a915c86f931dddb45`; real non-owner journey
  **1 passed** in `ojcc_task7_64d670c88b134a46ba69b83c6e54ff15`. All databases dropped normally.
- Complete Task 3 suite: **114 passed, zero skipped**. The outer identity target
  `ojcc_migration_test_744320dbb6224f018f9b8e7c0bc12bc3`, nested authority target
  `ojcc_migration_test_1a898c4888f641429beceb964cae2490`, nested HTTP target
  `ojcc_migration_test_6036dbfbfdc4492f9a9a2fe970ef4385`, and non-owner target
  `ojcc_task7_401a1ea46d0b4284a16477c33ed92734` all dropped normally.
- Fresh pre-commit completion gate repeated the same **114 passed, zero skipped** result. Its outer
  target `ojcc_migration_test_840017c7de9643e09ae844f9973c2a3b`, authority target
  `ojcc_migration_test_8d8f490913d84863b7c6a19b7ac8c495`, HTTP target
  `ojcc_migration_test_d57bfc3c13cd418e8318a57a2834f854`, and non-owner target
  `ojcc_task7_5b3bac5ba48044e19848890d2f4f0ad3` all dropped normally.
- Independent read-only post-commit review found no Critical or Important issues and judged Task 3
  ready to proceed. Its two traceability findings were corrected: the Task 3 checklist/status now
  reflects completion, and the HTTP suite uses the plan's `ojcc_task7_` prefix. The reviewer also
  noted that ASGITransport does not exercise application lifespan; that process/startup boundary
  remains explicitly covered by Task 4's real-Uvicorn process suite rather than connecting the
  pre-import global engine to a non-disposable target here.
- Post-review completion gate again produced **114 passed, zero skipped**. Outer identity target
  `ojcc_migration_test_206a13a70fc548888bd8cd2e68d88a1e`, authority target
  `ojcc_migration_test_73bfacd04e0f481aa1326e1678ebc8c7`, HTTP target
  `ojcc_task7_b52214f206f84046b3f0326fbf324db4`, and non-owner target
  `ojcc_task7_b7893f82cd9e4d51a41c6129e410a40e` all dropped normally.
- Ruff and Pyright passed for the touched production and test files. All seven migration hashes
  exactly match the immutable ledger, no migration 0008 exists, no migration diff exists, and
  `git diff --check` passed with only Windows line-ending notices.

The user accepted the Task 3 checkpoint and explicitly authorized Task 4. Task 5 remained pending.

### Task 4 — stable refusal, sanitization and cookie preservation

- Missing/malformed roster HTTP preservation: the new `raise_app_exceptions=False` cases already
  returned the exact configuration 503 with no Set-Cookie (**2 passed**). A direct boundary test
  then produced the intended RED because the HTTP exception explicitly chained the internal roster
  error. The configuration mapping now suppresses that context and the focused gate produced
  **3 passed**.
- Ambiguous issuance RED/GREEN: the first fixture attempt used two open grants and PostgreSQL
  correctly rejected it under the existing partial unique index in
  `ojcc_task7_eb1a29ae609d4ff6849f6da4bb932b03`; it dropped normally. The corrected legal overlap
  (one future-ended, one open grant) produced raw HTTP 500 in
  `ojcc_task7_dfc6a39f7cf84129b418be972cf7de23`; it dropped normally. A narrow typed ambiguity
  translation then returned exact actor 503 with no cookie. Existing Task 3 ambiguity, revocation,
  inactive-user, roster-drift and replacement-authority cases preserve exact no-longer-authorized
  401 behavior.
- Database-failure RED/GREEN: injected issuance and reauthorization SQLAlchemy failures each
  returned raw 500 before their respective mappings. Issuance and current-request boundaries now
  catch only `AuthorityDatabaseUnavailableError`, return exact authentication 503, suppress
  context, and neither retry nor reuse the failed session. Sentinel coverage includes a synthetic
  secret, database URL, SQL, parameter and token across response text, captured logs and formatted
  traceback; none escape.
- Compatibility and routing preservation: missing/legacy/wrong-type token version, missing/invalid
  grant ID and invalid staff patient-link claims all stop before database access with exact invalid-
  session 401 (**6 passed**). The direct parser-boundary test first exposed explicit exception
  chaining and passed after suppression. Valid-but-wrong role remains exact 403 and an unknown role
  path remains 422 (**2 passed**) without production edits.
- Cookie and lifetime preservation: local and non-local cookies retain host-only scope, Path=/,
  HttpOnly, SameSite=lax, local-only absence of Secure, non-local Secure, and no Max-Age/Expires.
  Repeated issuance replaces the one cookie-jar entry. TTL 1 and 120 are accepted, the exact expiry
  boundary refuses, absence remains 30, 0/121/non-integer/blank return sanitized configuration 503,
  and the two-hour verification ceiling remains enforced. These checks were preservation GREEN and
  required no cookie or TTL production edit.
- Real-process coverage: Uvicorn passed runtime privilege attestation and served unchanged health,
  then a temporary revoked SELECT forced the real authority query to return the stable
  authentication 503 without a traceback, SQL, database name, or signing secret in server output.
  The privilege was restored and only the owned process was terminated. Cleanup was strengthened
  after review so setup, process-stop, and output-collection failures cannot bypass engine disposal.
  Fresh process targets `ojcc_task7_33e9a1dd8d934eaabcec7c578ce74231`,
  `ojcc_task7_f573ffa70f1844e5ba4f3740e1514461`, and
  `ojcc_task7_c2e99d69d44a4e26bb18eb53abe644f3` all dropped normally.
- Independent review initially found three Important issues: missing roster roles were classified as
  actor rather than configuration failures, reauthorization did not reject a missing organization,
  and process-test cleanup began too late. Failing HTTP tests reproduced the first two. The service
  now distinguishes `DemoActorConfigurationError` from `AuthorityUnavailableError`, both factories
  require the configured organization, the broad `LookupError` route catch is gone, and nested
  cleanup guarantees engine disposal. Focused re-review found no remaining Critical or Important
  issues and judged the task ready.
- The first combined completion gate produced **157 passed, 1 failed** because its configuration-
  import child resolved `app.config` from an older sibling worktree through the interpreter's
  installed path. Anchoring that child to this worktree's API directory fixed the harness without
  changing configuration behavior. The post-review final completion gate produced **160 passed,
  zero skipped**. Its authority target `ojcc_migration_test_51aaa036255f4baa9e519dab815f77cc`,
  HTTP target `ojcc_task7_63ab760cf89e494ca1df594243b5feb7`, and process target
  `ojcc_task7_708bd6c4352e400697a093ec4e015c99` all dropped normally.
- Ruff passed; Pyright reported **0 errors, 0 warnings, 0 informations**; the automated immutable
  guard produced **7 passed, 18 deselected**; all seven hashes exactly match the ledger; no 0008 or
  migration diff exists; and `git diff --check` passed with only Windows line-ending notices.
- A read-only post-suite audit found two zero-session `ojcc_migration_test_` databases owned by the
  bootstrap role that already existed before the final gate. They were not created by Task 4 and
  were left untouched under the unknown-target cleanup rule.

The user accepted the Task 4 checkpoint and explicitly authorized Task 5. Task 6 remained pending.

### Task 5 — seed intent and live configuration

- Starting-state guards: HEAD was `c5ba1c43fc844cfa94b7a6d279d8ecf9e301588f` on the
  required feature branch; the approved base remained an ancestor; the worktree was clean; all
  seven migration hashes matched this ledger; and no migration 0008 existed. The focused baseline
  for the three Task 5 files produced **56 passed in 54.80s**. Its six fresh targets
  `ojcc_task7_a02c136b234e4c45821ddcee0a4da5c8`,
  `ojcc_task7_77a459ccbbc14ef4996a1c6e1add4a79`,
  `ojcc_task7_4e1e0b6c57d04db283a5ceec87aa901d`,
  `ojcc_task7_8206e362b4d04b6aa77ac7b1f66831d7`,
  `ojcc_task7_fd5336ec74ff4ebe8d4d45247f1a0025`, and
  `ojcc_task7_e68f56fab4ed4eb2b03450360687fad2` all dropped normally.
- Actor-roster CLI RED/GREEN: after correcting an initial invocation that lacked the collection-only
  synthetic URL environment, the focused test failed because `--print-demo-actors` was unknown and
  both database URL arguments were still required (**1 failed**). The pure roster helper and early
  exit then produced **1 passed** with engine creation forbidden, exact literal actor/patient UUIDs,
  sorted JSON, empty stderr, and no URL requirement or connection attempt.
- Deterministic identity RED/GREEN: the first fixture design tried to update a fully referenced seed
  row and was rejected by the existing approval foreign key before testing seed behavior. Its fresh
  target `ojcc_task7_0b390b698c4c41dd82aef25081c4dc4f` was reported in use by helper cleanup after
  the failed process, then ordinary-dropped once that owned process exited; no force or session
  termination was used. The corrected minimal fixture reused the intended navigator assignment ID
  for the wrong user. Without preflight, seed entered mutation and failed later at the approval
  foreign key instead of sanitized preflight refusal (**1 failed**); target
  `ojcc_task7_a16b1fb8d43e484ba2bfa9ffd1794220` had the same safe ordinary-drop disposition. The
  read-only preflight moved before trigger changes/inserts and then produced **1 passed** with the
  complete database digest unchanged and no trigger-toggle call; target
  `ojcc_task7_88b12c63d32d4b0882106a840a3e5385` dropped normally.
- Exact comparison expansion: separate wrong-user, wrong-organization, wrong-role,
  patient-link-user, and patient-link-patient cases produced **5 passed, 38 deselected** without
  requiring broader comparisons. Fresh targets
  `ojcc_task7_2e87c873b57041ceb4a58acf8e37a2c1`,
  `ojcc_task7_1c824cd26d8146768c1e04440e69045b`,
  `ojcc_task7_405f738762614939a506245276c2b5ee`,
  `ojcc_task7_3719e28e53894ddd8357860ed8f273d7`, and
  `ojcc_task7_5b84ac39012b4ad7a79f73638e14c7ca` all dropped normally.
- Non-repair preservation: inactive intended user, revoked intended grant, and revoked intended
  patient link cases were already non-repairing and produced **3 passed, 43 deselected**. Each
  retained the full database digest and exact history count with no replacement authority. Targets
  `ojcc_task7_93ab41741c23401da67fc3dd94e7a002`,
  `ojcc_task7_8c9e772c3a52491abd44edad5fa8e06c`, and
  `ojcc_task7_b733b4b46ce0484280ba0e424415ee6f` dropped normally. Repeated normal seed and
  completed-journey reseed preservation then produced **2 passed** with full digest/history checks;
  targets `ojcc_task7_cdbec7191d474d2694f75383cdef6ab6` and
  `ojcc_task7_764938ad67c249d1b3299ba8661551ac` dropped normally.
- Initial live child propagation cycle: evaluation of the transpiled Playwright configuration
  produced **1 failed** because the FastAPI child configuration lacked `DEMO_ACTORS_JSON`. The live
  wrapper then obtained the sorted roster through the connection-free CLI and the configuration-key
  test produced **1 passed**. Independent review later established that this test inspected only
  `webServer.env` overrides, not Playwright's effective child environments, so that earlier GREEN
  did not prove the claimed FastAPI-only process boundary.
- Environment restoration RED/GREEN: prior-roster probes for both success and a forced child
  failure produced **2 failed** because the live wrapper leaked its replacement value. Adding the
  roster to the existing exact save/remove/restore list produced **2 passed**.
- Non-owner journey: the fixture now supplies the serialized generated roster through explicit
  settings and the real issuance/reauthorization service factories. It asserts version 2 plus exact
  role-assignment and patient-link provenance. The focused journey produced **1 passed in 6.21s**;
  runtime `current_user` and `session_user` remained `ojcc_api`, and fresh target
  `ojcc_task7_ee587354c92f4625a1c84c65b3082860` dropped normally.
- Complete Task 5 focused gate: `test_demo_seed.py`, `test_verify_harness.py`, and
  `test_non_owner_closed_loop.py` produced **67 passed in 96.28s**, zero skips. Its fourteen fresh
  targets `ojcc_task7_fb89dfa87cc045a79c4856ff1fbbea06`,
  `ojcc_task7_7cfd7c3fc903488bb68f018cb4c8e6a7`,
  `ojcc_task7_d954d70fc15d446cb432b00c6e61f622`,
  `ojcc_task7_922772bf8cac47d9b20512fe33f124ee`,
  `ojcc_task7_5fb5d9669b8a45b5b4ac16e8ad67b393`,
  `ojcc_task7_80f756ea13bb4731b5fa9b84d4e408bd`,
  `ojcc_task7_c62f48e459b34eed95b8731203127b6e`,
  `ojcc_task7_ca92c51980474fb9a5273d3e5ed12fb7`,
  `ojcc_task7_d4967c7248b24246b2ef6b0ba63f8337`,
  `ojcc_task7_c5af9d8222ab4177aada0d86b4173e1c`,
  `ojcc_task7_182be607e90a4041a6d0f9a67dd1efb5`,
  `ojcc_task7_35853bf7a9b3414899f57664c82a5b86`,
  `ojcc_task7_98d605cb01f74faf9fa3c7a80412a77c`, and
  `ojcc_task7_083c2efd0ce24ec697ac1328d34d5031` all dropped normally.
- Fresh pre-commit rerun after the test-fixture clarity and documentation edits produced **67 passed
  in 90.00s**, zero skips. Its fourteen fresh targets
  `ojcc_task7_fee21417b4db40c491504f69bb8450c1`,
  `ojcc_task7_c37c389fbb604eb88ec9db747bf6985a`,
  `ojcc_task7_8224cfd018494e83bab2f49d427023f9`,
  `ojcc_task7_1b4e8491b0114f5dad14f85dd44e6127`,
  `ojcc_task7_5ac3fdde7d7c4a68bb0e5dc75cf474a7`,
  `ojcc_task7_d5a7cb67e534454e9b279926d7bc1902`,
  `ojcc_task7_51651f435d7a479d83adcbbc96b79846`,
  `ojcc_task7_baa16c9dc52a488c838c38fc95ec104b`,
  `ojcc_task7_bc35895a311f429fa3a0c54e310e1112`,
  `ojcc_task7_474f7a8fa5574c4f87c6e722222df3eb`,
  `ojcc_task7_b50a5457fa2f4535aa2d97881eb20206`,
  `ojcc_task7_2797258010044fef9e1fcff03b5a1941`,
  `ojcc_task7_f0ccec79bdb749fe9dc2122af9ea0c4a`, and
  `ojcc_task7_0d3d03405c234fb0bd114b46fa477ae9` all dropped normally.
- Static and preservation gates: web lint passed; all three verifier/reset PowerShell scripts parsed;
  Ruff passed; Pyright reported **0 errors, 0 warnings, 0 informations**; the automated immutable
  guard produced **7 passed, 18 deselected**; all seven hashes exactly match the ledger; all three
  CI cache guards remain and the CI workflow is unchanged; no dependency declaration, schema,
  privilege, migration, or domain behavior changed; and `git diff --check` passed with only Windows
  line-ending notices.
- Post-review effective-environment RED/GREEN: a harmless child launched through the installed
  Playwright `WebServerPlugin` first produced **1 failed in 1.31s**. The API child received
  parent-only synthetic bootstrap, migration, base-URL, device, migration-username, and API-origin
  markers; the launcher would also expose the inherited application URL, session secret, and actor
  roster to Next. The probe emitted booleans rather than environment values, accessed no database,
  and its owned child was stopped in `finally` through Playwright teardown with a bounded self-exit.
  Each live server environment now removes every inherited key before adding approved platform and
  child-specific values. The same effective-launch probe then produced **1 passed in 1.38s**:
  FastAPI received the exact configured application URL, local environment, session secret,
  organization, and roster, while Next received only its intended API origin from the tested live
  variables and neither child received bootstrap/migration URLs. The restoration, configuration-key,
  and effective-launch set subsequently produced **4 passed in 3.80s** on success and forced-failure
  paths.
- The complete post-review Task 5 gate produced **68 passed in 91.78s**, zero skips. Fresh targets
  `ojcc_task7_5a6556764367493fad8080561fc0a1e6`,
  `ojcc_task7_10919c2e9ec744d38fc7f1ff690e013e`,
  `ojcc_task7_a3fd4b2870644d66b8102be61db4dd21`,
  `ojcc_task7_544e9cb70210484c9a8ec29f72e765e5`,
  `ojcc_task7_c060c39f959947bba0685f0471b0acb4`,
  `ojcc_task7_50459928199a4115833c39d30c5639bf`,
  `ojcc_task7_43c210db031a4a3e974e3b95b8e66cfe`,
  `ojcc_task7_ccce9886ded743a991fb74a83aeb0c77`,
  `ojcc_task7_cfa09b9f223944de9617508dc3e79ee9`,
  `ojcc_task7_1070f9f748be4861a3761d1af444b24f`,
  `ojcc_task7_c490169a85b442beb1da724ae85d67cc`,
  `ojcc_task7_69a92aab104448378765705a0a150a21`,
  `ojcc_task7_835415beea814dbdbe560c96a713ffc9`, and
  `ojcc_task7_22887d92425f4c35aebaad2a7070face` were all created through the existing
  helper and dropped normally.
- A fresh completion rerun after adding the explicit owned-child stop assertion produced **68 passed
  in 91.24s**, zero skips. Fresh targets
  `ojcc_task7_fa45f9e59aed41d28034a13e32744686`,
  `ojcc_task7_0683a41a990a42e9aa8f3dd7bae34def`,
  `ojcc_task7_7a390ae5f3914c47a7598cfd32aad451`,
  `ojcc_task7_4b3c81c458ea4862afeedc3188975bb7`,
  `ojcc_task7_072a3464300a4bd281a2206503834dad`,
  `ojcc_task7_ffbbc030f2374d74b744652e583fbd63`,
  `ojcc_task7_d50507a0768347ffba974653dee2f8eb`,
  `ojcc_task7_1a218ae196224de39cd457b3033564e1`,
  `ojcc_task7_4fbd23f119484219b4871caeb01579a0`,
  `ojcc_task7_7f40432ca8ed43929ccf0f0ff1edb09b`,
  `ojcc_task7_9e1a4e86db274676ba95892339a0746a`,
  `ojcc_task7_a02f8fcb0c65430eb535acb0000fefe8`,
  `ojcc_task7_467d34836f8242cfa380b2974f00c859`, and
  `ojcc_task7_2a813c8837394c968b3ddfeab1c6dd6b` were all created through the existing
  helper and dropped normally. No database was created or accessed by the launcher probe itself.
- Post-review static/preservation verification passed: web lint had zero warnings/errors; all three
  PowerShell files parsed with zero errors; Ruff reported `All checks passed!`; Pyright reported
  **0 errors, 0 warnings, 0 informations**; the immutable guard produced **7 passed, 18 deselected**;
  all seven hashes remained exact; the cache guard produced **1 passed**; all three CI cache blocks
  remain; no migration 0008, migration diff, CI change, or dependency change exists; and
  `git diff --check` passed with only line-ending notices.

## Task 6 complete security and journey acceptance

- The first acceptance attempt at `96c6f66` correctly stopped when the complete verifier exposed
  TypeScript error TS2352 in `apps/web/playwright.live.config.ts`; no source was changed in that
  acceptance attempt. API/security tests had produced **723 passed** both standalone and inside the
  verifier, Vitest produced **34 passed**, and the ordered verifier stopped at the production build
  before mocked or live browser tests. Fresh API/live targets
  `ojcc_demo_6e6c0ac8e8104051a35ceb7030a9fe86` and
  `ojcc_demo_9620a684da7c4371a110a8e4fd09797c` were each verified as owned by
  `ojcc_migrator` with zero sessions, ordinarily dropped by that owner, and confirmed absent.
- The blocker was independently fixed and reviewed in `868f630` (`fix: type isolated Playwright
  environments`). Task 6 restarted from the beginning at that exact HEAD and did not modify the
  corrected source.
- Before second-attempt creation, fresh distinct API/live names
  `ojcc_demo_cd0f3599aed3471f8eedbaa039cfd75a` and
  `ojcc_demo_ecf316d6b2f44dbdb692ed72aff8eac5` were recorded, confirmed absent, and then created
  through the documented local service with owner `ojcc_migrator`. Existing bootstrap,
  migration-owner, application-login, and group-role properties were validated; credentials stayed
  separate and were not recorded. Both targets had zero sessions after creation.
- The API target reset used the bounded bootstrap/owner replay bridge, seeded twice with identical
  inventories, and returned zero integrity violations under both owner and API credentials. The
  roster came from the connection-free seed CLI; API tests received the explicit roster,
  organization and session settings.
- Fresh standalone API/security verification produced **723 passed in 472.82s**, zero failures and
  zero skips. It includes all new malformed-config/auth/token/resolver/HTTP/Uvicorn/process and
  actual Playwright-launcher isolation cases plus the existing privilege, runtime-attestation,
  real non-owner, identity/history, restore-integrity, seed, migration, offline replay,
  provisioning, and target-safety suites.
- Alembic reported `0007_database_least_privilege (head)` and `No new upgrade operations detected.`
  The frozen v0007 test produced **2 passed in 0.18s**. All seven migration SHA-256 values matched
  the immutable baseline above, and no migration 0008 exists.
- OpenAPI and TypeScript contracts were generated twice using the checked-in scripts. Both second
  generations were byte-stable; Git blob hashes matched `HEAD`; generated diff was empty; and the
  internal names `VerifiedDemoSession`, `ResolvedAuthority`, and `CurrentActor` were absent. The
  resulting raw SHA-256 values were
  `56eb4aab2d0e650e121091be68737cc12b8dcd5254814ecaa1ddd1767b106882` for OpenAPI and
  `9c46a9cb6a2654ed0e41a964fd86d7f9479ccd5874ef5f0adff188bcf70ddc46` for TypeScript.
- The exact complete repository verifier ran uninterrupted from the repository root with the API
  credential triple exported and the separate live triple supplied through the four required live
  arguments. Locked dependency installation passed; Ruff passed; Pyright reported **0 errors,
  0 warnings, 0 informations**; the API runtime integrity audit reported zero violations; pytest
  produced **723 passed in 466.64s**; web lint passed; Vitest produced **6 files and 34 tests passed
  in 1.80s**; the Next.js production build completed compilation, TypeScript checking, static page
  generation, optimization and trace collection; and mocked Playwright produced **3 passed in
  11.0s**.
- The same root verifier reset the live target independently for each viewport. Each reset seeded
  twice with identical inventories and returned zero owner/API pre-run integrity violations.
  Desktop Playwright produced **1 passed in 13.9s** and mobile Playwright produced **1 passed in
  13.8s**. Both journeys selected exact evidence, approved the transportation proposal, claimed,
  started and completed its task, saved the patient resolved follow-up, refreshed the navigator
  workspace, previewed and confirmed the resolved Outcome, removed the closed need from the queue,
  then reloaded navigator and patient pages to prove persisted need, response and Outcome history.
  Mobile additionally verified a sub-600-pixel viewport without horizontal overflow.
- The live test's database assertion ran through the application URL and required
  `current_user=session_user=ojcc_api`, distinct from `ojcc_migrator`; it proved exactly one
  follow-up request, response, Outcome and schema-version-2 proposal binding plus at least three
  task audits. A fresh final read-only API-login query confirmed those four counts at one, exactly
  three task audits, `navigation_task.status=completed`, and the same runtime identity. Fresh final
  integrity audits of both named API/live targets again returned zero violations.
- Task 6 made no workflow, migration, dependency, contract, privilege, target, test, configuration,
  or production-source change. Diff from the acceptance base was empty for every protected source
  category; `git diff --check` passed; ports 8011 and 3011 were free; and generated build/line-ending
  side effects had the exact `HEAD` blobs before documentation edits. The privilege, attestation,
  target-safety, provisioning, CI cache and child-environment guards all ran inside both 723-test
  passes. No credential, raw SQL parameter, cookie, signing secret, token, or traceback was added to
  tracked output or documentation.
- Before cleanup, both second-attempt targets were revalidated against the exact recorded UUID names,
  owner `ojcc_migrator`, and zero sessions. Both were ordinarily dropped by `ojcc_migrator` and a
  final catalog query confirmed both absent. No force-drop, connection termination, unknown-process
  stop, persistent `ojcc` access, push, merge, deployment, worktree removal, or deferred milestone
  occurred.
- Intentional limitations remain those in the approved design: this synthetic demo does not claim
  database-wide temporal exclusion, implicit actor discovery, seed repair, persistent cookie
  expiry, new workflow/provider/admin UI, real-data or clinical use, external booking/outreach, or
  deployment readiness. Deferred milestones remain deferred.

**Next exact step:** User review of the completed Task 6 evidence and documentation commit. Do not
merge, push, deploy, remove the worktree, or begin a deferred milestone without separate direction.

## Conditional-approval amendment

The user approved the architecture and retained the explicit actor roster, strict cardinality,
authority-bound version-2 tokens, controlled legacy-token 401 behavior, and no-migration-0008
boundary. At this historical checkpoint, implementation remained unauthorized until the amended
plan was reviewed; the later Task 1 and Task 2 execution records supersede that status.

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

**Historical next step (superseded by Task 6 and the final fix wave below):** Report the Task 5
execution checkpoint and wait for explicit authorization before Task 6.

## Final whole-branch review fix wave — 2026-09-14

Review of `0a3cda8..2536b2a` identified two remaining safeguards: root frontend children inherited
API configuration, and an unlinked deterministic patient in a different organization reached seed
mutation. Both were corrected in `769eb577110fe34fe0f5507bce10189224259014`
(`fix: close final demo acceptance gaps`). This commit changes exactly `scripts/verify.ps1`,
`services/api/scripts/seed_demo.py`, and their existing seed/verifier test files.

The root frontend block now saves, removes, and restores the six API-only environment variables
in `finally` before the live wrapper and on frontend failure. The regression executes all four
root web command children and a harmless mocked Next child through the installed Playwright
launcher, checking effective absence plus exact restoration on success/failure. The existing
live isolation and all three CI cache blocks are unchanged. Seed preflight now SELECTs the
deterministic patient's organization: absence or the intended organization is allowed; mismatch
raises the existing stable sanitized conflict before any insert or trigger change. It adds no
user primary-organization condition and performs no repair.

### Failing-first and focused evidence

- Exact RED/GREEN command from `services/api`:
  `python -m pytest tests/test_verify_harness.py::test_root_frontend_effective_environments_and_restoration tests/test_demo_seed.py::test_seed_refuses_wrong_organization_for_intended_patient_before_mutation -q -s --tb=short`.
- Intended RED before production edits: **4 failed in 10.66s**. Present-value root cases observed
  all six variables in lint/Vitest/build/mocked and mocked Next children; the absent-demo failure
  case still observed the three database variables. Output contained presence booleans only.
  Patient preflight observed `digest_unchanged=True`, `trigger_changes=[False]`,
  `database_error=True`, and failed the sanitized-refusal assertion. Its fixture contained only
  another valid organization and the deterministic patient, with zero role/link rows.
- GREEN: **4 passed in 10.62s**. Patient pre/post full database digests matched with
  `trigger_changes=[]`, `database_error=False`, and the exact stable sanitized refusal. RED
  fixture `ojcc_task7_de7a98106c18454a8b64df49fa84b5d9` and GREEN fixture
  `ojcc_task7_c9b2a631b24f4fd48aba7adda04751fa` were ordinarily dropped by the owned helper.
- Initial fixture development omitted required demographics and failed during setup; that is
  not RED evidence. Its `ojcc_task7_0ca562d170a74d09a966eb8dcde82d44` fixture was ordinarily
  dropped. A later mismatched synthetic base-name invocation failed target validation before
  creation; the corrected run supplied one shared base name.
- Complete focused seed/verifier/frozen selection: **73 passed, 25 core-migration cases
  deselected in 91.47s**. Separate immutable hashes: **7 passed**; frozen v0007: **2 passed**;
  three-cache guard: **1 passed**. Full Ruff/Pyright, all root PowerShell parsing, and diff checks
  passed. Pre-commit web lint, **34 Vitest**, production build, and **3 mocked Playwright** passed.

### Entire Task 6 rerun at repaired source HEAD

Two new UUIDv4 names were generated and recorded before creation, confirmed absent, provisioned
with distinct bootstrap/owner/runtime credentials, and verified as owned by `ojcc_migrator`
with zero sessions. No previous acceptance target or persistent `ojcc` was accessed.

| Purpose | Fresh exact name | Final disposition |
|---|---|---|
| API | `ojcc_demo_77dfdec72f444052b47b12b908cc2646` | Owner verified; 0 sessions; ordinary owner drop; absence confirmed |
| Desktop/mobile live | `ojcc_demo_b856543e2455487d8a31432b3d74f369` | Owner verified; 0 sessions; ordinary owner drop; absence confirmed |

The API reset/replay reached head, two seeds produced matching inventories, and owner/runtime
integrity audits were clean. Standalone `python -m pytest -q -ra`: **727 passed in 443.67s**, with
zero failures/skips. This includes every new security case and the complete existing privilege,
runtime attestation, history, seed, migration/replay, provisioning, and target-safety suites.
Alembic current was `0007_database_least_privilege (head)`; check found no upgrade operations.
All seven original SHA-256 values matched and the frozen contract suite passed **2 tests**.

OpenAPI and installed TypeScript generation each ran twice, remained byte-stable, and produced no
Git drift or internal auth model names. Raw hashes remain OpenAPI
`56eb4aab2d0e650e121091be68737cc12b8dcd5254814ecaa1ddd1767b106882` and TypeScript
`9c46a9cb6a2654ed0e41a964fd86d7f9479ccd5874ef5f0adff188bcf70ddc46`. An initial supplemental
export invocation used the wrong cwd and failed before executing the script; the ignored runner's
cwd/console encoding were corrected and the full migration/contract group passed. No source
defect or database mutation resulted from that invocation error.

The exact root verifier ran uninterrupted with the API triple exported and the distinct live
triple supplied through `LiveBootstrapDatabaseUrl`, `LiveMigrationDatabaseUrl`,
`LiveDatabaseUrl`, and `LiveConfirmDatabaseName`. It exited 0 after **531.33s**.

| Root gate stage | Result at `769eb57` |
|---|---|
| Locked Python install and editable no-dependency install | Passed |
| Ruff and Pyright | Passed; 0 type errors/warnings/informations |
| Runtime integrity | 0 violations |
| Complete API/security pytest | **727 passed in 443.28s**, no failures/skips |
| Web lint | Passed |
| Vitest | **6 files, 34 passed in 1.70s** |
| Next production build | Compile, types, static generation, optimization and traces passed |
| Mocked Playwright | **3 passed in 6.9s** |
| Desktop live | **1 passed in 13.1s** |
| Mobile live | **1 passed in 12.8s** |

Each live viewport reset/double-seeded the owned live target, passed owner/API pre-audits, and
completed the real signed-cookie story: exact evidence → approve → claim → start → complete →
patient resolved follow-up → navigator refresh → outcome preview/confirmation → reload persisted
navigator and patient history. Mobile also checked viewport width and horizontal overflow. Both
post-journey runtime audits returned zero violations. Live assertions checked the real non-owner
login and exact persisted counts, without overriding application authorization.

Fresh final runtime queries on both targets returned `current_user=session_user=ojcc_api`,
database owner `ojcc_migrator`, and public table owner `ojcc_migrator`. The transportation story
had exactly one request, response, Outcome, and schema-version-2 binding; three task audits; and
completed task status. Fresh final API/live integrity audits returned zero violations. Ports
8011/3011 had no listener. Both targets were checked again for owner and zero sessions, ordinarily
dropped by the owner, and confirmed absent. No force-drop, unknown-session termination, unknown
process stop, or worktree removal occurred.

### Clarity, scope, and remaining limitations

The final stale Task 5 next-step text above is explicitly historical. The ignored Task 6 report
now labels its opening failure as the superseded first attempt and points to the latest fix-wave
report; the SDD controller summary records this completed fix wave and fresh acceptance.

During initial regression development, traversing the unchanged live wrapper with absent demo
values showed that it restores them as empty strings on this PowerShell runtime. That separate
pre-existing behavior is retained as an observation for review. Root present-value success and
failure restoration and root absent-value frontend-failure restoration are tested; full acceptance
supplied explicit demo values. No live-wrapper scope expansion was made.

No migration 0008, schema/privilege, dependency, CI, workflow/domain, or existing live-config change
was made. The design's prior intentional limitations remain in force. The successful runs at
`868f630` and the earlier blocked attempt remain historical; this fresh run is the latest source
acceptance. The final evidence commit changes only the approved plan, design, and this ledger.

**Current next exact step:** User review of the repaired source and fresh acceptance evidence.
Do not merge, push, deploy, remove the worktree, or begin a deferred milestone without direction.
