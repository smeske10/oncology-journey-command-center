"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { getNavigatorNeedCandidates, type NavigatorNeedCandidatesResponse } from "../../lib/api-client";

type RelatedNeed = NavigatorNeedCandidatesResponse["candidates"][number]["linked_needs"][number];

const unavailableReasons = {
  ambiguous_lineage: "Correction history is ambiguous. Preview unavailable.",
  unsupported_questionnaire: "This questionnaire is not supported. Preview unavailable.",
  ambiguous_answer: "The transportation answer is ambiguous. Preview unavailable.",
};

export function NeedCandidates({ enabled }: { enabled: boolean }) {
  const [data, setData] = useState<NavigatorNeedCandidatesResponse>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const controller = useRef<AbortController | undefined>(undefined);

  const refresh = useCallback(async () => {
    controller.current?.abort();
    const active = new AbortController();
    controller.current = active;
    const request = ++generation.current;
    setData(undefined);
    setError("");
    setLoading(true);
    try {
      const response = await getNavigatorNeedCandidates(active.signal);
      if (!active.signal.aborted && generation.current === request) setData(response);
    } catch {
      if (!active.signal.aborted && generation.current === request) {
        setError("Transportation reports could not be loaded. Refresh to try again.");
      }
    } finally {
      if (!active.signal.aborted && generation.current === request) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    if (enabled) queueMicrotask(() => { if (!disposed) void refresh(); });
    return () => {
      disposed = true;
      controller.current?.abort();
      generation.current += 1;
    };
  }, [enabled, refresh]);

  return (
    <section aria-label="Transportation reports to review" style={panelStyle}>
      <h2>Transportation reports to review</h2>
      <p>Synthetic evidence preview only. Viewing or refreshing creates no need, task, approval, outcome, or saved review decision.</p>
      <button type="button" disabled={!enabled} onClick={() => void refresh()} style={buttonStyle}>
        Refresh transportation reports
      </button>
      {loading && <p role="status">Loading transportation reports…</p>}
      {error && <p role="alert">{error}</p>}
      {data && !data.candidates.length && <p>No positive transportation reports in the supported check-ins.</p>}
      {data?.candidates.map((candidate) => (
        <article key={candidate.chain_root_id} aria-label={`Transportation report ${candidate.chain_root_id}`} style={cardStyle}>
          <h3>{candidate.patient_display_name}</h3>
          <p>Check-in: <time dateTime={candidate.check_in_at}>{new Date(candidate.check_in_at).toLocaleString()}</time></p>
          <p>{candidate.is_correction ? "Correction of this check-in" : "Independent check-in"}</p>
          {candidate.is_correction && <p>Corrected: <time dateTime={candidate.source_submitted_at}>{new Date(candidate.source_submitted_at).toLocaleString()}</time></p>}
          <p>{candidate.evidence.question} <q>{candidate.evidence.text}</q></p>
          <details>
            <summary>Source details</summary>
            <p>Patient: {candidate.patient_id}</p>
            <p>Episode: {candidate.care_episode_id}</p>
            <p>Original check-in: {candidate.chain_root_id}</p>
            <p>Source submission: {candidate.source_submission_id}</p>
          </details>
          <RelatedNeeds title="Needs linked to this check-in chain" needs={candidate.linked_needs} />
          <RelatedNeeds title="Other transportation needs in this episode" needs={candidate.other_transportation_needs} />
        </article>
      ))}
      {data?.unavailable.map((item, index) => (
        <p key={`${item.patient_id}:${item.care_episode_id}:${item.chain_root_id}:${index}`}>
          <strong>{item.patient_display_name}</strong>: {unavailableReasons[item.reason]}
          {item.chain_root_id && <> Check-in: {item.chain_root_id}.</>}
        </p>
      ))}
    </section>
  );
}

function RelatedNeeds({ title, needs }: { title: string; needs: RelatedNeed[] }) {
  return (
    <div>
      <h4>{title}</h4>
      {needs.length ? <ul>{needs.map((need) => (
        <li key={need.id}>
          {need.kind.replaceAll("_", " ")} — <strong>{need.effective_state.replaceAll("_", " ")}</strong>
          <details>
            <summary>Need {need.id}</summary>
            {need.source_submission_id && <p>Source submission: {need.source_submission_id}</p>}
            {need.reopened_from_need_id && <p>Recurrence of: {need.reopened_from_need_id}</p>}
          </details>
        </li>
      ))}</ul> : <p>None.</p>}
    </div>
  );
}

const panelStyle: CSSProperties = { background: "#fff", border: "1px solid #bad2ca", borderRadius: "1rem", padding: "clamp(1rem, 3vw, 1.5rem)", overflowWrap: "anywhere", minWidth: 0 };
const cardStyle: CSSProperties = { borderTop: "1px solid #d7e5df", marginTop: "1.25rem", paddingTop: "0.5rem" };
const buttonStyle: CSSProperties = { background: "#075f5b", color: "#fff", border: 0, borderRadius: "0.5rem", padding: "0.75rem 1rem", font: "inherit", cursor: "pointer" };
