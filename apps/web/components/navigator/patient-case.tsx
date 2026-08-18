import type { CSSProperties, ReactNode } from "react";

import type { NavigatorPatientCaseResponse } from "../../lib/api-client";
import type { NavigatorQueueItem } from "./work-queue";

type PatientCaseProps = {
  caseData?: NavigatorPatientCaseResponse;
  error?: string;
  openNeeds: NavigatorQueueItem[];
  state?: "loading";
};

export function PatientCase({ caseData, error, openNeeds, state }: PatientCaseProps) {
  if (state === "loading") return <section aria-label="Patient case"><p aria-live="polite">Loading patient case…</p></section>;
  if (error) return <section aria-label="Patient case"><p role="alert">{error}</p></section>;
  if (!caseData) return <section aria-label="Patient case"><p>Select a queue item to review the patient case.</p></section>;

  return (
    <section aria-label="Patient case" style={caseStyle}>
      <header>
        <p style={eyebrow}>Selected patient</p>
        <h2>{recordText(caseData.patient, "display_name", "Selected patient")}</h2>
        <p>{recordText(caseData.patient, "diagnosis", "Synthetic pathway details unavailable")}</p>
        <p>Consent: {recordText(caseData.patient, "consent_status", "Not recorded")}</p>
        <p>Upcoming synthetic appointment: {recordText(caseData.upcoming_synthetic_appointment, "label", "None recorded")}</p>
      </header>
      <CaseSection title="Longitudinal submissions">
        {caseData.longitudinal_submissions.map((submission) => <div key={submission.id}><p>{submission.free_text ?? "No free-text context."}</p></div>)}
      </CaseSection>
      <CaseSection title="Open navigation needs">
        {openNeeds.map((need) => <p key={need.need_id}>{need.kind}: {need.priority.reasons.map((reason) => reason.replaceAll("_", " ")).join(", ") || "No additional ordering rules matched"}</p>)}
      </CaseSection>
      <CaseSection title="Safety signals">
        {caseData.safety_signals.map((signal) => <p key={signal.id}>{signal.rule_code} — {signal.status}</p>)}
      </CaseSection>
      <CaseSection title="Navigation tasks">
        {caseData.navigation_tasks.map((task) => <p key={task.id}>{task.title} — {task.status}</p>)}
      </CaseSection>
    </section>
  );
}

function CaseSection({ children, title }: { children: ReactNode; title: string }) {
  return <section><h3>{title}</h3>{children}</section>;
}

function recordText(value: Record<string, unknown> | null | undefined, key: string, fallback: string): string {
  const text = value?.[key];
  return typeof text === "string" ? text : fallback;
}

const caseStyle: CSSProperties = { background: "white", border: "1px solid #c7d9d4", borderRadius: "0.75rem", padding: "1.25rem" };
const eyebrow: CSSProperties = { color: "#075f5b", fontWeight: 700, margin: 0, textTransform: "uppercase" };
