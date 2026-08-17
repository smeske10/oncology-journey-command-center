# Oncology Journey Command Center

**Product design specification**  
**Date:** August 17, 2026  
**Status:** Ready for stakeholder review  
**Target:** Public portfolio release in four weeks, with up to two optional refinement weeks

## 1. Executive summary

The Oncology Journey Command Center is a two-sided care-navigation application for people receiving active breast cancer treatment and the nurse navigators who support them between visits.

Patients complete short, adaptive check-ins about symptoms, medication questions, distress, upcoming visits, and practical barriers. The system converts those reports into structured needs, applies deterministic safety rules, invokes narrow AI agents for bounded reasoning tasks, and presents an explainable proposed action plan to a nurse navigator. The navigator remains responsible for approving, editing, assigning, escalating, or declining every care-related action. Each need stays open until a patient or navigator records its disposition.

The public portfolio release uses synthetic data only. It demonstrates production-minded product design, full-stack engineering, agentic orchestration, interoperability, safety governance, evaluation, and measurable workflow impact without claiming clinical efficacy, HIPAA compliance, or suitability for real patient care.

## 2. Product decision

### Problem

Important patient needs arise between oncology visits, while nurse navigators face fragmented inputs, limited capacity, and difficulty identifying which patients need attention first. Traditional portals create another inbox, symptom tools often stop at collection and alerts, and generic chatbots do not own the work required to move a reported need toward resolution.

### Product thesis

A safe, effective navigation product should not merely answer questions or generate alerts. It should create a closed operational loop:

1. Capture the patient's need with minimal burden.
2. Detect safety concerns before generative reasoning.
3. Organize the need using explicit supporting evidence.
4. Propose the next navigation actions.
5. Preserve human decision authority.
6. Track the need until it is resolved or explicitly closed.

### Primary users

- **Patient:** An adult receiving active systemic treatment for breast cancer.
- **Primary operator:** A nurse navigator managing a panel of oncology patients.
- **Supporting actors:** Oncology nurses, advanced practice providers, social workers, scheduling staff, and administrators. Supporting-actor workflows are represented through assignments and dispositions, not separate full applications in the first release.

### North-star outcome

Reduce the time from a patient-reported need to an appropriate, navigator-reviewed next action.

For the synthetic public release, "appropriate" means that the action matches the labeled scenario rubric, respects the product's scope and safety policies, cites approved content when factual information is included, and is reviewed by the designated human role.

### Secondary product measures

- Time from check-in submission to navigator review
- Time from reported need to documented resolution
- Percentage of needs resolved before the next scheduled visit
- Navigator acceptance, editing, reassignment, escalation, and decline rates
- Aging of unresolved navigation tasks
- Patient check-in completion and follow-up response rates
- Patient-reported confidence and preparedness in simulated usability sessions

### Guardrail measures

- Recall of deterministic urgent-case routing in the labeled synthetic safety set
- Number of patient-facing clinical messages sent without navigator approval
- Citation completeness for displayed factual claims
- Percentage of failed background jobs that remain visible and recoverable
- Role-based access and tenant-isolation failures
- Rate of unsupported, out-of-scope, or uncited agent outputs reaching the navigator

## 3. Release scope

### Included in the four-week public release

- One active-treatment breast cancer pathway
- Responsive patient web application
- Nurse navigator command center
- Adaptive check-ins with structured questions and free text
- Three complete scenario families:
  - Worsening patient-reported symptom
  - Medication uncertainty that requires care-team follow-up
  - Practical barrier such as transportation or financial support
- A patient journey timeline showing check-ins, needs, tasks, decisions, and outcomes
- Deterministic policy and safety engine
- Bounded agent orchestration with structured outputs
- Navigator approval, editing, reassignment, escalation, and decline controls
- Closed-loop follow-up and resolution tracking
- FHIR-shaped clinical data and an import/export adapter
- Governed, versioned knowledge content with citations
- Audit trail, agent traces, and evaluation results
- Twelve to twenty synthetic patients with longitudinal histories
- Public recruiter sandbox with a known reset state
- Portfolio case study explaining discovery, tradeoffs, architecture, safety, metrics, and lessons

### Deliberately excluded

- Real protected health information or real patient use
- Diagnosis, treatment recommendations, medication dosing, or interpretation of test results
- Claims of HIPAA compliance, clinical validation, or improved clinical outcomes
- Live EHR integration
- Live clinical paging, SMS, or autonomous clinical outreach
- Multiple cancer pathways or a condition-neutral platform
- Native mobile applications
- Billing, claims, payer, or reimbursement workflows
- Predictive risk scoring presented as a clinical conclusion
- Unrestricted web search or retrieval from unapproved sources

### Optional refinement weeks

Weeks five and six, if used, are reserved for accessibility polish, performance tuning, richer observability, additional synthetic scenarios, visual refinement, and portfolio content. They do not add new clinical pathways or core workflow types.

## 4. Core experiences

### 4.1 Patient application

The patient experience is calm, mobile-first, and task-oriented. It avoids a blank chatbot as the primary interface.

#### Main capabilities

- Secure synthetic demo access
- Today's check-in and follow-up tasks
- One question per screen with visible progress
- Adaptive branching based on prior structured responses
- Free-text context after structured questions
- Clear urgent-care and product-scope language
- Review and submit before data is finalized
- Journey timeline containing reported needs, navigator responses, approved resources, and resolution status
- Approved educational content with source and review information
- Patient confirmation that a need is resolved, unresolved, or still needs help

#### Patient interaction rules

- The product never asks the patient to converse indefinitely with a general-purpose assistant.
- Urgent-language detection occurs before agent orchestration.
- Preapproved emergency or urgent-care language is deterministic and organization-configurable.
- The public sandbox clearly identifies itself as a simulation and does not impersonate a real care team.
- Clinical, medication, or symptom-management messages cannot be released by an agent.

### 4.2 Navigator command center

The navigator experience is a prioritized work environment, not a transcript viewer or another undifferentiated inbox.

#### Main capabilities

- Queue sorted by deterministic safety state, due time, change severity, unresolved-task age, and configurable operational priority
- Filters for need type, status, owner, pathway, and due time
- Patient header with synthetic diagnosis, active pathway, upcoming visit, and consent status
- Longitudinal summary of what changed since the last check-in
- Exact patient evidence that supports each extracted need
- Explanation of each priority rule that matched
- Proposed navigation plan with tasks, owners, due times, and approved resources
- Accept, edit, assign, escalate, decline, or request-more-information actions
- Patient-facing message preview and approval gate
- Resolution disposition with outcome reason
- Complete history of agent outputs, source versions, human edits, and overrides

### 4.3 Administrator and evaluation view

The first release includes a limited internal view rather than a third full product surface.

- Seed and reset demo data
- Inspect workflow failures and dead-letter jobs
- Review agent traces without exposing hidden reasoning text
- Run and compare evaluation sets
- Inspect source versions and review dates
- View aggregate operational metrics for the synthetic tenant

## 5. End-to-end workflow

1. The patient opens a scheduled check-in.
2. The application loads the pathway-defined questionnaire and relevant prior answers.
3. The patient completes structured questions and may add free text.
4. The server validates the response, records consent and provenance, and stores an immutable submitted version.
5. The deterministic policy engine checks emergency language, configured thresholds, access permissions, and prohibited actions.
6. If an urgent rule matches, the system shows preapproved instructions, creates a high-priority navigator task, and skips any generative step that could delay routing.
7. If the response may continue, the workflow coordinator creates an idempotent orchestration job.
8. Narrow agents structure needs, describe longitudinal changes, retrieve approved information, match nonclinical resources, and draft proposed navigation tasks.
9. A quality gate validates schemas, citations, allowed actions, and policy compliance.
10. Valid output is attached to a navigator-review task. Invalid or incomplete output enters a visible manual-review state.
11. The navigator reviews the patient evidence and proposed plan, then accepts, edits, assigns, escalates, declines, or requests more information.
12. Approved patient-facing content is released, while internal tasks are routed to their owners.
13. Follow-up checks whether the need has been resolved.
14. Patient confirmation and navigator disposition close the need, while the full decision trail remains auditable.

## 6. Agentic orchestration

### 6.1 Design principle

The workflow system owns state, safety, permissions, and transitions. Language models perform narrow reasoning tasks inside that workflow. No model can directly change an escalation state, close a need, send clinical guidance, or create an unapproved external action.

### 6.2 Workflow coordinator

The coordinator is a deterministic state machine, not a conversational supervisor agent. It:

- Reads the current workflow state
- Selects the next allowed task
- Creates jobs with idempotency keys
- Enforces timeouts and bounded retries
- Records inputs, outputs, model configuration, tool use, and validation results
- Routes failures to a recoverable manual-review state
- Prevents duplicate tasks and messages

### 6.3 Bounded agent roles

#### Intake structurer

Maps structured answers and free text into typed navigation needs. It preserves exact supporting evidence and classifies workflow needs, not diagnoses.

#### Change detector

Compares the current check-in with prior patient-reported information and produces a factual change summary. It cannot infer unreported symptoms, causes, or clinical conclusions.

#### Knowledge retriever

Retrieves only approved, versioned content. It returns source identifiers, passages, review dates, and retrieval scores. If no supported answer exists, it returns a structured decline.

#### Resource matcher

Matches patient-stated nonclinical constraints to vetted services such as transportation, financial counseling, food support, lodging, or peer support.

#### Action drafter

Creates proposed navigation tasks and patient-facing language from validated structured inputs. Anything clinical or medication-related remains blocked until an authorized human approves it.

#### Quality validator

Runs schema, citation, scope, and policy checks. Deterministic checks are implemented as services rather than prompts. A model-based evaluator may add a review signal but cannot override a failed deterministic check.

### 6.4 Agent output contract

Every agent response uses a versioned schema containing:

- Agent and schema version
- Source workflow and patient identifiers
- Structured result
- Supporting patient evidence identifiers
- Source document identifiers and versions when applicable
- Confidence or uncertainty field used for review support only
- Policy flags
- Validation status
- Timestamp and trace identifier

The interface never displays hidden chain-of-thought. It displays concise reasons, inputs, source evidence, applied rules, and human-readable decision summaries.

## 7. System architecture

### 7.1 Architectural style

The public release uses a modular monolith plus an asynchronous worker. This is intentionally simpler than microservices while preserving strong boundaries between user experience, domain workflow, orchestration, safety, data, and evaluation.

### 7.2 Components

- **Web application:** TypeScript and React-based responsive application with separate patient, navigator, and limited administrator routes.
- **Application API:** Typed API responsible for authentication, authorization, tenant scope, validation, and domain commands.
- **Workflow service:** Durable state machine for check-ins, needs, tasks, approvals, and resolutions.
- **Orchestration worker:** Python service for model calls, retrieval, resource matching, and structured validation.
- **Policy engine:** Deterministic rules for urgency, permissions, prohibited actions, and release gates.
- **Job queue:** Postgres-backed asynchronous jobs with idempotency keys, timeouts, bounded retries, and dead-letter handling.
- **Primary database:** Managed Postgres with row-level authorization, relational domain data, and vector indexing for approved knowledge retrieval.
- **Object storage:** Versioned approved documents and synthetic attachments.
- **FHIR adapter:** Maps the internal domain model to selected FHIR resources without making the application depend on a live EHR.
- **Observability layer:** Structured logs, metrics, distributed traces, agent-run records, and operational alerts.
- **Provider adapter:** A narrow interface around language-model and embedding providers so models can be replaced without changing domain workflows.

### 7.3 Deployment shape

- Static and server-rendered web assets deploy to managed web hosting.
- The API and orchestration worker deploy as separate containerized processes from the same repository.
- The database, object storage, and job queue use managed services where practical.
- Preview, staging, and production environments have separate databases and credentials.
- The public sandbox contains only synthetic data and resets on a schedule or by an administrator action.

## 8. Data model and interoperability

### 8.1 Core domain entities

- Organization
- User
- RoleAssignment
- SyntheticPatient
- CareEpisode
- PathwayDefinition
- CheckInDefinition
- CheckInSubmission
- ReportedNeed
- SafetySignal
- NavigationTask
- ApprovalDecision
- Resource
- KnowledgeDocument
- AgentRun
- Outcome
- AuditEvent

### 8.2 Key relationships

- A patient has one or more care episodes.
- A care episode follows one versioned pathway definition.
- A submitted check-in creates zero or more reported needs and safety signals.
- Each reported need has a lifecycle independent of the check-in that created it.
- A reported need can own multiple navigation tasks, but it closes only through an explicit outcome.
- Every agent run belongs to one workflow transition and retains input, output, validation, and source metadata.
- Every approval decision records the proposed value, final value, authorized user, timestamp, and reason when edited or declined.

### 8.3 FHIR-shaped representation

- Patient identity and demographics map to `Patient`.
- Submitted check-ins map to `QuestionnaireResponse`.
- Searchable or trended patient-reported answers map to `Observation` and are tagged as patient-supplied.
- Active navigation and treatment-context summaries map to `CarePlan` where appropriate.
- Navigator work maps to `Task`.
- Source and transformation lineage map to `Provenance` where appropriate.

The relational domain model remains the application's source of truth. FHIR is an explicit interoperability boundary, not a replacement for workflow-specific storage.

## 9. Safety, privacy, and trust

### 9.1 Product boundary

The product supports navigation and operational coordination. It does not diagnose, recommend treatment, adjust medications, interpret tests, or determine that a patient is clinically safe.

### 9.2 Safety controls

- Deterministic urgent-language and configured-threshold rules execute before model calls.
- A model may add a higher-concern review signal but cannot lower or clear a deterministic escalation.
- Clinical or medication-related patient messages require navigator approval.
- Agent tools use least-privilege, task-specific access.
- Retrieval is limited to approved and currently valid sources.
- Source review dates and versions are retained with each generated artifact.
- Unsupported questions produce a decline and human-review task.
- All critical state transitions are auditable.
- The public demo displays clear simulation language and does not invite real health information.

### 9.3 Security posture for the public sandbox

- Synthetic data only
- Tenant-scoped access and row-level authorization
- Short-lived sessions and secure cookie settings
- Secrets stored outside the repository
- Input validation and output encoding
- Rate limits for authentication and model-backed endpoints
- Dependency, secret, and static-analysis checks in continuous integration
- Content Security Policy and restrictive cross-origin configuration
- Audit events for authentication, authorization, exports, approvals, and administrator actions
- Automated reset to a known synthetic state

This posture demonstrates healthcare-aware engineering practices but is not represented as HIPAA compliance.

## 10. Error handling and degraded states

### Invalid patient input

The application preserves the draft, highlights the invalid field, and requests correction. A partially completed check-in does not create navigation needs.

### Urgent rule match

The system immediately displays preapproved organization-configured instructions, creates the navigator task, records the matched rule, and avoids any generative dependency in the routing path.

### Model timeout or provider failure

The job retries with the same idempotency key up to the configured limit. If it still fails, the workflow enters manual review with the original patient submission intact.

### Invalid agent schema

The output is rejected. The system may perform one repair attempt through the configured structured-output mechanism. Continued failure creates a manual-review task and operational alert.

### Missing or outdated source

The system does not draft a factual answer. It records a structured decline and asks the navigator to respond manually.

### Conflicting or ambiguous patient information

The system preserves both statements, does not reconcile them as fact, and proposes a clarification task. If a deterministic concern is present, the higher-priority state remains active.

### Notification failure

The underlying task remains open. Delivery status is visible, retries are bounded, and the navigator receives an operational alert before the task can be considered complete.

### Database or queue degradation

New submissions fail closed with a clear retry message unless they can be durably stored. The system never tells the patient a check-in was submitted until persistence succeeds.

## 11. Evaluation and testing

### 11.1 Deterministic test suite

- Unit tests for domain rules and state transitions
- Property-based tests for invalid transition sequences and idempotency
- Authorization tests for every role and tenant boundary
- Policy-engine tests covering urgent routing and prohibited actions
- FHIR mapping and schema-validation tests
- Queue retry, timeout, and dead-letter tests
- Audit completeness tests for critical commands

### 11.2 Agent golden sets

The repository contains versioned, labeled synthetic cases covering each scenario family, common benign variations, ambiguous reports, and safety-critical cases.

Agent evaluations measure:

- Need-extraction precision and recall
- Evidence-span accuracy
- Longitudinal-change faithfulness
- Retrieval relevance
- Citation completeness
- Resource-match constraint satisfaction
- Action-draft policy compliance
- Appropriate decline behavior

### 11.3 Adversarial tests

- Prompt injection embedded in patient free text
- Instructions embedded in retrieved documents
- Contradictory structured and unstructured responses
- Negation, misspellings, slang, and indirect urgency language
- Outdated, revoked, or missing approved content
- Cross-patient and cross-tenant data access attempts
- Model, database, queue, and notification failures
- Duplicate submissions and replayed jobs

### 11.4 End-to-end tests

- Routine check-in through approved response and resolution
- Urgent route that does not depend on a model call
- Medication question routed to human review
- Practical barrier matched to a vetted resource and closed
- Agent failure entering a visible manual-review state
- Navigator edit preserving both original and final values
- Patient confirmation reopening an unresolved need
- Demo reset restoring all seeded journeys
- Keyboard-only and screen-reader-critical flows

### 11.5 Public release gates

- All labeled urgent synthetic cases reach human review.
- No clinical or medication-related message can be sent without authorized approval.
- Every displayed factual claim from the knowledge workflow includes an approved source reference.
- Every failed asynchronous job remains visible and recoverable.
- No role or tenant isolation test fails.
- The complete seeded journey passes in supported desktop and mobile viewport tests.

These are engineering gates for synthetic cases, not evidence of clinical safety or efficacy.

## 12. Analytics and observability

### Product events

- Check-in started, saved, submitted, and abandoned
- Need created, prioritized, reviewed, assigned, escalated, and closed
- Proposed action accepted, edited, declined, or reassigned
- Patient message approved, delivered, failed, and acknowledged
- Follow-up completed and resolution confirmed
- Source opened and citation inspected

### Operational signals

- API latency and error rate
- Job queue age and completion rate
- Model latency, error rate, and cost by agent
- Schema-validation and policy-failure rates
- Retrieval quality and unsupported-answer rate
- Manual-review backlog and dead-letter count
- Navigator override and edit patterns

### Traceability

One trace identifier links the patient submission, policy evaluation, agent runs, source retrieval, validation results, navigator decision, released action, and outcome. Sensitive reasoning text is not required or exposed; traceability comes from structured inputs, outputs, rules, evidence, and decisions.

## 13. Accessibility and experience quality

- Meet WCAG 2.2 AA for the public release where testable.
- Support keyboard navigation, visible focus, semantic landmarks, and accessible form errors.
- Do not communicate priority or status through color alone.
- Use plain language and progressive disclosure in the patient experience.
- Keep check-ins short and preserve progress.
- Test at mobile, tablet, and desktop breakpoints.
- Respect reduced-motion settings.
- Provide clear empty, loading, failed, offline, and manual-review states.

## 14. Four-week delivery sequence

### Week 1: Foundation and patient submission

- Repository, environments, continuous integration, and design system
- Domain schema, synthetic tenant, authentication, and authorization
- Patient check-in flow with draft and submission states
- Seed data and initial FHIR mapping
- Unit, accessibility, and end-to-end test harnesses

### Week 2: Navigator workflow and closed loop

- Prioritized navigator queue
- Patient evidence and longitudinal change views
- Proposed-action review controls
- Task ownership, due dates, and resolution states
- Patient follow-up and journey timeline

### Week 3: Agentic orchestration and safety

- Workflow coordinator and asynchronous jobs
- Deterministic policy engine
- Bounded agents and provider adapter
- Governed knowledge retrieval and resource matching
- Audit trail, traces, error states, and manual-review routing

### Week 4: Evaluation, hardening, and public release

- Golden-set and adversarial evaluations
- Authorization, failure-injection, performance, and accessibility testing
- Seeded recruiter demo and automated reset
- Operational metrics and evaluation view
- Deployment hardening, documentation, and portfolio case study

### Optional weeks 5–6

- Visual polish and usability refinements
- Additional synthetic scenarios and richer observability
- Performance and cost tuning
- Recorded walkthrough and interview-ready case-study assets

## 15. Demonstration script

The seeded public demo should tell one coherent story in under five minutes:

1. Open a synthetic patient's mobile check-in.
2. Report worsening nausea, a medication question, and a transportation concern.
3. Submit and show the deterministic policy decision.
4. Switch to the navigator command center.
5. Show how the case is prioritized and which exact evidence supports it.
6. Inspect the proposed tasks, approved sources, and agent trace.
7. Edit and approve the appropriate navigation actions.
8. Return to the patient experience and confirm follow-up.
9. Close one need while leaving another visibly unresolved.
10. Open the evaluation view to show safety gates, golden-set results, and failure recovery.

## 16. Portfolio narrative

The central interview statement is:

> I designed and built an oncology navigation system that turns patient-reported needs into explainable, navigator-approved actions, then measures whether those needs were resolved before the next visit.

The case study should make six product decisions explicit:

1. Why the product optimizes for need resolution rather than chatbot engagement
2. Why the nurse navigator is the primary operator
3. Why deterministic safety sits outside generative reasoning
4. Why the system uses bounded agents rather than one broad assistant
5. Why the first release uses one pathway and synthetic data
6. Which evidence would be required before piloting with a real oncology organization

## 17. Future expansion, not part of the first release

- Additional breast cancer treatment pathways
- Other oncology conditions
- Real EHR connectivity through standards-based interfaces
- Organization-configurable questionnaires and escalation policies
- Multilingual patient experiences
- Caregiver participation and delegated access
- Community-resource referral integrations
- Real-world navigator usability research
- Prospective clinical and operational evaluation

Each expansion requires separate discovery, safety review, and implementation planning.

## 18. Evidence base and external references

- [CMS Enhancing Oncology Model](https://www.cms.gov/priorities/innovation/innovation-models/eom): patient navigation, electronic patient-reported outcomes, social-needs screening, care plans, and continuous quality improvement.
- [President's Cancer Panel, Enhancing Patient Navigation with Technology](https://prescancerpanel.cancer.gov/reports-meetings/enhancing-patient-navigation-2024/achieving-equity-cancer-care): navigation needs, capacity constraints, equity, and responsible uses of technology.
- [PRO-TECT cluster-randomized trial](https://pubmed.ncbi.nlm.nih.gov/39920394/): outcomes from electronic patient-reported symptom monitoring during cancer treatment.
- [FDA Clinical Decision Support Software guidance, January 2026](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/clinical-decision-support-software): current regulatory framing for decision-support software functions.
- [HL7 US Core screening and assessment guidance](https://hl7.org/fhir/us/core/STU7/screening-and-assessments.html): representation of questionnaires, questionnaire responses, and searchable observations.

## 19. Approval criteria for implementation planning

This specification is ready to move into implementation planning when the reviewer confirms:

- The primary user and patient journey are correct.
- The north-star outcome is the intended portfolio impact story.
- The four-week release boundary is credible.
- The product's navigation-only clinical boundary is acceptable.
- The agent, deterministic-policy, and human-approval responsibilities are clear.
- The synthetic-data and public-demo strategy is acceptable.
- No required first-release capability is missing.

