# Task 5 implementation report: explainable navigator work queue

## Scope delivered

- Added a deterministic `NeedFactory` that maps only explicit submitted fields to navigation needs. It preserves each source value and free-text span exactly, produces repeatable outputs, and uses `{submission_id}:{kind}` idempotency keys for later durable command handling.
- Added transparent operational prioritization using reviewed constants for worsening reports, medication uncertainty, due time, and unresolved age. The ordering is explicitly not clinical-risk scoring and makes no model or language-model call.
- Added navigator-only, organization-scoped queue and patient-case reads. Each case contains the patient header, longitudinal submissions, open needs and every priority reason, safety signals, navigation tasks, and upcoming synthetic appointment.
- Registered `/v1/navigator` while retaining disabled documentation routes.
- Added the responsive navigator demo route and accessible queue selection. The UI shows exact evidence and plain-language priority reasons; it deliberately does not display a numeric score. It includes loading, API-error, and empty states.
- Exported the updated OpenAPI JSON contract. The installed TypeScript generator could not start in the managed sandbox (details below), so the controller must rerun it in the approved elevated environment.

## TDD evidence

### Prioritization RED

`tests/test_prioritization.py` was created before `app.domain.prioritization` existed. The requested focused run failed at collection for the expected missing rule:

```text
ModuleNotFoundError: No module named 'app.domain.prioritization'
```

After the minimal deterministic priority module was added:

```text
2 passed in 0.02s
```

### Queue/case API and NeedFactory RED

`tests/test_navigator_queue.py` was added before the navigator router and need factory existed. The initial focused run showed the intended absent-route and absent-module failures:

```text
GET /v1/navigator/queue -> 404 {"detail":"Not Found"}
GET /v1/navigator/patients/{patient_id}/case -> 404 {"detail":"Not Found"}
ModuleNotFoundError: No module named 'app.domain.needs'
```

The subsequent first GREEN run found only an issue in the query-aware test double: SQLAlchemy binds an `IN` predicate as a list, which the fixture attempted to put in a set. The fixture was corrected to flatten bound list values; no production authorization or query behavior was weakened. The final focused run was clean:

```text
10 passed in 1.04s
```

Coverage includes navigator allow, supporting-actor and administrator denial, missing and invalid session denial, cross-tenant patient-case 404, organization predicates on every fake-session read, exact evidence, all case sections, reason codes, and deterministic NeedFactory output.

### Navigator UI RED

`apps/web/tests/work-queue.test.tsx` was written before `WorkQueue` existed. The local test command could not reach Vitest discovery because the managed Node sandbox denies a parent-directory metadata read:

```text
Error: EPERM: operation not permitted, lstat 'C:\\Users\\smesk'
```

The same immediate environment-only failure occurs for lint, build, and the installed OpenAPI generator; it does not indicate a test failure. The controller has been notified for bounded approved-environment verification.

## Fresh API verification

```text
focused prioritization + navigator API: 10 passed in 1.04s
full API: 38 passed, 2 skipped in 5.74s
ruff check app tests: All checks passed!
pyright app: 0 errors, 0 warnings, 0 informations
scripts/export_openapi.py: exit 0
```

The two full-suite skips are the existing PostgreSQL/Docker integration checks. No Docker, network wait, or dependency installation was attempted.

## Required controller follow-up

Run these through the approved elevated Node environment, with a five-minute cap per command:

```powershell
npx --no-install openapi-typescript .\contracts\openapi.json -o .\apps\web\lib\api-types.ts
npm --workspace apps/web test -- --run tests/work-queue.test.tsx
npm --workspace apps/web test -- --run
npm --workspace apps/web run lint
npm --workspace apps/web run build
```

No navigator E2E was added; no additional browser verification is pending beyond the focused component test and normal web checks.

## Self-review

- Every route reads through `organization_id == actor.organization_id`; patient-case lookup adds the requested patient ID and returns 404 before exposing any other section.
- Route-level role enforcement is independent of frontend state. Navigator is permitted; supporting actors and non-navigator actors are denied; missing/invalid sessions return 401.
- Queue sorting uses deterministic score descending, then earliest due time, oldest need, and UUID string as a stable final tie-breaker.
- Each generated API priority contains exact reason codes. The browser maps those codes into plain-language explanations and never presents the numeric score by itself.
- Need extraction is explicit and deterministic: no inferred diagnosis, no risk label, no language model, and no mutation of immutable submission evidence.
- The current UI client source references generated navigator response types. Once the approved generator runs, those type names will be emitted from the checked-in contract; hand-editing the generated file was intentionally avoided.

## Controller verification fix: contract generation and score disclosure

The controller successfully ran the installed OpenAPI TypeScript generator in its approved environment and refreshed `apps/web/lib/api-types.ts` with `NavigatorQueueRead` and `PatientCaseRead`.

The controller's elevated full Vitest run then found one real test defect: 7 tests passed and the queue test failed because `queryByText(/score/i)` also matched the intentional product-boundary disclaimer, “This is not a clinical-risk score.” The UI did not display the numeric value at all, and removing the disclaimer would have weakened the safety boundary.

The regression assertion now verifies the actual requirement: the plain-language reasons and disclaimer are visible, while the numeric fixture score (`115`) is not rendered. This preserves both transparent reasons and the explicit non-clinical-risk statement. The controller will rerun its bounded Node verification after this test-only correction.

## Fix round 1: isolated evidence, validated queue policy, and current-case state

### Findings addressed

- `NeedFactory` previously attached every submission item (and free text) to every extracted need. It now maps each explicit matching source item only to the matching need kind. A transportation need cannot receive nausea or medication-question evidence from the same check-in.
- Evidence now deep-snapshots JSON-compatible values into immutable tuple structures before use, calculates a SHA-256 hash over canonical JSON (`sort_keys`, stable separators, UTF-8), and exposes `evidence_hash`. The deterministic command key is now `{submission_id}:{kind}:{evidence_hash}` for Task 9 idempotency composition.
- Priority configuration is now a validated deployment setting, `NAVIGATOR_PRIORITY_WEIGHTS_JSON`. It accepts reviewed non-negative integer weights and ordered thresholds; malformed, unknown, or unsafe values fall back to documented safe defaults. The navigator route receives an injectable policy dependency, with a tenant-provider protocol reserved for a later tenant override implementation.
- Queue priority inputs now come exclusively from that persisted need's evidence. Configured nonzero kind weights receive an explicit `configured_kind_*` reason; zero-point rules do not add misleading reasons.
- The web client aliases navigator types from generated OpenAPI `paths` responses, with no hand-duplicated backend queue fields or unsafe response-item cast. The patient-case request now uses an `AbortController`, clears old case data on selection, and ignores superseded resolutions.
- Queue ordering retains due time, created time, and UUID string as a deterministic final tie-breaker; a direct multi-item regression test records that invariant.

### RED evidence

Before the fixes, the focused API RED suite reported seven relevant failures:

```text
mixed submission evidence: transportation inherited nausea/medication/free-text fields
mutable list evidence: no need was extracted or frozen/hashable
configured base weight: reasons was []
Settings: unexpected navigator_priority_weights_json argument
ReportedNeed: no evidence_hash
queue transportation: inherited worsening_report and medication_uncertainty
navigator_queue: no get_navigator_priority_policy dependency
```

The new `navigator-page.test.tsx` was written before the request-cancellation change. It drives Patient A then Patient B and resolves A last; the old page would have rendered stale A. The local Vitest command could not reach discovery because managed Node again failed immediately with `EPERM: operation not permitted, lstat 'C:\\Users\\smesk'`. This is recorded for controller verification rather than treated as a product-test result.

### GREEN verification

```text
focused needs/prioritization/navigator API: 16 passed in 0.91s
full API: 45 passed, 2 skipped in 5.88s
ruff check app tests: All checks passed!
pyright app: 0 errors, 0 warnings, 0 informations
scripts/export_openapi.py: exit 0
```

The existing PostgreSQL/Docker integration checks remain the two skips. No Docker or network wait was used. The installed OpenAPI TypeScript command could not start locally because of the same Node sandbox `EPERM`; the controller must regenerate the already-exported contract and run focused/full Vitest, lint, and build in its approved environment.

### Fix-round self-review

- Evidence hash material is canonical JSON data, never Python representation or incoming map order. The factory retains no mutable source reference.
- A need's priority no longer reads its entire source submission, so separate needs from one check-in cannot transfer ordering reasons.
- The default deployment policy uses zero kind weights and documented operational rule values; when a deployment elects a positive kind weight, its reason is visible. This remains operational queue ordering, not a clinical-risk label or model decision.
- The current case is only retained while its selected request remains current. Selecting another patient clears the old case, cancels the prior request, and prevents stale completion from changing the screen.

### Controller web-test alignment

After the controller regenerated the TypeScript contract, its elevated full Vitest run reached the new race-condition test and found one test-only mismatch: the production request correctly calls `getNavigatorPatientCase(patientId, AbortSignal)`, while the first assertion expected a one-argument call. The regression now asserts an `AbortSignal` for both Patient A and Patient B and verifies that Patient A's signal is aborted immediately after selecting Patient B, before resolving A. The cancellation implementation remains unchanged; the controller will rerun the Node suite.

The controller's subsequent run reached the final assertion with 8 tests passing, then found an equally test-only ambiguity: Patient B appears both in the selected queue-card heading and the patient-case heading. The assertion now scopes to the accessible `Patient case` region and its level-two heading, proving that the case—not merely the selected queue item—remains Patient B after Patient A resolves. Production UI and cancellation code are unchanged.
