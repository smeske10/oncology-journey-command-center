# Task 3 implementation report: demo roles and tenant enforcement

## Scope

Implemented short-lived signed demo sessions, role authorization, and explicit organization scope for actor and patient lookups. The API now exposes `POST /v1/demo/session/{role}`, writes an `ojcc_session` cookie, and provides `CurrentActor` plus `require_role(*roles)` for subsequent protected routes. The real navigator queue remains deliberately out of scope for Task 5.

## RED evidence

The authorization and tenant-isolation tests were created before the auth implementation and run with:

```powershell
python -m pytest services/api/tests/test_auth.py services/api/tests/integration/test_tenant_isolation.py -q -p no:cacheprovider
```

The run failed at collection as expected because the new session/auth package did not exist:

```text
services/api/tests/test_auth.py:8: in <module>
    from app.auth.dependencies import require_role
E   ModuleNotFoundError: No module named 'app.auth'
```

The shared interpreter also did not yet have the locked SQLAlchemy dependency, so the integration test independently reported `ModuleNotFoundError: No module named 'sqlalchemy'`. The green runs used the already-existing isolated, locked Python environment.

A second RED/GREEN cycle tightened the pre-existing unit-of-work lookup contract. Before implementation, this focused command failed because an unscoped method call was accepted:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pytest tests/test_repositories.py -q -p no:cacheprovider
```

```text
FAILED tests/test_repositories.py::test_unit_of_work_requires_scope_and_rejects_cross_tenant_operations
Failed: DID NOT RAISE <class 'TypeError'>
```

## Implementation

- `app.auth.models` defines frozen `CurrentActor(user_id, organization_id, role)` and reuses the domain `UserRole` as `Role`.
- `DemoSessionService` issues HS256 JWTs with `iss=ojcc-demo`, `aud=ojcc-web`, `iat`, `nbf`, `exp`, and a random `jti`. It rejects altered signatures, malformed claims, expired tokens, future tokens, and lifetimes over two hours.
- `DEMO_SESSION_SECRET`, `DEMO_SESSION_TTL_MINUTES`, and `DEMO_ORGANIZATION_ID` are environment configuration. There is no fallback demo-secret value: construction fails closed if the secret is absent, and the API returns a configuration-unavailable response.
- Demo actor selection joins `User` and `RoleAssignment` and filters both by the required `organization_id`; inactive users are excluded.
- Cookies are `HttpOnly`, `SameSite=Lax`, `Path=/`, and are `Secure` outside `APP_ENV=local`.
- `current_actor` reads only the signed HttpOnly cookie, and `require_role` returns 403 with `Role not permitted` for a non-allowed role.
- `PatientRepository.get_for_actor` requires an explicit `organization_id`, and `SqlAlchemyUnitOfWork.get` now also requires and validates an explicit `organization_id`. Neither exposes an unscoped `get(id)` lookup.
- The session router is registered in `app.main`; tests confirm registration and retain disabled FastAPI documentation routes.
- `apps/web/lib/session.ts` provides the shared demo-role union and session endpoint path helper.

## GREEN and static verification

The final focused security suite was run after the explicit lookup change:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pytest tests/test_auth.py tests/integration/test_tenant_isolation.py tests/test_repositories.py -q -p no:cacheprovider
```

```text
8 passed, 1 skipped in 2.78s
```

The same run also completed:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m ruff check .
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pyright --pythonpath C:\tmp\ojcc-domain-venv\Scripts\python.exe
```

```text
All checks passed!
0 errors, 0 warnings, 0 informations
```

The complete API suite was also run after the auth implementation:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

```text
13 passed, 2 skipped in 5.47s
```

The two skips are the existing real-PostgreSQL integration contracts; no SQLite substitute was used.

`git diff --check` was run after the final implementation and static checks. It exited successfully with no whitespace errors (Git printed only ambient daemon/line-ending warnings).

## Full verifier status

The non-Docker verifier was attempted with the isolated environment first on `PATH` so it would use the locked dependencies. It confirmed all lockfile requirements were already satisfied, but its editable API install stopped with the documented local bootstrap failure:

```text
ModuleNotFoundError: No module named 'setuptools'
```

An attempt to install only `setuptools` into that local environment was blocked by sandbox network policy (`WinError 10013`). The approved retry was interrupted before it completed. Per the controller's direction, no further install or full-verifier attempt was made. No repository dependency, lockfile, or global Git configuration was changed.

## Files changed

- `.env.example`
- `apps/web/lib/session.ts`
- `services/api/app/api/__init__.py`
- `services/api/app/api/demo_sessions.py`
- `services/api/app/auth/__init__.py`
- `services/api/app/auth/models.py`
- `services/api/app/auth/service.py`
- `services/api/app/auth/dependencies.py`
- `services/api/app/config.py`
- `services/api/app/db/repositories.py`
- `services/api/app/main.py`
- `services/api/tests/test_auth.py`
- `services/api/tests/integration/test_tenant_isolation.py`
- `services/api/tests/test_health.py`
- `services/api/tests/test_repositories.py`

## Self-review

- Authorization is tested directly through `require_role`, per the preflight ruling; a patient-facing `SUPPORTING_ACTOR` cannot obtain navigator permission. Task 5 will attach this dependency to the real navigator queue route and repeat route-level authorization.
- Token tests cover valid signed tokens, tampering, expiry, and a lifetime exceeding the two-hour maximum. Successful token decoding also proves the generated `jti`, issuer, audience, subject, organization, and role claims satisfy validation.
- Cookie tests cover the local `HttpOnly`/`SameSite=Lax`/`Path=/` policy and the `Secure` flag in a non-local environment.
- The tenant-isolation integration test creates two organizations and confirms an actor-organization lookup cannot retrieve the other organization’s patient. The repository statement requires `organization_id` explicitly.
- Actor lookup, patient lookup, and unit-of-work lookup all require an organization scope; there is no default tenant or public raw session supplied to application routes.
- The main application includes the demo router, while `docs_url`, `redoc_url`, and `openapi_url` remain disabled and are asserted in route tests.

## Concerns

- A reachable PostgreSQL service was unavailable, so the real tenant-isolation integration test remains correctly environment-skipped. It will run unchanged once `DATABASE_URL` points to migrated PostgreSQL.
- The full cross-stack verifier is blocked only by the local environment's missing `setuptools` bootstrap package and a blocked/interrupted attempt to install that package. Focused API tests, Ruff, Pyright, and whitespace validation completed successfully.

---

## Fix round 1: unavailable demo tenant configuration

### Finding addressed

`get_demo_session_service` previously accepted `settings.demo_organization_id=None`. The token decoder legitimately constructs a verifier-only `DemoSessionService` without an actor repository or organization, but the issuer route requires both. The missing value therefore reached `create_session()` and raised an unhandled `RuntimeError`.

The issuer dependency now explicitly rejects a missing `DEMO_ORGANIZATION_ID` before creating the service. Its existing controlled configuration-error mapping returns `503` with only:

```json
{"detail":"Demo sessions are not configured"}
```

### RED evidence

The endpoint regression test was added before the implementation change and run with:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pytest tests/test_auth.py -q -p no:cacheprovider
```

It reproduced the defect rather than returning an HTTP response:

```text
app\api\demo_sessions.py:37: in create_demo_session
    token = session_service.create_session(role)
app\auth\service.py:79: in create_session
    raise RuntimeError("Demo session actor repository is not configured")
E   RuntimeError: Demo session actor repository is not configured
```

The same RED run exposed a flaky older tamper test that changed only unused base64url padding bits. The test now deterministically changes the signed header input; no token behavior was relaxed.

### GREEN and verification evidence

After validating `DEMO_ORGANIZATION_ID` during dependency construction, the focused auth suite completed:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pytest tests/test_auth.py -q -p no:cacheprovider
```

```text
12 passed in 0.75s
```

The following static checks also completed in the same fix round:

```powershell
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m ruff check .
C:\tmp\ojcc-domain-venv\Scripts\python.exe -m pyright --pythonpath C:\tmp\ojcc-domain-venv\Scripts\python.exe
```

```text
All checks passed!
0 errors, 0 warnings, 0 informations
```

### Additional token-claim coverage

The fix round adds independent, re-signed-token regression cases for incorrect issuer, incorrect audience, empty `jti`, and future `nbf`. This exercises the decoder's individual claim checks rather than relying solely on a tampered signature to reject malformed identity claims.

### Fix-round self-review

- A missing tenant issuer setting now fails before the route can reach actor lookup or token issuance.
- The decoder-only dependency remains able to omit organization scope intentionally: it never performs an actor lookup, while every issuer and repository lookup has an explicit organization id.
- The new endpoint test asserts both the 503 status and the exact public detail, ensuring no `RuntimeError` text leaks to callers.
- No dependency, lockfile, migration, router shape, or Task 4 behavior was changed.
