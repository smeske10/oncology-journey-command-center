"use client";

import { useState } from "react";
import {
  ApiError, createNeedCreationProposal, recordNeedCreationDecision,
  type NeedCreationProposal, type NeedCreationProposalInput, type NeedCreationDecisionInput,
} from "../../lib/api-client";

type Completed = (message: string) => void;

export function PrepareNeedCreation({ rootId, submissionId, onChanged }: {
  rootId: string; submissionId: string; onChanged: Completed;
}) {
  const [rationale, setRationale] = useState("");
  const [proposal, setProposal] = useState<NeedCreationProposal>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState<NeedCreationProposalInput>();

  async function prepare() {
    if (busy) return;
    const payload = attempt ?? { request_id: crypto.randomUUID(), chain_root_id: rootId,
      source_submission_id: submissionId, rationale };
    setAttempt(payload); setBusy(true); setError("");
    try {
      setProposal(await createNeedCreationProposal(payload));
    } catch (e) {
      if (e instanceof ApiError && e.status && e.status < 500) {
        if (e.status === 409) onChanged("This report changed or already has a need. Review the refreshed evidence.");
        else { setAttempt(undefined); setError(e.message); }
      } else setError("The proposal could not be confirmed. Retry the same request.");
    } finally { setBusy(false); }
  }

  if (proposal) return <NeedCreationReview proposal={proposal} onChanged={onChanged} />;
  return <div>
    <label>Reason for proposing this need
      <textarea value={rationale} maxLength={4000} disabled={busy || !!attempt}
        onChange={(event) => setRationale(event.target.value)} style={{ display: "block", width: "100%", boxSizing: "border-box" }} />
    </label>
    <p>Use synthetic information only. Preparing a review saves a proposal; approval creates the need.</p>
    <button type="button" disabled={busy || !rationale.trim()} onClick={() => void prepare()}>
      {busy ? "Preparing…" : attempt ? "Retry preparing review" : "Prepare approval review"}
    </button>
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function NeedCreationReview({ proposal, onChanged }: { proposal: NeedCreationProposal; onChanged: Completed }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState<NeedCreationDecisionInput>();

  async function decide(decision: "approved" | "declined") {
    if (busy) return;
    const payload = attempt ?? { request_id: crypto.randomUUID(), decision, reason: reason || null };
    setAttempt(payload); setBusy(true); setError("");
    try {
      const result = await recordNeedCreationDecision(proposal.id, payload);
      onChanged(result.outcome === "created" ? "Reported need created. Approval and evidence are saved in history." : "Proposal declined. No need was created.");
    } catch (e) {
      if (e instanceof ApiError && e.status && e.status < 500) {
        if (e.status === 409) onChanged("The decision was refused. Review the refreshed report and decision history.");
        else { setAttempt(undefined); setError(e.message); }
      } else setError("The decision could not be confirmed. Retry the same request to recover its result.");
    } finally { setBusy(false); }
  }

  const pending = proposal.state === "pending";
  return <section aria-label={`Creation approval ${proposal.id}`}>
    <h4>Review reported need creation</h4>
    <p><strong>{proposal.patient_display_name}</strong> — create one open transportation reported need.</p>
    <p>{String(proposal.evidence.question.label)} <q>{proposal.evidence.display_text}</q></p>
    <p>Rationale: {proposal.rationale}</p>
    <p>One navigator approval is required. The proposing navigator may approve.</p>
    <p>This authorizes recording the reported need. Tasks, messages, and outcomes require their own decisions.</p>
    <details><summary>Approval evidence and policy</summary>
      <p>Proposal: {proposal.id}</p><p>Patient: {proposal.patient_id}</p><p>Episode: {proposal.care_episode_id}</p>
      <p>Original check-in: {proposal.chain_root_id}</p><p>Reviewed submission: {proposal.source_submission_id}</p>
      <p>Evidence fingerprint: {proposal.evidence_sha256}</p>
      <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(proposal.policy_snapshot, null, 2)}</pre>
    </details>
    {proposal.source_changed && <p role="status">A later correction exists. Review the current check-in; the original evidence and any created need remain unchanged.</p>}
    {proposal.source_changed && <p>Current transportation answer: <strong>{proposal.current_answer_text}</strong>.
      {proposal.current_source_submission_id && <> Current submission: {proposal.current_source_submission_id}.</>}
    </p>}
    {!pending && <p>Proposal status: <strong>{proposal.state.replaceAll("_", " ")}</strong></p>}
    {pending && <>
      <label>Decision note (required to decline)
        <textarea value={reason} disabled={busy || !!attempt} maxLength={4000}
          onChange={(event) => setReason(event.target.value)} style={{ display: "block", width: "100%", boxSizing: "border-box" }} />
      </label>
      <button type="button" disabled={busy || attempt?.decision === "declined"}
        onClick={() => void decide("approved")}>
        {attempt?.decision === "approved" ? "Retry approval" : "Approve and create reported need"}
      </button>{" "}
      <button type="button" disabled={busy || !reason.trim() || attempt?.decision === "approved"}
        onClick={() => void decide("declined")}>
        {attempt?.decision === "declined" ? "Retry decline" : "Decline proposal"}
      </button>
    </>}
    {error && <p role="alert">{error}</p>}
    {proposal.decisions.length > 0 && <ul>{proposal.decisions.map((decision) => <li key={decision.id}>
      {decision.outcome.replaceAll("_", " ")} — {new Date(decision.authorized_at).toLocaleString()}
      <details><summary>Decision details</summary>
        <p>Navigator: {decision.authorized_by_user_id}</p><p>Authority: {decision.qualifying_role_assignment_id}</p>
        <p>Decision: {decision.id}</p>{decision.reason && <p>Reason: {decision.reason}</p>}
        {decision.reported_need_id && <p>Reported need: {decision.reported_need_id}</p>}
      </details>
    </li>)}</ul>}
  </section>;
}
