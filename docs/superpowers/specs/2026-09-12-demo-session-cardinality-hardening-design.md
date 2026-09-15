# Demo-session authentication and authorization-cardinality hardening

**Date:** 2026-09-12 (America/New_York)
**Status:** Accepted after the bounded CI live-environment repair. Fresh full security and desktop/mobile acceptance passed at repaired source `36435bb21eb6880fc6770a342bd3eb92c4bdb9a2` on 2026-09-14 with the actual CI `LIVE_*_DATABASE_URL` aliases populated: 727 API tests in both standalone and root runs, 34 web tests, production build, three mocked browser tests, and both live viewports. Earlier acceptances remain historical. Awaiting user review only; see the progress ledger for fresh target dispositions and exact evidence.
**Plan amended:** 2026-09-13 (America/New_York)
**Milestone:** Navigator closed-loop post-merge security and operational hardening; Week 1 authentication/authorization closure supporting Week 4 release readiness.
**Base:** `0a3cda82203d850992f2efc3653b653c8253459c`
**Branch:** `feature/demo-session-cardinality-hardening`

## Recommendation

Configure the intended synthetic user for each of the three existing demo roles, require exactly one effective authorization record at every relevant boundary, and bind each issued token to its qualifying role assignment and, for patient access, its patient identity link. Preserve all stored history. Do not add migration 0008.

Explicit identity selection alone does not resolve overlapping grants. Uniqueness alone does not express which synthetic person the public demonstration intends. Combining explicit selection with fail-closed record cardinality is the smallest design that addresses both. Binding tokens to the original authority prevents a replacement grant/link from silently keeping an old session valid.

No patient, navigator, agent, administrator UI, or domain-command behavior is added.

## Verified baseline and evidence

The [progress ledger](../progress/2026-09-12-demo-session-cardinality-hardening.md) records hashes, commands, limits, and both successful GitHub Verify runs. Remote master and fresh worktree HEAD equal the requested merge. PR head `7cef17aff4ea07bc68e92aba74c2279687ae187c` is included. The old database-privilege worktree remains preserved.

Source findings at this exact commit:

| Finding | Evidence | Consequence |
|---|---|---|
| Issuance selects by organization and role, with no intended user | `app/auth/service.py:find_active_actor`, `DemoSessionService.create_session` | Two eligible users produce multiple rows |
| Issuance and per-user lookup join effective role assignments to effective patient links, then call `one_or_none()` | Both SQLAlchemy repository methods | Even identical projected actor tuples can raise; row multiplication is not actor selection |
| Async patient helper repeats the join and calls `scalar_one_or_none()` | `app/auth/dependencies.py:resolve_patient_actor`; no callers found in repository search | Keep its contract consistent; do not leave a second unsafe resolver |
| Current activity is half-open: start <= at < revoked, or no end | All three resolver queries | A future-revoked row is still effective; adjacent intervals are valid |
| Unique indexes cover only rows with NULL revoked_at | Identity ORM and immutable 0002 | Future-ended rows can overlap each other or an open row; no cross-row interval exclusion exists for identities |
| Users are platform identities | Product §8.2; nullable primary_organization_id | Primary organization is not an access condition |
| Patient links have composite organization/patient FK | Identity ORM and 0002 | Tenant/patient consistency exists at storage level and must be preserved in resolver predicates |
| Reauthorization checks current user/org/role and patient value | `current_actor` dependency | Ordinary revocation is already covered; multiple rows remain uncontrolled |
| Tokens contain user/org/role/patient but no grant/link identity | Token encoder and parser | Inference: revocation followed by a replacement grant/link can revive an old token if its actor tuple matches |
| Endpoint catches only LookupError; dependency does not catch query errors | Demo route and auth dependency | ORM errors can escape as framework 500; no auth-local database-error sanitization |
| Three roles already exist | UserRole, web demoRoles, seed | Retain administrator, navigator, supporting_actor; do not invent a patient enum |
| Seed already has named stable synthetic IDs and historical navigator grant | `scripts/seed_demo.py:DEMO_IDS`, identity seed | Reuse these identifiers explicitly; do not infer actors from email/order |
| Cookie/token policy is already bounded | Token TTL 1–120 minutes, 30 default; route cookie options; auth tests | Preserve cookie flags and lifetime behavior |

A connection-free probe supplied rows to the real SQLAlchemy Result used by the unmodified repository. Both two-user and duplicate-projection cases raised uncaught MultipleResultsFound. This proves exception behavior, not PostgreSQL constraint behavior. Local existing tests: 18 passed, 18 deselected, with engine connections blocked. Real PostgreSQL red/green evidence is an implementation gate, not a planning claim.

Reference checks: SQLAlchemy documents the [single-result exception behavior](https://docs.sqlalchemy.org/en/20/core/connections.html#sqlalchemy.engine.Result.one_or_none). PostgreSQL supports [range exclusion constraints](https://www.postgresql.org/docs/16/rangetypes.html#RANGETYPES-CONSTRAINT) for non-overlap; the repository already uses them for pathway assignments.

## Alternatives compared

| Approach | Benefit | Limitation | Decision |
|---|---|---|---|
| Explicit configured/seeded actors only | Predictable actor despite other legitimate users | Overlapping grants/links and reauthorization ambiguity remain | Necessary but insufficient |
| Fail-closed uniqueness across every eligible user in the organization | Simple refusal with no arbitrary row choice | A second legitimate navigator disables the demo; actor intent still depends on population | Reject as issuance policy; retain strict cardinality within the explicit actor's authority |
| Database interval exclusions, plus explicit actors | Prevents conflicting intervals at write time, including concurrent inserts | New 0008, preflight of existing history, online/offline replay and attestation compatibility work; still cannot select among different valid users | Defer |
| Explicit actors + application cardinality + authority-bound tokens | Intentional selection, history preservation, immediate per-request refusal, no privilege/schema change | Does not prevent owner-created overlapping history globally | Recommend |

## Exact actor-selection contract

Keep `DEMO_ORGANIZATION_ID`. Add a server-only `DEMO_ACTORS_JSON` object keyed by existing role values. Each key occurs at most once. No client-supplied user/patient ID selects a session actor.

- `supporting_actor`: exactly `{"user_id": "<UUID>", "patient_id": "<UUID>"}`.
- `navigator`: exactly `{"user_id": "<UUID>"}`.
- `administrator`: exactly `{"user_id": "<UUID>"}`.

No fallback to seeded constants, primary organization, email, smallest UUID, ordering, DISTINCT, ORM unique(), or LIMIT 1. The runtime never imports the seed script. Missing role entry disables that demo role. Missing/invalid organization, absent JSON, malformed JSON, duplicate JSON keys, unknown role keys, invalid UUIDs, non-object values, extra fields, or the wrong patient-field shape produce a sanitized configuration failure. Parse at the service boundary so malformed new configuration does not print its contents in an import traceback. Parse `DEMO_SESSION_TTL_MINUTES` without eager `int(...)` failure: absence retains the 30-minute default, while non-integer and out-of-range values leave the settings module importable and produce the same sanitized 503 configuration response. Different roles may intentionally name the same user if each role independently satisfies its contract; the normal seed names three different users.

For every role, the configured organization must exist; the configured user must exist and be active; exactly one RoleAssignment for that organization/user/requested role must be effective. Other roles and other organizations neither grant nor invalidate this role. A NULL or different primary organization is permitted.

For supporting_actor, exactly one effective link for the configured organization/user is required, it must name the configured patient, and that patient must exist in the same organization. Independently require exactly one effective link for that organization/patient across all users, including links attached to inactive users. Two distinct effective link records are ambiguous even when they project the same patient. Never filter out a conflicting link just because it fails the expected patient/user predicate.

Navigator and administrator actors carry no patient ID. Their patient-link history is irrelevant to their staff role and is not joined or used to infer patient access. A staff token containing patient/link claims is invalid.

Two eligible users with the same role are acceptable when the roster names exactly one of them. Only that configured user can receive that role's demo session. If it is missing, inactive, revoked, or ambiguous, refuse; never switch to the other user.

## Temporal and cardinality semantics

Use one timezone-aware UTC instant per resolution operation. Effective intervals are `[granted_at, revoked_at)` and `[linked_at, revoked_at)`; NULL end is unbounded.

| History at the checked instant | Result |
|---|---|
| One effective record; any number of expired or not-yet-effective records | Eligible |
| End exactly equals the checked instant | Expired |
| Start exactly equals the checked instant | Effective |
| Future revocation with no overlap | Effective until its endpoint |
| Two effective same-role assignments for the user/org | Ambiguous, including identical projected actors |
| Adjacent non-overlapping assignments | One eligible authority at either side; old token does not transfer to successor |
| Overlap entirely in the past/future, outside the checked instant | Preserve; it does not create current authority; future overlap causes refusal when it becomes effective |
| Zero-length allowed historical interval | Empty and never effective; do not rewrite or tighten old CHECK constraints |
| Multiple roles for one user | Evaluate only the requested role |
| Multiple effective patient links by user or by patient | Ambiguous patient authority |

Implement one shared query builder and result interpreter for synchronous and asynchronous resolution. A single SQL statement evaluates user, organization, role records, and relevant link counts under one PostgreSQL statement snapshot. Use independent scalar aggregate subqueries/CTEs for role and link records, avoiding a role-by-link product. Count authorization-record IDs, not DISTINCT actor tuples. Extract an ID from its result array only after verifying its cardinality is exactly one. A user-PK anchor guarantees at most one envelope row; an empty anchor is unavailable. Include a reverse-link conflict predicate for any effective user link's patient.

Only SELECT privileges are needed. Do not introduce FOR UPDATE, privilege grants, authorization writes, or owner credentials. Unexpected cardinality still maps to a sanitized failure defensively. Keep malformed/ambiguous authority distinct internally from database unavailability.

This is request-time authorization, not continuous revocation of already-running operations. A change committed before the authorization statement is observed; a change after that statement may affect the next request. Existing domain guards remain responsible for their transaction-time checks. No new concurrency/locking policy is added to workflows.

## Issuance and reauthorization

Introduce an internal ResolvedAuthority containing CurrentActor plus role_assignment_id and nullable patient_identity_link_id. CurrentActor delivered to domain routes remains unchanged.

Issuance resolves the configured user and patient at the current instant, checks strict cardinality, and signs the actor and exact qualifying IDs. Preserve existing JWT issuer/audience/algorithm/time/jti guarantees. Add a session-format version claim `ver=2`, required UUID claim `ra`, and patient-only UUID claim `pil`. The patient actor also retains its existing `patient` claim. Claim shape validation is role-specific. Do not round authorization comparisons to JWT integer-second precision.

Per request:

1. Missing cookie returns the existing 401.
2. Verify signature, version, issuer/audience, claim types, UUIDs, time bounds, and role-specific shape.
3. Check actor claims against the configured organization and requested role's roster entry.
4. Resolve current authority once at request time using the same cardinality rules as issuance.
5. Require exact equality of user, organization, role, patient, role assignment ID, and patient link ID.
6. Return CurrentActor to existing role checks and domain routes.

Revoked/inactive/mismatched/ambiguous identities receive 401. A replacement role/link never authorizes the old token, even if user/org/patient values match. A fresh issuance may use the successor after it becomes uniquely effective. Roster changes invalidate old sessions for that entry on the next request.

Existing tokens without version/provenance claims receive controlled 401 and can be replaced through the existing demo endpoint; no dual-format fallback. Tests explicitly cover missing, legacy, invalid-value, and wrong-type `ver`; missing, invalid-UUID, and wrong-type `ra`; and every role-specific `pil`/`patient` mismatch, including missing patient provenance and patient claims on staff tokens. This bounded compatibility change is intentional and approved with this design. New issuer/parser deploy together; rolling mixed-version deployment is outside scope. No server-side session store or logout/revocation service is introduced.

## Stable HTTP and sanitized errors

| Condition | HTTP/body |
|---|---|
| Successful issuance | 204; existing cookie name/path replaced |
| Missing/malformed demo configuration or missing role entry | 503 `{"detail":"Demo sessions are not configured"}` |
| Configured actor absent, inactive, mismatched, revoked, or ambiguous during issuance | 503 `{"detail":"Demo actor is unavailable"}` |
| Database unavailable during issuance or reauthorization | 503 `{"detail":"Demo authentication is unavailable"}` |
| Missing cookie | 401 `{"detail":"Authentication required"}` |
| Bad/expired/legacy token or invalid claim shape | 401 `{"detail":"Invalid or expired demo session"}` |
| Token no longer satisfies current authority/roster | 401 `{"detail":"Demo session is no longer authorized"}` |
| Valid actor on disallowed route | Existing 403 `{"detail":"Role not permitted"}` |
| Unknown role path value | Existing validation 422 |

Translate known auth-domain errors and SQLAlchemy errors at narrow auth boundaries; no global exception swallowing. Suppress raw exception chains with `from None`; never format/log caught SQLAlchemy errors, statements, parameters, URLs, credentials, tokens, or full cookies. Tests inspect response text, formatted traceback, and real server logs with secret/SQL sentinels. Request-scoped sessions close normally; no failed transaction is reused for a fallback lookup. An unavailable dependency does not become permission.

Do not add an audit-table write from authentication: runtime has no such INSERT privilege. No auth audit schema or wider observability project is included.

## Cookie preservation

Retain `ojcc_session`, host-only scope, Path=/, HttpOnly, SameSite=lax, Secure outside APP_ENV=local, random jti, configured 1–120 minute TTL (30 default), and hard two-hour verification ceiling. The cookie is currently a browser-session cookie without Max-Age/Expires; the signed expiry bounds server acceptance. That is not a demonstrated defect, so do not add persistent cookie expiry.

A successful issuance replaces the cookie at the same name/domain/path. Failure sends no new session cookie and does not silently switch actors. Reauthorization still validates any old cookie. Test replacement in one cookie jar and local/non-local flags. No SameSite, CSRF, rate-limiting, or cookie architecture redesign is included.

## Seed and operator configuration

Keep stable DEMO_IDS and existing authorization timestamps. Add a pure `demo_actor_configuration()` helper in the seed tooling producing the explicit roster from the three named seed users and named patient. Expose it as a connection-free CLI output mode for local/live configuration. It contains IDs only, no credentials. Operators explicitly export its JSON and the organization; runtime has no implicit seed/production identity assumption.

Before seed inserts or trigger disabling, validate any already-present intended identity records for expected organization/user/role/patient identities. This includes a SELECT-only lookup of the deterministic patient's organization even when its intended link is absent: absence or exact organization match is allowed; mismatch raises the existing sanitized conflict. Refuse conflicting deterministic-ID reuse without repair; absent rows may be inserted by the existing seed. Preserve recorded grants/revocations, active status, and completed journey history; seed must not reopen a revoked assignment or manufacture a replacement. A consistent but unavailable actor remains unavailable and is reported by validation/session issuance, not silently repaired. Repeated seeding must retain complete row digests and history counts. Validate and report the roster separately from mutation.

Pass DEMO_ACTORS_JSON explicitly through the live wrapper and FastAPI-only Playwright environment. Restore prior environment on success/failure. The root verifier also removes the API database triple, all three CI `LIVE_*_DATABASE_URL` aliases, session secret, organization, and roster around frontend lint/test/build/mocked Playwright, restoring exact prior values in finally before the live wrapper and on frontend failure. Next receives no actor roster, signing secret, or database URL. Preserve CI cache blocks, separate API/live targets, and bootstrap/owner/runtime process boundaries. The final ledger records the separate pre-existing live-wrapper absent-to-empty restoration observation; the accepted full workflow supplies explicit demo values.

## Migration decision and existing-data behavior

No 0008, ORM constraints, new relation/function, privilege-contract edits, Alembic head change, or replay change is proposed. Freeze 0001–0007 plus the existing v0007 contract. Runtime attestation continues to select revision 0007.

Existing ambiguous rows remain byte-for-byte intact and cannot authorize the affected session while ambiguous. Existing non-overlapping history remains usable. This does not claim database-wide temporal integrity or block future owner-created overlaps. Repair requires separately approved, audited administration; neither seed nor auth performs it.

If evidence requires database exclusions, stop and amend the plan before implementation. The alternative would require three scope keys: role (org,user,role), link (org,user), and link (org,patient), each with half-open range exclusion. It would need locked preflight scanning all existing intervals, refusal without data repair, concurrency tests, owner-only atomic DDL, online/offline execution parity, frozen-0007 compatibility, and explicit runtime revision handling. No migration 0008 artifact is authorized here. Downgrade/replay behavior remains PR #7's existing refusal and staged replay contract.

## Approval and acceptance

Approve the explicit roster, strict current cardinality, authority-bound version-2 sessions (including legacy-token refusal), role-specific patient semantics, HTTP contract, and no-0008 boundary together with the implementation plan.

Acceptance requires all ten requested scenarios, real API and PostgreSQL tests, real non-owner credentials, post-issuance revocation/re-grant tests, preserved seed/history digests, immutable hashes, and the complete repository verifier including desktop/mobile live journeys. No skipped security test counts as a pass.

Deferred: deterministic audit-ID/FIPS assessment; broader release verification and independent security review; Week 3 policy engine/orchestration/retrieval/evaluations/new scenarios; deployment. No merge, deploy, push, or worktree removal is part of this planning task.
