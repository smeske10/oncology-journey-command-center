"use client";

import type { CSSProperties } from "react";

export type Priority = {
  level: "high" | "medium" | "routine";
  reasons: string[];
  score: number;
};

export type NavigatorQueueItem = {
  created_at: string;
  due_at: string | null;
  evidence: Array<{ field: string; text: string }>;
  kind: string;
  need_id: string;
  owner_id: string | null;
  patient_display_name: string;
  patient_id: string;
  priority: Priority;
};

type WorkQueueProps = {
  error?: string;
  items: NavigatorQueueItem[];
  onSelect: (item: NavigatorQueueItem) => void;
  selectedNeedId?: string;
  state?: "loading";
};

const reasonLabels: Record<string, string> = {
  due_soon: "Due soon",
  medication_uncertainty: "Medication uncertainty",
  unresolved_over_24_hours: "Unresolved for more than 24 hours",
  unresolved_over_48_hours: "Unresolved for more than 48 hours",
  worsening_report: "Worsening report",
};

export function WorkQueue({ error, items, onSelect, selectedNeedId, state }: WorkQueueProps) {
  if (state === "loading") {
    return <section aria-label="Navigator work queue"><p aria-live="polite">Loading navigator queue…</p></section>;
  }
  if (error) {
    return <section aria-label="Navigator work queue"><p role="alert">{error}</p></section>;
  }
  if (!items.length) {
    return <section aria-label="Navigator work queue"><h2>Work queue</h2><p>No open navigation needs right now.</p></section>;
  }

  const selected = items.find((item) => item.need_id === selectedNeedId) ?? items[0];
  return (
    <section aria-label="Navigator work queue" style={queueLayout}>
      <div>
        <h2>Work queue</h2>
        <p>Ordered using configured operational rules. This is not a clinical-risk score.</p>
        <div aria-label="Queue items" style={itemList}>
          {items.map((item) => {
            const isSelected = item.need_id === selected.need_id;
            return (
              <button
                aria-pressed={isSelected}
                key={item.need_id}
                onClick={() => onSelect(item)}
                style={{ ...queueButton, borderColor: isSelected ? "#0b6e69" : "#b9ccc8" }}
                type="button"
              >
                <strong>{item.patient_display_name}</strong>
                <span>{humanizeKind(item.kind)}</span>
                <span>{item.priority.level} operational priority</span>
                {item.due_at && <span>Due {formatDate(item.due_at)}</span>}
              </button>
            );
          })}
        </div>
      </div>
      <aside aria-live="polite" aria-label="Selected queue item" style={selectedPanel}>
        <p style={eyebrow}>{selected.priority.level} operational priority</p>
        <h3>{selected.patient_display_name}</h3>
        <p>{humanizeKind(selected.kind)}</p>
        <h4>Why this item is ordered here</h4>
        <ul>
          {selected.priority.reasons.map((reason) => <li key={reason}>{reasonLabels[reason] ?? reason}</li>)}
        </ul>
        <h4>Exact patient-reported evidence</h4>
        <ul>
          {selected.evidence.map((evidence, index) => (
            <li key={`${evidence.field}-${index}`}><strong>{humanizeKind(evidence.field)}:</strong> {evidence.text}</li>
          ))}
        </ul>
      </aside>
    </section>
  );
}

function humanizeKind(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

const queueLayout: CSSProperties = {
  display: "grid",
  gap: "1rem",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 18rem), 1fr))",
};
const itemList: CSSProperties = { display: "grid", gap: "0.75rem" };
const queueButton: CSSProperties = { alignItems: "start", background: "white", border: "2px solid", borderRadius: "0.75rem", color: "#12302d", cursor: "pointer", display: "grid", font: "inherit", gap: "0.3rem", padding: "1rem", textAlign: "left", width: "100%" };
const selectedPanel: CSSProperties = { background: "#eef7f3", borderRadius: "0.75rem", padding: "1rem" };
const eyebrow: CSSProperties = { color: "#075f5b", fontWeight: 700, margin: 0, textTransform: "uppercase" };
