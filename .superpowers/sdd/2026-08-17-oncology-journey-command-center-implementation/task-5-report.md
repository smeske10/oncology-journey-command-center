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
