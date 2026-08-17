# Task 4 implementation report: synthetic patient check-in vertical slice

## Scope delivered

- Added tenant- and patient-scoped patient check-in endpoints for current definitions, immutable submissions, and synthetic FHIR-shaped export.
- Added typed draft validation with public-demo PHI safeguards. It rejects obvious email addresses, phone numbers, MRN-style identifiers, and explicit contact fields while deliberately not attempting generic name detection, because the demo requires synthetic names.
- Persists the submitted questionnaire version, exact labels displayed, structured answers, optional free text, patient-supplied provenance, and timestamp together before any policy or orchestration work. No policy or orchestration behavior was added in this task.
- Added a FHIR R4-shaped `Bundle` containing a `QuestionnaireResponse` and patient-supplied `Observation` resources. The mapper and generated contract describe a synthetic demo boundary only and make no production-conformance claim.
- Added the mobile-first one-question patient flow with browser draft preservation, urgent-care and synthetic-demo warnings, review-before-submit, retryable persistence-error, and confirmation states.

## TDD evidence

### API submission

The initial focused API route test was added first and run before the router existed:

```text
FAILED tests/test_check_ins.py::test_submit_check_in_is_atomic - assert 404 == 201
```

After the minimal submission route/domain implementation:

```text
1 passed, 1 warning in 0.75s
```

An explicit-contact-field regression cycle then failed with the generic extra-input error rather than the required public warning. Adding `mobile` to the explicit contact-field denial set made the focused API/FHIR suite green.

### FHIR export

The mapper test was added before the mapper package and failed at collection with:

```text
ModuleNotFoundError: No module named 'app.fhir'
```

The FHIR export-route test likewise failed at the intended missing-route point:

```text
FAILED test_patient_can_export_only_own_synthetic_fhir_submission - assert 404 == 200
```

Both became green after the minimal mapper and scoped export route were added.

### Patient flow

The focused component and Playwright tests were added before the patient-flow component. The first local Vitest launch was blocked by the sandbox before test discovery, not by a test result:

```text
Error: EPERM: operation not permitted, lstat 'C:\\Users\\smesk'
```

An escalated focused-run attempt was user-aborted before a result. The same pre-discovery `EPERM` occurred on the later ≤3-minute local retry. Once execution was approved outside the sandbox, the focused patient-flow suite completed with `2 passed in 950ms`; the full web unit suite completed with `3 passed in 999ms`; and the focused Chromium Playwright check-in journey completed with `1 passed (6.7s)`.

## Verification

Fresh API verification completed using the existing locked local environment:

```text
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m ruff check .
All checks passed!

C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pyright --pythonpath C:\tmp\ojcc-domain-venv\Scripts\python.exe
0 errors, 0 warnings, 0 informations

C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
23 passed, 2 skipped in 5.50s
```

The two skips are the pre-existing PostgreSQL integration tests; Docker/PostgreSQL was not available and no SQLite substitute was used.

`scripts/export_openapi.py` generated `contracts/openapi.json` from FastAPI routes while preserving disabled `docs`, `redoc`, and live OpenAPI routes. The controller then completed the bounded installation of the pinned `openapi-typescript` dev dependency (with zero audit vulnerabilities). The installed generator produced `apps/web/lib/api-types.ts` from that committed artifact, and `api-client.ts` imports the generated request/response types rather than duplicating backend API schemas.

Final web verification outside the sandbox completed:

```text
npm --workspace apps/web run lint
exit 0

npm --workspace apps/web test -- --run
Test Files  2 passed (2)
Tests  3 passed (3)

npm --workspace apps/web run build
✓ Compiled successfully
✓ Running TypeScript
✓ Generating static pages

npm --workspace apps/web run test:e2e -- patient-check-in.spec.ts
1 passed (6.7s)
```

## Self-review

- Patient submissions use the current actor's organization and user ID as the patient scope. FHIR export returns `404` unless both tenant and patient match.
- Submission creation assigns and persists its immutable source record before returning 201. There are no mutation endpoints.
- Question labels and the questionnaire canonical are snapshotted into the source data at submission time, so later definition edits cannot change the submission or its export.
- The client preserves drafts only in the browser, shows warnings throughout the flow, uses semantic headings/buttons/labels/live feedback, and keeps persistence failures in the review state.
- The patient check-in router is registered in `app.main`; its test asserts all three paths and confirms API documentation routes remain disabled.

## Environment-only gaps

- Web unit, lint/build, and Playwright execution could not start because the sandbox denies Node's `lstat` of `C:\Users\smesk`. The attempted elevated test was aborted before result.
- The existing PostgreSQL integration checks are still environment-skipped because no reachable Docker/PostgreSQL service is available. No SQLite substitute was used.
