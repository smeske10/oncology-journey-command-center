"use client";

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";

import { JourneyTimeline } from "../../../components/journey-timeline";
import {
  CheckInFlow,
  type CheckInQuestion,
  type PatientCheckInDefinition,
} from "../../../components/patient/check-in-flow";
import { FollowUpPanel } from "../../../components/patient/follow-up-panel";
import {
  ApiError,
  bootstrapPatientCheckIn,
  getPatientFollowUps,
  getPatientTimeline,
  respondToFollowUp,
  submitCheckIn,
  type CheckInDefinitionResponse,
  type PatientFollowUpListResponse,
  type PatientTimelineResponse,
} from "../../../lib/api-client";

export default function PatientDemoPage() {
  const [definition, setDefinition] = useState<PatientCheckInDefinition>();
  const [followUps, setFollowUps] = useState<PatientFollowUpListResponse["items"]>([]);
  const [timeline, setTimeline] = useState<PatientTimelineResponse["events"]>([]);
  const [error, setError] = useState("");
  const [supportError, setSupportError] = useState("");
  const [checkInSession, setCheckInSession] = useState(0);

  const loadFollowUpsAndTimeline = useCallback(async () => {
    try {
      const [followUpResponse, timelineResponse] = await Promise.all([
        getPatientFollowUps(),
        getPatientTimeline(),
      ]);
      setFollowUps(followUpResponse.items);
      setTimeline(timelineResponse.events);
      setSupportError("");
    } catch (requestError: unknown) {
      const message = requestError instanceof ApiError
        ? requestError.message
        : "Navigation follow-up and history are temporarily unavailable.";
      setSupportError(message);
    }
  }, []);

  const loadCurrentDefinition = useCallback(async () => {
    try {
      const response = await bootstrapPatientCheckIn();
      setDefinition(toPresentationDefinition(response));
      setError("");
      await loadFollowUpsAndTimeline();
    } catch (requestError: unknown) {
      const message = requestError instanceof ApiError ? requestError.message : "Demo unavailable";
      setError(message);
    }
  }, [loadFollowUpsAndTimeline]);

  useEffect(() => {
    queueMicrotask(() => void loadCurrentDefinition());
  }, [loadCurrentDefinition]);

  if (error) return <main><p role="alert">{error}</p><button onClick={() => void loadCurrentDefinition()} type="button">Restore patient session</button></main>;
  if (!definition) return <main><p aria-live="polite">Loading synthetic patient experience…</p></main>;
  return (
    <main style={mainStyle}>
      <CheckInFlow
        definition={definition}
        key={`${definition.id}:${definition.questionnaireVersion}:${definition.activeSubmissionId ?? "first"}:${checkInSession}`}
        onConfigurationError={loadCurrentDefinition}
        onRestart={async () => {
          const response = await bootstrapPatientCheckIn();
          setDefinition(toPresentationDefinition(response));
          setCheckInSession((current) => current + 1);
        }}
        onSubmit={async (payload) => {
          const response = await submitCheckIn(definition.id, payload);
          void loadFollowUpsAndTimeline();
          return response;
        }}
      />
      <FollowUpPanel
        items={followUps}
        onRefresh={loadFollowUpsAndTimeline}
        onRespond={respondToFollowUp}
      />
      {supportError && <div><p role="alert">{supportError}</p><button onClick={() => void loadFollowUpsAndTimeline()} type="button">Retry journey history</button></div>}
      <JourneyTimeline events={timeline} />
    </main>
  );
}

function toPresentationDefinition(response: CheckInDefinitionResponse): PatientCheckInDefinition {
  return {
    activeSubmissionId: response.active_submission_id,
    id: response.id,
    title: response.title,
    questionnaireVersion: response.questionnaire_version,
    questions: response.questions
      .map(toQuestion)
      .filter((question): question is CheckInQuestion => question !== null),
  };
}

const mainStyle: CSSProperties = { background: "#f4f8f6", color: "#12302d", display: "grid", fontFamily: "Arial, sans-serif", gap: "1.5rem", margin: "0 auto", maxWidth: "56rem", minHeight: "100vh", padding: "clamp(1rem, 4vw, 2.5rem)" };

function toQuestion(question: Record<string, unknown>): CheckInQuestion | null {
  const linkId = question.link_id;
  const label = question.label;
  const options = question.options;
  if (typeof linkId !== "string" || typeof label !== "string" || !Array.isArray(options)) return null;
  const choices = options.flatMap((option) => {
    if (!option || typeof option !== "object") return [];
    const value = (option as Record<string, unknown>).value;
    const choiceLabel = (option as Record<string, unknown>).label;
    return typeof value === "string" && typeof choiceLabel === "string"
      ? [{ value, label: choiceLabel }]
      : [];
  });
  return choices.length ? { linkId, label, options: choices } : null;
}
