import type { CSSProperties } from "react";

import type { components } from "../lib/api-types";

type NavigatorEvent = components["schemas"]["NavigatorNeedWorkspaceRead"]["timeline"][number];
type PatientEvent = components["schemas"]["PatientTimelineRead"]["events"][number];

type JourneyTimelineProps = {
  events: NavigatorEvent[] | PatientEvent[];
};

export function JourneyTimeline({ events }: JourneyTimelineProps) {
  return (
    <section aria-label="Journey history" style={panelStyle}>
      <h2>Journey history</h2>
      {!events.length ? (
        <p>No journey events are available yet.</p>
      ) : (
        <ol style={listStyle}>
          {events.map((event) => (
            <li key={event.event_id} style={itemStyle}>
              <strong>{eventTitle(event.kind)}</strong>
              <span>{event.summary}</span>
              <time dateTime={event.occurred_at}>{formatDate(event.occurred_at)}</time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function eventTitle(kind: NavigatorEvent["kind"] | PatientEvent["kind"]): string {
  const titles: Record<typeof kind, string> = {
    check_in_corrected: "Check-in corrected",
    check_in_submitted: "Check-in submitted",
    follow_up_requested: "Follow-up requested",
    follow_up_responded: "Follow-up responded",
    need_reported: "Need reported",
    outcome_recorded: "Outcome recorded",
    proposal_created: "Proposal created",
    proposal_decided: "Proposal decided",
    task_cancelled: "Task cancelled",
    task_claimed: "Task claimed",
    task_completed: "Task completed",
    task_started: "Task started",
  };
  return titles[kind];
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

const panelStyle: CSSProperties = {
  background: "white",
  border: "1px solid #c7d9d4",
  borderRadius: "0.75rem",
  padding: "1.25rem",
};
const listStyle: CSSProperties = { display: "grid", gap: "0.75rem", paddingLeft: "1.25rem" };
const itemStyle: CSSProperties = { display: "grid", gap: "0.2rem" };
