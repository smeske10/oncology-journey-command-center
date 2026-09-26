# Navigator approval to create a reported need

Approved in conversation on 2026-09-24, followed by explicit authorization to implement.

## Contract

One currently authorized navigator may propose and approve creation of one open transportation reported need from the exact current leaf of one independent check-in chain. Self-approval is permitted. Approval creates the need in the same transaction. No task, message, priority, clinical interpretation, recurrence, or outcome is authorized.

The browser sends chain root, exact reviewed submission ID, and rationale. The database derives organization/patient/episode, questionnaire identity/version, question and stored answer, initial state, versioned canonical JSON evidence and SHA-256 digest. The immutable proposal contains proposer authority and an immutable policy snapshot (one navigator, self-approval allowed). The final decision records approver authority, timestamp, proposal and created need. An explicit declined decision includes a reason.

## Ordering, corrections and duplicates

Proposal and approval commands serialize against correction insertion on the chain root. Revalidate after acquiring the lock. A correction committed first makes the old evidence ineligible even when its answer is still yes. Approval committed first leaves the created need and its original evidence intact; a subsequent correction is shown as changed evidence requiring review. No automatic closure or rewrite.

Commands require READ COMMITTED so post-lock reads see committed corrections; other isolation levels fail closed. Role-assignment write statements take an exclusive authority advisory lock before row locks; creation commands hold the shared lock, preventing newly inserted overlaps as well as revocations during authority validation. Revalidate authority after waiting for the chain lock to catch scheduled expiry.

An existing need sourced anywhere in the chain blocks a second creation; independent check-ins remain eligible even with other transportation needs in the episode. Existing historical records are not rewritten. A retry with the same request identity returns its original result; conflicting reuse is rejected. Concurrent independent proposals for one chain can create at most one need.

## Audit and authority

New proposal and decision records are append-only, with organization-aware relationships. The runtime remains unable to INSERT/UPDATE/DELETE reported_need directly. Fixed-search-path database trigger functions derive and validate command records. Active navigator authority must have exact cardinality, including an active user; ambiguity fails closed. Role revocation serializes against authority validation and historical authority cannot be rewritten.

Successful decisions and resulting needs commit together. Authorized stale/duplicate approval attempts commit as refused decision records, with an audit event, then return a conflict to the browser; they are not lost through a rollback. Unknown/foreign targets and invalid authority do not disclose evidence. Invalid proposal payloads are refused, not represented as successful proposals.

## Presentation

Keep related needs visible. An eligible candidate offers proposal creation with a rationale, followed by review of the stored evidence and an explicit approve-and-create or decline action. Show immutable decision history and subsequent corrections. Refresh clears stale client selections. Synthetic-only text validation remains in force.

## Constraints and validation

Preserve master, other worktrees, and migrations 0001–0007. Add migration 0008 and a versioned privilege contract. Never connect to or modify persistent ojcc. Use named disposable databases, restricted application login, real PostgreSQL concurrency tests, HTTP tests, browser tests, static checks and contract regeneration. No merge, publish or deployment is included.
