# Reported need approval progress

Base: e86ed90, merged navigator candidate preview. Branch: feature/reported-need-approval.

User authorized implementation after approving atomic creation, independent-chain eligibility, immutable evidence, audit handling, and navigator self-approval.

Baseline: the first run reached the candidate unit tests but could not reach PostgreSQL because it was stopped. Started isolated container ojcc-approval-test-20260924, without a persistent project volume, then repeated the baseline successfully before implementation (26 passed).

Ruling: policy version 1 requires exactly one navigator and allows self-approval, matching the approved milestone. Larger approval thresholds are outside this version; future versions require explicit design.

Ruling: maintain a PostgreSQL JSONB text canonicalization version and hash its UTF-8 bytes within the database. Store canonical text alongside structured evidence so verification does not depend on client serialization.

## Implementation and evidence

- Baseline repeated successfully: 26 candidate unit and restricted-login HTTP tests.
- Database RED: missing proposal relation. GREEN: immutable evidence, atomic creation/audit, before/after corrections, retries, independent chains, simultaneous approvals, restricted-write refusal and rollback tests.
- HTTP RED: missing route. GREEN: proposal/review/decision/history, strict payloads, navigator-only access, durable stale refusal and decline.
- Browser component RED: missing explicit approval flow. GREEN: 58 component tests, including stable request retry and negative correction history.
- Offline replay and restore integrity updated for 0008; historical migration files preserved. New Core table metadata retains Alembic schema parity without bypassing the database command guards.
- Initial focused API/replay suite: 27 passed. Expanded review-fix suite plus metadata parity: 28 passed.

## Independent review

One fresh reviewer identified four Important issues, no Critical or Minor findings. All four have failing-first regressions and implemented fixes:

1. Authority may expire while waiting for a chain lock: revalidate after acquisition.
2. Repeatable-read snapshots can hide a committed correction: refuse unsupported isolation before commands.
3. Concurrent overlapping role insertion escapes row locks: serialize role-assignment writes against creation authority validation.
4. History can combine an old answer with a newer correction flag: derive leaf, answer and changed flag from one submission snapshot.

Ruling: creation/correction command triggers require READ COMMITTED, the runtime default. Other isolation levels fail closed. Cost: external tools must use the supported command isolation.

Ruling: a database-wide shared/exclusive advisory lock orders creation authority reads and role-assignment write statements, before row locks. This covers newly inserted assignments as well as revocations. Cost: role administration briefly waits for creation transactions across organizations; appropriate for this bounded synthetic demo.

Ruling: frozen 0007 privilege tests explicitly target 0007; separate runtime tests attest the exact 0008 boundary. Current-head assertions and replay manifests advance to 0008. Historical behavior stays covered.

## Final acceptance

The final full verifier completed with exit code 0. Its first pass exposed old head/catalog expectations and ran while review fixes were still being applied; only the settled-code rerun below is acceptance evidence.

- Python: **798 passed**, 485.45 seconds.
- Web components: **58 passed**, 9 files.
- Browser tests: **4 passed**.
- Live API/PostgreSQL journeys: **2 desktop + 2 mobile passed**, including original closed-loop behavior and new reported-need approval with a later negative correction.
- Python lint and type checks, web lint and production build: passed.
- Database integrity checks before/after live journeys: zero violations.
- Runtime startup attested the exact 0008 privilege catalog using the non-owner application login.
- All seven historical migration files match their SHA-256 hashes at e86ed90.
- Main checkout remains at its original commit with its pre-existing plan edit intact. No merge, push, deployment, or persistent ojcc operation was performed.

Command: `python .superpowers/run_verify.py`, a local helper that creates separate disposable API/live targets, sets all three database URLs explicitly, and invokes `scripts/verify.ps1` with the live target triple. Full local output: `.superpowers/full-verify.log`. Both disposable outer databases were dropped after the successful run. The test-only container and its verified anonymous volume were removed after verification, restoring the prior stopped-database environment.

Final review scope rulings: full tests and live browser behavior were verified here after the reviewer left them unjudged; deployment remains outside this implementation request; clinical validity is not claimed for this synthetic demonstration. No review findings remain deferred.
