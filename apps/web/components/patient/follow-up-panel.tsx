"use client";

import { useRef, useState } from "react";
import type { CSSProperties } from "react";

import { ApiError, type FollowUpResponseInput, type PatientFollowUpListResponse } from "../../lib/api-client";

type FollowUpPanelProps = {
  items: PatientFollowUpListResponse["items"];
  onRefresh: () => Promise<void>;
  onRespond: (requestId: string, payload: FollowUpResponseInput) => Promise<unknown>;
};

export function FollowUpPanel({ items, onRefresh, onRespond }: FollowUpPanelProps) {
  const [responses, setResponses] = useState<Record<string, FollowUpResponseInput["response"] | undefined>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [pendingId, setPendingId] = useState("");
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const errorRefs = useRef<Record<string, HTMLParagraphElement | null>>({});

  async function submit(requestId: string) {
    if (pendingId) return;
    const response = responses[requestId];
    const note = (notes[requestId] ?? "").trim();
    if (!response) {
      showError(requestId, "Select a follow-up response.");
      return;
    }
    if (looksLikePhi(note)) {
      showError(requestId, "Remove names, contact details, or record numbers from the note.");
      return;
    }
    setPendingId(requestId);
    setErrors((current) => ({ ...current, [requestId]: "" }));
    try {
      await onRespond(requestId, { response, note: note || undefined });
      setMessages((current) => ({ ...current, [requestId]: `Response saved: ${response.replaceAll("_", " ")}.` }));
      await onRefresh();
    } catch (caught: unknown) {
      const message = caught instanceof ApiError
        ? caught.status === 401 || caught.status === 403
          ? `Patient session needs recovery. ${caught.message}`
          : caught.message
        : "The response could not be saved. Your answer is still here; please retry.";
      showError(requestId, message);
      if (caught instanceof ApiError && caught.status === 409) await onRefresh();
    } finally {
      setPendingId("");
    }
  }

  function showError(requestId: string, message: string) {
    setErrors((current) => ({ ...current, [requestId]: message }));
    queueMicrotask(() => errorRefs.current[requestId]?.focus());
  }

  return (
    <section aria-label="Patient follow-up" style={panelStyle}>
      <h2>Navigation follow-up</h2>
      {!items.length && <p>No navigation follow-up is waiting right now.</p>}
      {items.map((item) => {
        const unavailable = item.status !== "awaiting_response";
        const savedMessage = messages[item.request_id]
          ?? (item.response ? `Response saved: ${item.response.replaceAll("_", " ")}.` : "");
        return (
          <article key={item.request_id} style={cardStyle}>
            <h3>{item.prompt}</h3>
            <p>Requested {formatDate(item.requested_at)}</p>
            {item.status === "unavailable_need_closed" && <p>This request is closed because the need was already closed.</p>}
            {savedMessage ? (
              <p aria-live="polite">{savedMessage}</p>
            ) : unavailable ? null : (
              <>
                {responseOptions.map((option) => (
                  <label key={option.value} style={optionStyle}>
                    <input
                      checked={responses[item.request_id] === option.value}
                      disabled={unavailable || Boolean(pendingId)}
                      name={`follow-up-${item.request_id}`}
                      onChange={() => setResponses((current) => ({ ...current, [item.request_id]: option.value }))}
                      type="radio"
                    />
                    {option.label}
                  </label>
                ))}
                <label htmlFor={`follow-up-note-${item.request_id}`}>Add a note (optional)</label>
                <p id={`follow-up-note-help-${item.request_id}`}>Do not include real names, contact information, or record numbers.</p>
                <textarea
                  aria-describedby={`follow-up-note-help-${item.request_id}`}
                  disabled={unavailable || Boolean(pendingId)}
                  id={`follow-up-note-${item.request_id}`}
                  maxLength={2000}
                  onChange={(event) => setNotes((current) => ({ ...current, [item.request_id]: event.target.value }))}
                  value={notes[item.request_id] ?? ""}
                />
                <button disabled={unavailable || Boolean(pendingId)} onClick={() => void submit(item.request_id)} type="button">
                  {pendingId === item.request_id ? "Saving response…" : "Send follow-up response"}
                </button>
              </>
            )}
            {errors[item.request_id] && (
              <p ref={(node) => { errorRefs.current[item.request_id] = node; }} role="alert" tabIndex={-1}>
                {errors[item.request_id]}
              </p>
            )}
          </article>
        );
      })}
    </section>
  );
}

const responseOptions: Array<{ label: string; value: FollowUpResponseInput["response"] }> = [
  { label: "Resolved", value: "resolved" },
  { label: "Unresolved", value: "unresolved" },
  { label: "I still need help", value: "still_needs_help" },
];

function looksLikePhi(value: string): boolean {
  return /\b[\w.+-]+@[\w.-]+\.\w+\b|\b(?:mrn|record)\s*#?\s*\d+\b|\b\d{3}[-.)\s]\d{3}[-.\s]\d{4}\b/i.test(value);
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

const panelStyle: CSSProperties = { background: "white", border: "1px solid #c7d9d4", borderRadius: "0.75rem", padding: "1.25rem" };
const cardStyle: CSSProperties = { display: "grid", gap: "0.6rem" };
const optionStyle: CSSProperties = { alignItems: "center", display: "flex", gap: "0.5rem" };
