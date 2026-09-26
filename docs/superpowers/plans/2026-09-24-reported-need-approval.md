# Reported need approval implementation plan

> Execution: use superpowers:executing-plans and superpowers:test-driven-development inline. The user explicitly authorized implementation after the in-chat design review.

**Goal:** Allow a navigator to approve exact check-in evidence and atomically create one reported need.

**Architecture:** Separate immutable policy, proposal and decision records; database-derived evidence and guarded creation. Preserve the existing approval model for existing targets.

**Tech Stack:** PostgreSQL 16, Alembic, SQLAlchemy, FastAPI, React/Next.js.

**Spec:** ../specs/2026-09-24-reported-need-approval-design.md

## Global constraints

Preserve master, all existing worktrees and migrations 0001–0007. Only named disposable databases. Restricted runtime login must retain SELECT-only access to reported_need. No merge or publication.

## Review focus

- Concurrent corrections and approvals must serialize with post-lock revalidation.
- A committed response lost in transit must be safely retryable.
- Direct SQL cannot supply fabricated snapshots or create duplicate needs.
- Old policy and authority evidence cannot be rewritten; revoked or ambiguous authority fails closed.
- Corrections after creation remain visible even when the new answer is no.

## Task 1: Database creation boundary

- [x] Add real restricted-login integration tests in services/api/tests/integration/test_need_creation.py for immutable evidence, stale corrections, independent chains, retry, concurrency and audit rollback.
- [x] Run the new tests and confirm missing relation/behavior failures.
- [x] Add 0008_need_creation_approval.py, models/need_creation.py and privilege_contracts/v0008.py. Extend runtime attestation to the new exact catalog while retaining frozen v0007 history.
- [x] Prove the new integration tests and privilege attestation pass.

## Task 2: HTTP commands and read model

- [x] Add failing HTTP tests for navigator-only proposal, decision and history endpoints.
- [x] Add api/need_creation.py with strict request schemas, exact proposal IDs, stable retry IDs, conflict handling that commits refused attempts, organization-scoped reads and no-store responses.
- [x] Register router, export OpenAPI and regenerate web types.
- [x] Run API and metadata/integrity regressions.

## Task 3: Navigator review flow

- [x] Add failing component tests for proposal review, explicit creation, stale evidence and retry behavior.
- [x] Add a focused need-creation component and API client methods; integrate with the candidate panel and history.
- [x] Run web tests, lint, type checking, build and live browser journey.

## Task 4: Verification and handoff

- [x] Run the full verifier against separate disposable API/live databases.
- [x] Verify immutable migration hashes and unchanged master edit.
- [x] Obtain a fresh whole-branch review, fix material findings with regression coverage, and record evidence in the progress ledger.
- [x] Leave the isolated branch reviewable and summarize completion and limitations.
