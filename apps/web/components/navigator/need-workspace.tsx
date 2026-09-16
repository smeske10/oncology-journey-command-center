"use client";

import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import {
  ApiError,
  claimTask,
  completeTask,
  decideProposal,
  getOutcomePreview,
  recordOutcome,
  startTask,
  type NavigatorNeedWorkspaceResponse,
  type OutcomePreviewResponse,
} from "../../lib/api-client";
import { JourneyTimeline } from "../journey-timeline";

type NeedWorkspaceProps = {
  error?: string;
  onRefresh: () => Promise<void>;
  state?: "loading";
  workspace?: NavigatorNeedWorkspaceResponse;
};

export function NeedWorkspace({ error, onRefresh, state, workspace }: NeedWorkspaceProps) {
  const [selectedProposalId, setSelectedProposalId] = useState("");
  const [declineReason, setDeclineReason] = useState("");
  const [dueLocal, setDueLocal] = useState("");
  const [pending, setPending] = useState("");
  const [message, setMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [outcomeDisposition, setOutcomeDisposition] = useState<"resolved" | "closed_unresolved" | "">("");
  const [outcomeNote, setOutcomeNote] = useState("");
  const [preview, setPreview] = useState<OutcomePreviewResponse>();
  const [cancelledTaskIds, setCancelledTaskIds] = useState<string[]>([]);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const outcomeCommandRef = useRef<{ signature: string; key: string } | undefined>(undefined);

  const selected = (() => {
    if (!workspace) return undefined;
    for (const task of workspace.tasks) {
      const proposal = task.proposals.find((item) => item.id === selectedProposalId);
      if (proposal) return { proposal, task };
    }
    return undefined;
  })();

  useEffect(() => {
    if (actionError) errorRef.current?.focus();
  }, [actionError]);

  if (state === "loading") {
    return <section aria-label="Selected need workspace"><p aria-live="polite">Loading selected need…</p></section>;
  }
  if (error) {
    return (
      <section aria-label="Selected need workspace" style={panelStyle}>
        <p role="alert">{error}</p>
        <button onClick={() => void onRefresh()} type="button">Restore navigator session</button>
      </section>
    );
  }
  if (!workspace) {
    return <section aria-label="Selected need workspace"><p>Select a need to review its workspace.</p></section>;
  }
  const data = workspace;

  const approvedSelection = selected?.proposal.state === "approved" && selected.proposal.supported;
  const task = selected?.task ?? workspace.tasks[0];
  const hasPendingReview = workspace.tasks.some((item) =>
    item.proposals.some((proposal) => proposal.state === "pending" && proposal.reviewable),
  );

  async function runAction(label: string, action: () => Promise<unknown>) {
    if (pending) return;
    setActionError("");
    setMessage("");
    setPending(label);
    try {
      await action();
      await onRefresh();
    } catch (caught: unknown) {
      setActionError(actionErrorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) await onRefresh();
    } finally {
      setPending("");
    }
  }

  function submitDecision(decision: "approved" | "declined") {
    if (!selected) {
      setActionError("Select one proposal before making a decision.");
      return;
    }
    const reason = declineReason.trim();
    if (decision === "declined" && !reason) {
      setActionError("A decline reason is required.");
      return;
    }
    void runAction("decision", async () => {
      const result = await decideProposal(selected.proposal.id, {
        decision,
        reason: decision === "declined" ? reason : undefined,
      });
      setMessage(`Proposal ${result.proposal_state}.`);
    });
  }

  function claimSelectedTask() {
    if (!selected || !approvedSelection) {
      setActionError("Select a supported approved proposal before claiming the task.");
      return;
    }
    const dueAt = awareIsoFromLocal(dueLocal);
    if (!dueAt) {
      setActionError("Enter a valid task due time.");
      return;
    }
    void runAction("claim", async () => {
      const result = await claimTask(selected.task.id, selected.proposal.id, dueAt);
      setMessage(`Task ${statusLabel(result.status)}.`);
    });
  }

  function transitionTask(transition: "start" | "complete") {
    if (!task) return;
    void runAction(transition, async () => {
      const result = transition === "start" ? await startTask(task.id) : await completeTask(task.id);
      setMessage(
        transition === "complete" && result.follow_up_request_id
          ? "Task completed. Follow-up requested."
          : `Task ${statusLabel(result.status)}.`,
      );
    });
  }

  function previewClosure() {
    void runAction("preview", async () => {
      setPreview(await getOutcomePreview(data.need.id));
      setMessage("Closure preview loaded. It may change before confirmation.");
    });
  }

  function confirmOutcome() {
    if (!outcomeDisposition) {
      setActionError("Select an outcome before confirming closure.");
      return;
    }
    if (looksLikePhi(outcomeNote.trim())) {
      setActionError("Remove names, contact details, or record numbers from the outcome note.");
      return;
    }
    const payload = { disposition: outcomeDisposition, note: outcomeNote.trim() || undefined };
    const signature = JSON.stringify(payload);
    if (!outcomeCommandRef.current || outcomeCommandRef.current.signature !== signature) {
      outcomeCommandRef.current = { signature, key: newIdempotencyKey() };
    }
    const key = outcomeCommandRef.current.key;
    void runAction("outcome", async () => {
      const result = await recordOutcome(data.need.id, payload, key);
      setCancelledTaskIds(result.cancelled_task_ids);
      setMessage(`Need closed as ${result.disposition.replace("_", " ")}.`);
    });
  }

  return (
    <section aria-label="Selected need workspace" style={panelStyle}>
      <header>
        <p style={eyebrow}>Selected need workspace</p>
        <h2>{workspace.need.patient_display_name}: {humanize(workspace.need.kind)}</h2>
        <p>Canonical state: <strong>{workspace.need.effective_state}</strong></p>
        <button disabled={Boolean(pending)} onClick={() => void onRefresh()} type="button">
          Refresh selected need
        </button>
      </header>

      {(actionError || message) && (
        <p
          aria-live="assertive"
          ref={errorRef}
          role={actionError ? "alert" : "status"}
          tabIndex={-1}
        >
          {actionError || message}
        </p>
      )}

      <section aria-label="Exact evidence">
        <h3>Exact evidence</h3>
        <p>When a source submission is available, this shows evidence from the active submission in the selected need&apos;s source chain. Otherwise, stored or inherited need evidence is shown. This chain may belong to an earlier check-in.</p>
        <ul>{workspace.evidence.map((item) => (
          <li key={`${item.field_identifier}:${item.source_submission_id}`}>
            <span>{item.display_text}</span>
            <p>{evidenceProvenance(item)}</p>
          </li>
        ))}</ul>
      </section>

      <section>
        <h3>Submission comparisons</h3>
        <SubmissionComparison comparison={workspace.comparisons.between_check_ins} kind="independent" />
        <SubmissionComparison comparison={workspace.comparisons.correction} kind="correction" />
      </section>

      <section>
        <h3>Governed task proposals</h3>
        {!hasPendingReview && !workspace.tasks.some((item) => item.proposals.length) && <p>No task proposals are available.</p>}
        {workspace.tasks.flatMap((item) => item.proposals.map((proposal) => (
          <article key={proposal.id} style={cardStyle}>
            <label>
              <input
                checked={selectedProposalId === proposal.id}
                name="selected-proposal"
                onChange={() => setSelectedProposalId(proposal.id)}
                type="radio"
              />
              {proposal.title ?? "Untitled task proposal"}
            </label>
            <p>Proposal state: {proposal.state}</p>
            <p>{proposal.rationale}</p>
            <p>{policyLabel(proposal.policy)}</p>
            {!proposal.supported && <p role="note">Unsupported proposal: {proposal.unsupported_reason}</p>}
            <ul>
              {proposal.resources.map((resource) => (
                <li key={resource.id}>
                  <strong>{resource.name}</strong> — {resource.match_rationale}
                  {resource.url && <> · <a href={resource.url}>Resource details</a></>}
                </li>
              ))}
            </ul>
          </article>
        )))}
        <label htmlFor="decline-reason">Decline reason</label>
        <textarea
          disabled={Boolean(pending)}
          id="decline-reason"
          onChange={(event) => setDeclineReason(event.target.value)}
          value={declineReason}
        />
        <div style={buttonRow}>
          <button disabled={!selected?.proposal.reviewable || Boolean(pending)} onClick={() => submitDecision("approved")} type="button">Approve proposal</button>
          <button disabled={!selected?.proposal.reviewable || Boolean(pending)} onClick={() => submitDecision("declined")} type="button">Decline proposal</button>
        </div>
      </section>

      {task && (
        <section>
          <h3>Task execution</h3>
          <p>{task.title} — {statusLabel(task.status)}</p>
          {task.due_at && <p>Due: {formatStoredInstant(task.due_at)}</p>}
          {task.status === "open" && (
            <>
              <label htmlFor="task-due">Task due time</label>
              <input id="task-due" onChange={(event) => setDueLocal(event.target.value)} type="datetime-local" value={dueLocal} />
              <p>Times are entered in {localTimezone()} and sent with an explicit UTC offset.</p>
              <button disabled={!approvedSelection || Boolean(pending)} onClick={claimSelectedTask} type="button">Claim task</button>
              {!approvedSelection && <p>Select a supported approved proposal to claim this task.</p>}
            </>
          )}
          {task.status === "assigned" && (
            <button disabled={Boolean(pending) || !isAwareInstant(task.due_at)} onClick={() => transitionTask("start")} type="button">Start task</button>
          )}
          {task.status === "assigned" && !isAwareInstant(task.due_at) && <p role="alert">Task due time from the API is missing a timezone offset.</p>}
          {task.status === "in_progress" && <button disabled={Boolean(pending)} onClick={() => transitionTask("complete")} type="button">Complete task</button>}
        </section>
      )}

      <section>
        <h3>Patient follow-up evidence</h3>
        {!workspace.follow_ups.length && <p>No patient response is available yet. Closure remains available when operationally appropriate.</p>}
        {workspace.follow_ups.map((followUp) => (
          <p key={followUp.request_id}>
            {followUp.response ? `Patient responded: ${followUp.response.replaceAll("_", " ")}` : "Follow-up requested; awaiting patient response."}
          </p>
        ))}
      </section>

      <section>
        <h3>Record outcome</h3>
        {workspace.outcome ? (
          <p>Outcome recorded. Need closed as {workspace.outcome.disposition.replaceAll("_", " ")}.</p>
        ) : (
          <>
            <label><input checked={outcomeDisposition === "resolved"} name="outcome" onChange={() => setOutcomeDisposition("resolved")} type="radio" />Resolved outcome</label>
            <label><input checked={outcomeDisposition === "closed_unresolved"} name="outcome" onChange={() => setOutcomeDisposition("closed_unresolved")} type="radio" />Closed unresolved outcome</label>
            <label htmlFor="outcome-note">Outcome note (optional)</label>
            <textarea id="outcome-note" onChange={(event) => setOutcomeNote(event.target.value)} value={outcomeNote} />
            <div style={buttonRow}>
              <button disabled={Boolean(pending)} onClick={previewClosure} type="button">Preview closure</button>
              <button disabled={!preview || Boolean(pending)} onClick={confirmOutcome} type="button">Confirm outcome</button>
            </div>
            {preview && (
              preview.tasks.length
                ? <p>Active tasks that may be cancelled: {preview.tasks.map((item) => item.title).join(", ")}</p>
                : <p>No active tasks will be cancelled.</p>
            )}
            {cancelledTaskIds.length > 0 && <p>Actually cancelled task IDs: {cancelledTaskIds.join(", ")}</p>}
          </>
        )}
      </section>

      <JourneyTimeline events={workspace.timeline} />
    </section>
  );
}

function SubmissionComparison({ comparison, kind }: {
  comparison: NavigatorNeedWorkspaceResponse["comparisons"]["between_check_ins"];
  kind: "independent" | "correction";
}) {
  const independent = kind === "independent";
  const deltas = comparison.deltas ?? [];
  return (
    <section aria-label={independent ? "Latest independent check-ins" : "Selected need correction"} style={cardStyle}>
      <h4>{comparison.label}</h4>
      <p>{independent
        ? "The latest two independent check-ins in this care episode, using each check-in's active correction. A later correction does not create a new encounter."
        : "This compares the active correction in the selected need's source chain with its immediate predecessor. The active correction may be later than the need's original source submission and may refer to an older encounter than the latest independent check-ins."}</p>
      {comparison.previous_submission_id && <p>Previous submission: {comparison.previous_submission_id}</p>}
      {comparison.current_submission_id && <p>Current submission: {comparison.current_submission_id}</p>}
      {comparison.status === "insufficient_history" && <p>{independent
        ? "Fewer than two independent check-ins are available; an encounter comparison cannot be shown yet."
        : "No complete source-and-correction pair is available for this selected need."}</p>}
      {comparison.status === "not_comparable" && <p>These submissions cannot be compared: matching check-in definitions and versions are required, and a definition may be unavailable.</p>}
      {comparison.status === "available" && (deltas.length
        ? <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", textAlign: "left" }}>
              <caption>Field differences between these submissions</caption>
              <thead><tr><th scope="col">Field</th><th scope="col">Previous value</th><th scope="col">Current value</th></tr></thead>
              <tbody>{deltas.map((delta) => (
                <tr key={delta.field_identifier}>
                  <th scope="row">{delta.field_identifier}</th>
                  <td>{comparisonValue(delta.previous_present, delta.previous_value)}</td>
                  <td>{comparisonValue(delta.current_present, delta.current_value)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        : <p>No field differences between these submissions.</p>)}
    </section>
  );
}

function comparisonValue(present: boolean, value: unknown): string {
  if (!present) return "Missing field";
  if (value === null) return "Explicit null";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function evidenceProvenance(item: NavigatorNeedWorkspaceResponse["evidence"][number]): string {
  const source = item.source_submission_id ? `Source submission: ${item.source_submission_id}` : "Source submission unavailable";
  if (item.provenance_kind === "inherited_history") return `Inherited history. ${source}`;
  if (item.provenance_kind === "stored_need_evidence") return `Stored need evidence. ${source}`;
  return source;
}

function awareIsoFromLocal(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? null : parsed.toISOString();
}

function localTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "your local timezone";
}

function isAwareInstant(value: string | null): boolean {
  return Boolean(value && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && !Number.isNaN(new Date(value).valueOf()));
}

function formatStoredInstant(value: string): string {
  if (!isAwareInstant(value)) return "Invalid date: timezone offset missing";
  return new Date(value).toLocaleString();
}

function statusLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function policyLabel(policy: NavigatorNeedWorkspaceResponse["tasks"][number]["proposals"][number]["policy"]): string {
  const count = policy.required_approval_count === 1 ? "One" : String(policy.required_approval_count);
  return `${count} ${policy.required_approver_role} approval${policy.required_approval_count === 1 ? "" : "s"} required. ${policy.allow_self_approval ? "Self-approval allowed." : "Self-approval not allowed."}`;
}

function actionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return `Navigator session needs recovery. ${error.message}`;
    if (error.status === 409) return `The selected record changed. Canonical data was refreshed. ${error.message}`;
    return error.message;
  }
  return "The action could not be saved. Your entered values are still here; please retry.";
}

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `ojcc-${Date.now()}-${Math.random()}`;
}

function looksLikePhi(value: string): boolean {
  return /\b[\w.+-]+@[\w.-]+\.\w+\b|\b(?:mrn|record)\s*#?\s*\d+\b|\b\d{3}[-.)\s]\d{3}[-.\s]\d{4}\b/i.test(value);
}

const panelStyle: CSSProperties = { background: "white", border: "1px solid #c7d9d4", borderRadius: "0.75rem", display: "grid", gap: "1rem", padding: "1.25rem" };
const cardStyle: CSSProperties = { border: "1px solid #c7d9d4", borderRadius: "0.5rem", marginBlock: "0.75rem", padding: "0.9rem" };
const buttonRow: CSSProperties = { display: "flex", flexWrap: "wrap", gap: "0.75rem", marginTop: "0.75rem" };
const eyebrow: CSSProperties = { color: "#075f5b", fontWeight: 700, margin: 0, textTransform: "uppercase" };
