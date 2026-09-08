"use client";

import { useCallback, useEffect, useState } from "react";

import {
  CheckInFlow,
  type CheckInQuestion,
  type PatientCheckInDefinition,
} from "../../../components/patient/check-in-flow";
import {
  ApiError,
  bootstrapPatientCheckIn,
  submitCheckIn,
  type CheckInDefinitionResponse,
} from "../../../lib/api-client";

export default function PatientDemoPage() {
  const [definition, setDefinition] = useState<PatientCheckInDefinition>();
  const [error, setError] = useState("");

  const loadCurrentDefinition = useCallback(
    () =>
      bootstrapPatientCheckIn()
        .then((response) => {
          setError("");
          setDefinition(toPresentationDefinition(response));
        })
        .catch((requestError: unknown) => {
          const message = requestError instanceof ApiError ? requestError.message : "Demo unavailable";
          setError(message);
        }),
    [],
  );

  useEffect(() => {
    void loadCurrentDefinition();
  }, [loadCurrentDefinition]);

  if (error) return <main><p role="alert">{error}</p></main>;
  if (!definition) return <main><p aria-live="polite">Loading synthetic check-in…</p></main>;
  return (
    <CheckInFlow
      definition={definition}
      key={`${definition.id}:${definition.questionnaireVersion}:${definition.activeSubmissionId ?? "first"}`}
      onConfigurationError={loadCurrentDefinition}
      onSubmit={(payload) => submitCheckIn(definition.id, payload)}
    />
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
