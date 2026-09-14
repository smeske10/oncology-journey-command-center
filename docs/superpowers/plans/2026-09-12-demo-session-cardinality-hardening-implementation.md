# Demo-session authentication and authorization-cardinality hardening implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` after user approval to implement this plan task-by-task. Do not delegate unless separately authorized. Checkboxes track completed work; Tasks 1–2 were implemented after their explicit execution approvals.

**Date:** 2026-09-12 (America/New_York); amended 2026-09-13
**Goal:** Make demo actor selection explicit and session authority unambiguous while preserving stored history and delivered journeys.
**Architecture:** A server-configured demo roster selects user identities. One shared SELECT-only resolver evaluates effective record cardinality; tokens bind exact qualifying records and requests revalidate them. Schema and frozen privilege contracts stay at 0007.
**Tech Stack:** Existing Python 3.12, FastAPI, SQLAlchemy 2, psycopg 3, PostgreSQL 16, pytest/httpx, PowerShell, Next.js and Playwright; locked dependencies unchanged.
**Spec:** [Design](../specs/2026-09-12-demo-session-cardinality-hardening-design.md)
**Evidence:** [Progress ledger](../progress/2026-09-12-demo-session-cardinality-hardening.md)

## Global constraints

- Work only in `.worktrees/demo-session-cardinality-hardening`, branch `feature/demo-session-cardinality-hardening`, based on `0a3cda82203d850992f2efc3653b653c8253459c`.
- Preserve migrations 0001–0007 byte-for-byte and the frozen v0007 privilege contract.
- No migration 0008, schema or privilege change; no dependency upgrade without separately approved demonstrated blocker.
- Never mutate persistent `ojcc`; use freshly generated, recorded UUID-suffixed disposable databases whose ownership is known.
- Never force-drop, terminate unknown sessions, reuse uncertain database names, or remove worktrees.
- Preserve bootstrap-only provisioning/0005 bridge, owner-only migration/seed, and runtime-only API credentials; preserve all target safeguards.
- Preserve authorization history; no deleting, reopening, backdating, fabricating, deduplicating, or choosing a winning record.
- No new workflow, provider, administrator UI, patient scenario, policy engine, agent, retrieval, orchestration, evaluation, deployment, or merge.
- Preserve cookie lifetime and flags. The approved token-format change invalidates legacy tokens through controlled 401.
- No PostgreSQL execution before this design/plan is approved. No push or PR creation is implied by approval to implement.

## Execution discipline and safety setup

Before every task, confirm feature HEAD/base ancestry and seven hashes against the ledger. Use exact invocation-scoped Git safe.directory if needed; do not change global configuration. Preserve the other worktrees and main checkout's untracked plan.

Tests needing PostgreSQL must use the existing `tests.database_support.disposable_database(prefix="ojcc_task7_", migrate_to="head")` helper on the isolated test PostgreSQL service. Existing allowed prefixes suffice; no helper target-policy relaxation. Supply explicit validated bootstrap/owner/runtime URLs in memory, all to the same newly named target; do not print them. For whole-repository testing, record two fresh `ojcc_demo_<uuid4 hex>` names, one API and one live, before creation; use existing provisioning and reset tools. Do not trust inherited URLs.

For focused auth integration tests, create/seed and commit fixtures with the owner, then issue real HTTP requests through runtime sessions. Separate owner transactions commit revocation/drift before the next request. Use lifespan-aware TestClient for the main route suite and real Uvicorn subprocess tests for startup/environment/log assertions. Override runtime URL/session construction if necessary; never override CurrentActor, ActorRepository, or DemoSessionService in the decisive tests. Existing mocked unit tests remain supplementary.

After each task: run focused tests, Ruff/types for touched code, seven hashes and diff checks; append actual RED/GREEN evidence and exact database dispositions to the ledger, review, then commit explicit task files. A baseline guard already passing is not RED evidence. All new behavioral regressions must fail for the intended reason before the change and pass after it.

## Task 1: Explicit roster and invariant interfaces

**Files**
- Create `services/api/app/auth/demo_actors.py`: strict roster parsing and configuration errors.
- Modify `services/api/app/auth/models.py`: internal authority/claim records; CurrentActor stays unchanged.
- Modify `services/api/app/config.py`: raw optional DEMO_ACTORS_JSON setting without import-time JSON parsing; import-safe TTL and organization parsing.
- Modify `services/api/app/api/demo_sessions.py` and `services/api/app/auth/dependencies.py`: translate invalid parsed settings at the service factories.
- Modify `services/api/tests/test_auth.py`; create `services/api/tests/test_demo_actor_configuration.py`.
- Modify `services/api/tests/test_core_domain_migration.py`: add immutable 0007 hash.

**Interfaces**
`DemoActorSelection(user_id: UUID, patient_id: UUID | None)`;
`parse_demo_actors(value: str | None) -> dict[Role, DemoActorSelection]`;
`DemoActorConfigurationError`.
`ResolvedAuthority(actor: CurrentActor, role_assignment_id: UUID, patient_identity_link_id: UUID | None)`.
`VerifiedDemoSession(authority: ResolvedAuthority)` for validated signed claims, distinct from database-validated authority.
`_optional_int_from_environment(name: str, default: int) -> int | None`; absence returns 30, blank/non-integer returns None. `Settings.demo_session_ttl_minutes` is `int | None`.
Keep token provenance out of CurrentActor passed to domain routes.

- [x] Add 0007 to the existing literal hash dictionary:
```python
"0007_database_least_privilege.py":
    "7bbe68eeb878fcb6418c62354e9ee323f46e1750ee36d293f69e978ae06018f7"
```
Run `python -m pytest tests/test_core_domain_migration.py -k immutable -q` from services/api. Expected seven passes, recorded as baseline guard.
- [x] Write a subprocess test that imports `app.config` with a valid synthetic DATABASE_URL and `DEMO_SESSION_TTL_MINUTES=not-an-integer`; assert exit 0, `CONFIG_IMPORTED` on stdout, and no malformed-value traceback.
- [x] Run `python -m pytest tests/test_demo_actor_configuration.py::test_non_integer_ttl_does_not_break_config_import -q`. Expected RED: module import raises from eager `int(...)`.
- [x] Implement `_optional_int_from_environment`: absent returns 30; blank or non-base-10 integer returns None without echoing input. Make optional UUID parsing return None for malformed input without echoing it.
```python
def _optional_int_from_environment(name: str, default: int) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip(), 10)
    except ValueError:
        return None
```
- [x] Rerun the import test. Expected GREEN: one pass.
- [x] Write route tests for TTL `None`, 0, and 121 plus malformed organization; each POST returns 503 `{"detail":"Demo sessions are not configured"}` and no Set-Cookie.
- [x] Run `python -m pytest tests/test_auth.py -k "invalid_ttl_configuration or invalid_demo_organization" -q`. Expected RED: the factory passes invalid state into construction or import fails.
- [x] Guard both service factories before `DemoSessionService` construction and translate only configuration failure to the existing sanitized 503. Rerun the same selection. Expected GREEN.
- [x] Write strict-parser tests for each valid role, a subset roster, duplicate keys at both levels, unknown role, wrong shape/type, invalid UUID, unexpected fields, absent JSON, and patient-field requirements. Explicitly allow a user intentionally selected for two different roles.
```python
def test_duplicate_role_configuration_is_refused():
    raw = '{"navigator":{"user_id":"00000000-0000-0000-0000-000000000001"},' \
          '"navigator":{"user_id":"00000000-0000-0000-0000-000000000002"}}'
    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)

def test_staff_configuration_rejects_patient_field():
    raw = json.dumps({"navigator": {"user_id": str(uuid4()), "patient_id": str(uuid4())}})
    with pytest.raises(DemoActorConfigurationError):
        parse_demo_actors(raw)
```
- [x] Run `python -m pytest tests/test_demo_actor_configuration.py -k roster -q`; record missing-interface RED.
- [x] Implement the types and parser using `object_pairs_hook`, exact key sets, UUID parsing, and stable exceptions from None. Do not echo input or fall back to a default organization.
- [x] Rerun the roster selection. Expected GREEN.
- [x] Run the full parser/auth suites, Ruff and Pyright for touched files, seven hashes, and `git diff --check`.
- [x] Update the ledger with each RED/GREEN result and commit `feat: define explicit demo actor configuration`.

## Task 2: One cardinality-safe authority resolver

**Files**
- Create `services/api/app/auth/authority.py`: shared statement builder/result interpreter and typed unavailable/ambiguous/database errors.
- Modify `services/api/app/auth/service.py`: SqlAlchemyActorRepository.
- Modify `services/api/app/auth/dependencies.py`: async resolver delegates to shared builder/interpreter.
- Create `services/api/tests/integration/test_demo_session_authority.py`.
- Modify `services/api/tests/integration/test_identity_pathway_submission.py`.

**Interfaces**
`build_authority_statement(*, organization_id: UUID, user_id: UUID, role: Role, at: datetime)` returns a SQLAlchemy Select.
`authority_from_row(row, *, organization_id, user_id, role) -> ResolvedAuthority | None` validates the documented envelope.
`resolve_authority(*, organization_id: UUID, user_id: UUID, role: Role, at: datetime | None = None) -> ResolvedAuthority | None` replaces org/role discovery in ActorRepository.
`AmbiguousAuthorityError` and `AuthorityDatabaseUnavailableError` carry only stable messages.
Preserve `resolve_patient_actor(...)->CurrentActor` as an async wrapper around the same authority rules; no new async service/runtime.

- [x] Create the disposable authority fixture with independently generated IDs, committed owner setup, runtime SELECT session, and one fixed UTC instant. Run a smoke case and record the fresh database's ordinary cleanup.
- [x] Write `test_overlapping_roles_refuse_authority` using one finite role spanning the instant and one open role. Do not disable constraints.
- [x] Run only that test. Expected RED: missing new resolver interface or baseline `MultipleResultsFound`; record that PostgreSQL accepted the legal overlap.
- [x] Add the desired assertion:
```python
with pytest.raises(AmbiguousAuthorityError):
    repository.resolve_authority(
        organization_id=organization.id, user_id=user.id,
        role=Role.SUPPORTING_ACTOR, at=checked_at,
)
```
- [x] Implement only `build_authority_statement` with a user/org anchor and effective role-ID aggregate. Add a literal compiled-query assertion proving `[granted_at, revoked_at)` predicates and absence of DISTINCT/LIMIT. Run it. Expected GREEN for query shape while the behavior test remains RED.
- [x] Implement only `authority_from_row` role cardinality: zero → unavailable, one → extract, more than one → `AmbiguousAuthorityError`. Run interpreter unit cases. Expected GREEN while repository behavior remains RED.
- [x] Add `resolve_authority` to execute the statement once and pass its row to the interpreter. Rerun the overlap behavior test. Expected GREEN.
- [x] Write temporal role tests for starts/ends exactly at t, future grant/revocation, expired and zero-length history, adjacent grants, disjoint role/org, inactive/missing user/org, and NULL/different primary organization.
- [x] Run the temporal-role selection. Expected RED where the role-only envelope is incomplete.
- [x] Add missing-user/org/inactive envelope branches to the interpreter; rerun those named temporal cases. Expected GREEN.
- [x] Add SQLAlchemy error translation in `resolve_authority` as `AuthorityDatabaseUnavailableError` from None; run one injected database-error test. Expected GREEN with no raw context.
- [x] Write `test_multiple_effective_links_refuse_authority` for two finite links to one patient and for two patients attached to one user. Run it. Expected RED: patient cardinality is not implemented.
- [x] Add an independent forward-link aggregate; count link record IDs rather than distinct actor tuples. Rerun the multiple-link test. Expected GREEN.
- [x] Write `test_reverse_patient_link_conflict_refuses_authority`, including a conflicting link owned by an inactive user. Run it. Expected RED: reverse conflict is not counted.
- [x] Add the independent reverse-link aggregate and require the selected link to be the only effective link for that patient. Rerun. Expected GREEN.
- [x] Write `test_staff_role_ignores_patient_links`; run it, then branch staff interpretation away from link requirements only if RED. Expected final GREEN.
- [x] Write async-wrapper parity tests for valid and ambiguous patient authority. Run them. Expected RED: the old async join still raises/misclassifies.
- [x] Delegate the async wrapper to the shared builder/interpreter with exactly one awaited query. Rerun async parity tests. Expected GREEN.
- [x] Run both complete integration files with `-q -s`, Ruff/Pyright on touched files, seven hashes, and `git diff --check`; no PostgreSQL case may skip.
- [x] Update the ledger with each named RED/GREEN and database disposition; commit `fix: reject ambiguous effective session authority`.

## Task 3: Explicit issuance and authority-bound reauthorization

**Files**
- Modify `services/api/app/auth/service.py`, `services/api/app/auth/dependencies.py`, `services/api/app/api/demo_sessions.py`.
- Modify `services/api/tests/test_auth.py` and new authority integration suite.
- Create `services/api/tests/integration/test_demo_session_http.py`.
- Update constructor/token call sites in existing auth, identity and non-owner journey tests.

**Interfaces**
DemoSessionService constructor gains `demo_actors: Mapping[Role, DemoActorSelection] | None`.
`create_session(role: Role) -> str` selects the explicit roster identity, resolves authority, compares configured patient, and signs it.
`create_token(authority: ResolvedAuthority, *, issued_at: int | None = None, expires_at: int | None = None) -> str` encodes ver=2, ra and conditional pil.
`verify_session(token: str, *, now: int | None = None) -> VerifiedDemoSession` replaces the old token-only current_actor method.
The HTTP current_actor dependency verifies claims, roster, and freshly resolved authority, then returns CurrentActor.

- [x] Write one real HTTP issuance test with two eligible navigators, configure the second, and assert 204 plus the configured token subject. Run it. Expected RED: baseline org/role discovery raises or selects ambiguously.
- [x] Reverse IDs/insertion order and rerun the same test. Preserve both cases so no ordered selector can satisfy the contract.
- [x] Implement only explicit roster selection and authority resolution in `create_session`; sign the returned authority. Rerun both issuance cases. Expected GREEN.
- [x] Repeat the explicit-selection test for administrator and supporting_actor, including configured patient equality. Run; implement only missing role-specific selection; rerun GREEN.
- [x] Write cookie-authenticated simple-revocation tests using these exact route expectations:
```python
assert client.post("/v1/demo/session/navigator").status_code == 204
assert client.get("/v1/navigator/queue").status_code == 200
# Owner transaction commits revocation of the issued grant here.
response = client.get("/v1/navigator/queue")
assert response.status_code == 401
assert response.json() == {"detail": "Demo session is no longer authorized"}
```
- [x] Run simple revocation. Existing behavior may pass; record it as baseline evidence rather than RED.
- [x] Write the adjacent replacement-grant test: old cookie remains 401 after a new grant ID becomes effective; fresh issuance works. Run it. Expected RED: tuple-only token revives.
- [x] Write the equivalent patient-link replacement test and run it. Expected RED for the same missing provenance binding.
- [x] Write failing token tests for `ver`: missing, legacy, values 1/3, and wrong types `"2"`, bool, null, list, and object. Run `python -m pytest tests/test_auth.py -k token_version -q`. Expected RED: missing/legacy claims are accepted or parser interface is absent.
- [x] Implement exact `ver=2` parsing using `type(value) is int`; rerun token-version cases. Expected GREEN.
- [x] Write failing `ra` tests: missing, empty, malformed UUID, integer, bool, null, list, and object. Run the role-assignment-claim selection. Expected RED.
- [x] Implement required UUID-string `ra` parsing from None; rerun. Expected GREEN.
- [x] Write failing patient claim-shape tests. Supporting_actor requires both valid UUID `patient` and `pil`; test either missing, malformed/wrong type, or singly present. Staff roles reject either or both claims. Run the patient-shape selection. Expected RED.
- [x] Implement role-specific `patient`/`pil` encoding and parsing; rerun. Expected GREEN. Preserve issuer/audience/HS256/time/jti/TTL behavior.
- [x] Add only token-to-roster user/org/role/patient comparison in `current_actor`; run mismatch tests. Expected GREEN while replacement-authority tests remain RED.
- [x] Add exactly one fresh `resolve_authority` call and actor-tuple comparison; run simple revocation and ambiguity tests. Expected GREEN while replacement-ID tests remain RED.
- [x] Add role-assignment ID comparison; rerun replacement-grant test. Expected GREEN.
- [x] Add nullable patient-link ID comparison; rerun replacement-link test. Expected GREEN.
- [x] Add mismatched org/user/role/patient/ra/pil cases, a valid other-organization membership, inactive user, and roster change after issuance. Run; implement only uncovered comparisons; rerun GREEN.
- [x] Remove org/role discovery only after repository call-site search is empty; retain no fallback. Update token-unit fixtures to v2 with no legacy acceptance.
- [x] Run the complete Task 3 suite command, Ruff/Pyright, seven hashes, and `git diff --check`.
- [x] Update the ledger with every compatibility RED/GREEN and commit `fix: bind demo sessions to explicit authorization records`.

## Task 4: Stable refusal, sanitization and cookie preservation

**Files**
- Modify auth error translations in `service.py`, `dependencies.py`, and `api/demo_sessions.py`.
- Modify `tests/test_auth.py`, `tests/integration/test_demo_session_http.py`.
- Create `services/api/tests/integration/test_demo_session_process.py`.

- [x] Write one missing/malformed-roster issuance test using `ASGITransport(raise_app_exceptions=False)`; assert configuration 503 and no Set-Cookie. Run it. Expected RED before narrow mapping.
- [x] Map only roster/config errors to the constant configuration 503 with suppressed context. Rerun. Expected GREEN.
- [x] Write one unavailable/ambiguous-actor issuance test; assert actor 503 and no Set-Cookie. Run it. Expected RED.
```python
assert response.status_code == 503
assert response.json() == {"detail": "Demo actor is unavailable"}
assert "set-cookie" not in response.headers
```
- [x] Map only authority unavailable/ambiguous errors to actor 503; rerun. Expected GREEN.
- [x] Write reauthorization ambiguity and revoked/mismatched tests; assert no-longer-authorized 401. Run; add the narrow typed mapping; rerun GREEN.
- [x] Write issuance database-failure test; assert 503 `Demo authentication is unavailable`. Run. Expected RED raw 500; add narrow SQLAlchemy-derived mapping from None; rerun GREEN.
- [x] Repeat the database-failure test for reauthorization. Run RED, add the dependency mapping without fallback/reuse of failed session, rerun GREEN.
- [x] Add preservation tests for wrong valid role → 403 and unknown path role → 422. Run; do not edit production if already GREEN.
- [x] Inject SQLAlchemy errors containing synthetic secret, URL, SQL, parameter, and token sentinels; inspect response, captured logs, and `traceback.format_exception`. Run. Expected RED if raw chain/500 escapes.
- [x] Suppress original exception context and never log/format caught database objects. Rerun sentinel test. Expected GREEN.
- [x] Add a real Uvicorn case with valid startup attestation then auth-query failure in its dedicated disposable fixture. Run it; assert stable HTTP/log output, stop only the owned process, and record ordinary cleanup.
- [x] Write cookie flag tests: local/nonlocal Secure, HttpOnly, SameSite=lax, Path=/, host-only, and no Max-Age/Expires. Run; treat existing passes as preservation evidence.
- [x] Write TTL boundary tests for absent=30, 1/120 accepted, 0/121/non-integer/blank sanitized 503, expiry boundary, and two-hour maximum. Run; implement only an observed defect; rerun GREEN.
- [x] Write the repeated-issuance cookie test:
```python
first = client.post("/v1/demo/session/navigator")
old_cookie = client.cookies["ojcc_session"]
second = client.post("/v1/demo/session/navigator")
assert first.status_code == second.status_code == 204
assert client.cookies["ojcc_session"] != old_cookie
assert len([c for c in client.cookies.jar if c.name == "ojcc_session"]) == 1
```
- [x] Run it; implement only if RED; rerun GREEN.
- [x] Run all auth/HTTP/process tests with no skips, Ruff/Pyright, seven hashes, and `git diff --check`.
- [x] Update the ledger with each refusal/cookie RED or preservation result and commit `fix: sanitize demo authentication refusal paths`.

## Task 5: Seed intent and live configuration

**Files**
- Modify `services/api/scripts/seed_demo.py`, `services/api/tests/test_demo_seed.py`.
- Modify `.env.example`, `README.md`.
- Modify `scripts/verify_live_journey.ps1`, `apps/web/playwright.live.config.ts`, `services/api/tests/test_verify_harness.py`.
- Modify `services/api/tests/integration/test_non_owner_closed_loop.py`.
- CI change only if necessary for configuration propagation; preserve its three cache blocks.

**Interfaces**
`demo_actor_configuration() -> dict[str, dict[str, str]]` maps navigator/administrator/supporting_actor to named DEMO_IDS; patient entry includes named patient.
`python -m scripts.seed_demo --print-demo-actors` prints JSON and exits without a connection or requiring database-URL CLI arguments.
`validate_existing_demo_identities(session: Session) -> None` checks immutable identity relationships before seed mutation; conflict raises sanitized seed error.
Use explicit settings/config in HTTP fixtures and runtime live environment.

- [x] Write the connection-forbidden `--print-demo-actors` CLI test with exact literal keys/IDs. Run it. Expected RED: missing CLI mode or URL requirement.
- [x] Implement pure `demo_actor_configuration()` and an early CLI exit that prints sorted JSON IDs only. Rerun. Expected GREEN with engine creation blocked.
- [x] Write one deterministic-ID drift test: reuse the intended role-assignment ID for the wrong user, capture complete pre/post digest, and assert refusal before insert/trigger toggle. Run. Expected RED.
- [x] Implement the read-only intended-role identity preflight before mutation. Rerun. Expected GREEN and unchanged digest.
- [x] Add separate wrong organization, role, patient-link user, and patient fixtures. Run. Extend only exact comparisons that fail; rerun GREEN.
- [x] Write non-repair tests for inactive user, revoked grant, and revoked link. Assert seed never reactivates, clears revocation, or inserts replacement authority. Run. Expected RED for any repair behavior.
- [x] Add the minimal non-repair refusal behavior and rerun. Confirm repeated normal seed and completed-journey reseed retain full digest/history counts.
- [x] Write a harness test that requires DEMO_ACTORS_JSON in the FastAPI child and forbids it, session secret, bootstrap URL, and migration URL in Next. Run. Expected RED: roster absent from API propagation.
- [x] Make the live wrapper obtain roster JSON from the connection-free CLI and forward it only to FastAPI. Rerun allowlist test. Expected GREEN.
- [x] Write success and forced-failure restoration tests for a prior DEMO_ACTORS_JSON value. Run. Expected RED until the variable joins the existing save/remove/restore lifecycle.
- [x] Implement exact restoration and rerun. Expected GREEN.
- [x] Update the non-owner journey to use explicit roster and version-2 issuance. Run only that journey; expected GREEN with runtime current_user/session_user equal the API login.
- [x] Run the complete Task 5 pytest command, web lint, PowerShell parsing, Ruff/Pyright, seven hashes, cache-block guard, and `git diff --check`.
- [x] Update the ledger with every RED/GREEN and disposable database disposition; commit `chore: configure the intended synthetic demo actors`.

## Task 6: Complete security and journey acceptance

**Files**
- Update only this plan, design status, and new ledger after evidence exists.
- No implementation feature expansion.

- [ ] Generate and record fresh API/live UUID names and known ownership. Provision with the existing separate-credential tooling; reset/replay/seed via the owner/bootstrap bridge. Export explicit roster and demo org/secret for API tests. Never copy a persistent target.
- [ ] Run all new auth/config/resolver/HTTP/process cases with required PostgreSQL availability. Run existing privilege, runtime attestation, non-owner journey, identity/history, restore-integrity, seed, migration and offline replay suites.
- [ ] Verify `alembic current` is 0007 head, `alembic check` has no drift, all seven migration hashes match the ledger, and the frozen v0007 test passes.
- [ ] Generate OpenAPI/TypeScript contracts using existing scripts; verify no request/response schema change is accidentally introduced by internal token types.
- [ ] Run the complete verifier from repository root, with the API credential triple exported and separate live triple held in these named variables:
```powershell
./scripts/verify.ps1 `
  -LiveBootstrapDatabaseUrl $liveBootstrapDatabaseUrl `
  -LiveMigrationDatabaseUrl $liveMigrationDatabaseUrl `
  -LiveDatabaseUrl $liveApplicationDatabaseUrl `
  -LiveConfirmDatabaseName $liveDatabaseName
```
Expected: locked dependency install, Ruff, Pyright, runtime integrity, all pytest, web lint/Vitest/build, mocked browser tests, and live desktop/mobile transportation journeys. No skipped security tests.
- [ ] Preserve exact live story: review → approve → claim → start → complete → patient follow-up → outcome → reload persisted history. Verify runtime current_user/session_user are the real non-owner login and all pre/post integrity audits are clean.
- [ ] Check no workflow source changes, no seven migration changes, no dependency changes, no credential/SQL/token leakage, and no privilege/target weakening. Record complete test counts and statuses.
- [ ] Stop only owned processes/connections; validate recorded database names and owners, check sessions, ordinarily drop only known disposable databases. Leave and record uncertain/active leftovers; never terminate unknown sessions.
- [ ] Update ledger with evidence, intentional limitations and next exact step; commit documentation. Stop for user review. Do not merge/deploy/remove worktrees or start a deferred milestone.

## Required scenario traceability

| Requested scenario | Failing-first/proof location |
|---|---|
| Import-safe malformed TTL and sanitized configuration 503 | Task 1 subprocess and HTTP tests |
| Missing/legacy/wrong-value/wrong-type `ver` | Task 3 token-version matrix; Task 4 HTTP mapping |
| Missing/invalid/wrong-type `ra` | Task 3 role-assignment-claim matrix; Task 4 HTTP mapping |
| Invalid role-specific `pil`/`patient` shapes | Task 3 patient-shape matrix; Task 4 controlled 401 |
| 1. Two eligible users for same role | Task 3 explicit selection HTTP tests; no configuration means refusal |
| 2. Overlapping role assignments | Task 2 actual PostgreSQL acceptance + controlled resolver refusal; Task 4 HTTP mapping |
| 3. Multiple links / duplicate actor projections | Task 2 forward/reverse link cardinality, staff independence; Task 4 sanitized response |
| 4. Revoked role/link after issuance | Task 3 signed cookie, committed revocation, replacement grant/link |
| 5. Cross-org and user/patient mismatch | Tasks 2–3 FK/predicate/roster/claim checks, no CurrentActor override |
| 6. Inactive user | Tasks 2–3 resolution and HTTP refusal |
| 7. Valid patient and navigator | Tasks 3, 5, 6; administrator contract also tested |
| 8. Controlled ambiguity, no raw 500/traceback | Task 4 ASGI and Uvicorn/sentinel tests |
| 9. Real non-owner demo journey | Tasks 5–6 actual login and persistent database assertions |
| 10. Hashes and full repository verifier | Tasks 1, 6; seven files, v0007, desktop/mobile |

## Plan self-review

Scope, role contracts, interfaces, history preservation, seeded identity intent, single-statement resolution, provenance format, cookie policy, failure mapping, database safety and all requested scenarios have explicit tasks. Malformed TTL import has a separate RED/GREEN cycle. Token compatibility has literal version, role-assignment, patient, and link claim matrices. Original Tasks 2–5 now separate each failing test, observed RED, minimal implementation, focused GREEN, static verification, evidence update, and commit; resolver and reauthorization work are not single implementation steps. Existing tests that bypass CurrentActor are not authentication proof. No migration or global overlap prevention is claimed. Architecture is approved; the amended plan still requires explicit approval before implementation.
