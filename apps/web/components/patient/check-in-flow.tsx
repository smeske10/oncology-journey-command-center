"use client";

import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";

import type { components } from "../../lib/api-types";
import { DemoWarning } from "./demo-warning";

export type CheckInQuestion = {
  linkId: string;
  label: string;
  options: Array<{ value: string; label: string }>;
};

export type PatientCheckInDefinition = {
  id: string;
  title: string;
  questionnaireVersion: string;
  questions: CheckInQuestion[];
};

export type CheckInSubmissionInput = components["schemas"]["CheckInSubmissionCreate"];

type FlowStage = "question" | "review" | "submitting" | "success";

type CheckInFlowProps = {
  definition: PatientCheckInDefinition;
  onSubmit: (submission: CheckInSubmissionInput) => Promise<unknown>;
};

export function CheckInFlow({ definition, onSubmit }: CheckInFlowProps) {
  const [stage, setStage] = useState<FlowStage>("question");
  const draftKey = `ojcc-check-in:${definition.id}`;
  const [draft, setDraft] = useState(() => readDraft(draftKey));
  const [error, setError] = useState("");
  const { answers, freeText } = draft;
  const question = definition.questions[0];
  const selectedAnswer = answers[question?.linkId];

  useEffect(() => {
    window.localStorage.setItem(draftKey, JSON.stringify(draft));
  }, [draft, draftKey]);

  const submission = useMemo<CheckInSubmissionInput>(
    () => ({
      questionnaire_version: definition.questionnaireVersion,
      answers: definition.questions.flatMap((item) => {
        const value = answers[item.linkId];
        return value ? [{ link_id: item.linkId, value }] : [];
      }),
      free_text: freeText.trim() || undefined,
    }),
    [answers, definition, freeText],
  );

  if (!question) {
    return <p role="alert">This synthetic check-in is not available right now.</p>;
  }

  async function submit() {
    setError("");
    setStage("submitting");
    try {
      await onSubmit(submission);
      window.localStorage.removeItem(draftKey);
      setStage("success");
    } catch {
      setError("We couldn’t save your check-in. Your review is still here—please try again.");
      setStage("review");
    }
  }

  return (
    <main
      style={{
        background: "#f5f7f5",
        color: "#102a2a",
        fontFamily: "Arial, sans-serif",
        margin: "0 auto",
        maxWidth: "42rem",
        minHeight: "100vh",
        padding: "1.25rem",
      }}
    >
      <header>
        <p style={{ color: "#386d66", fontWeight: 700, letterSpacing: "0.04em", margin: 0 }}>
          ONCOLOGY JOURNEY
        </p>
        <h1 style={{ fontSize: "clamp(1.75rem, 7vw, 2.5rem)", marginBottom: "0.25rem" }}>
          {definition.title}
        </h1>
        <p aria-live="polite" style={{ marginTop: 0 }}>
          {stage === "question" ? "Question 1 of 1" : "Your progress is saved in this browser."}
        </p>
        <div aria-label="Check-in progress" style={{ background: "#d5e3df", borderRadius: 999, height: 8 }}>
          <div
            style={{
              background: "#1f6f62",
              borderRadius: 999,
              height: "100%",
              width: stage === "question" ? "50%" : "100%",
            }}
          />
        </div>
      </header>

      <DemoWarning />

      <aside
        role="note"
        style={{ borderLeft: "4px solid #9b1c1c", marginBlock: "1rem", paddingLeft: "0.875rem" }}
      >
        <strong>Need urgent help?</strong> For urgent or emergency symptoms, call 911 or your local
        emergency service. This demo does not provide medical advice.
      </aside>

      {stage === "question" && (
        <section aria-labelledby="question-heading">
          <h2 id="question-heading">{question.label}</h2>
          <div aria-label="Answer choices" style={{ display: "grid", gap: "0.75rem" }}>
            {question.options.map((option) => (
              <button
                aria-pressed={selectedAnswer === option.value}
                key={option.value}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    answers: { ...current.answers, [question.linkId]: option.value },
                  }))
                }
                style={choiceStyle(selectedAnswer === option.value)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
          <label htmlFor="check-in-context" style={{ display: "block", fontWeight: 700, marginTop: "1.5rem" }}>
            Add context (optional)
          </label>
          <p id="check-in-context-help">Do not include real names, contact information, or record numbers.</p>
          <textarea
            aria-describedby="check-in-context-help"
            id="check-in-context"
            maxLength={2000}
            onChange={(event) =>
              setDraft((current) => ({ ...current, freeText: event.target.value }))
            }
            rows={4}
            style={{ boxSizing: "border-box", font: "inherit", padding: "0.75rem", width: "100%" }}
            value={freeText}
          />
          <button
            disabled={!selectedAnswer}
            onClick={() => setStage("review")}
            style={primaryButtonStyle}
            type="button"
          >
            Continue
          </button>
        </section>
      )}

      {(stage === "review" || stage === "submitting") && (
        <section aria-labelledby="review-heading">
          <h2 id="review-heading">Review your check-in</h2>
          <p>
            <strong>{question.label}</strong>
            <br />
            {question.options.find((option) => option.value === selectedAnswer)?.label}
          </p>
          {freeText && <p><strong>Your context:</strong> {freeText}</p>}
          {error && <p role="alert">{error}</p>}
          <button onClick={() => setStage("question")} style={secondaryButtonStyle} type="button">
            Edit answer
          </button>
          <button disabled={stage === "submitting"} onClick={submit} style={primaryButtonStyle} type="button">
            {stage === "submitting" ? "Saving…" : "Submit check-in"}
          </button>
        </section>
      )}

      {stage === "success" && (
        <section aria-labelledby="success-heading">
          <h2 id="success-heading">Your synthetic check-in was saved</h2>
          <p>Thank you. In this demo, any next step is reviewed by a human navigator.</p>
        </section>
      )}
    </main>
  );
}

function readDraft(draftKey: string): { answers: Record<string, string>; freeText: string } {
  if (typeof window === "undefined") return { answers: {}, freeText: "" };
  const savedDraft = window.localStorage.getItem(draftKey);
  if (!savedDraft) return { answers: {}, freeText: "" };
  try {
    const draft = JSON.parse(savedDraft) as { answers?: Record<string, string>; freeText?: string };
    return { answers: draft.answers ?? {}, freeText: draft.freeText ?? "" };
  } catch {
    window.localStorage.removeItem(draftKey);
    return { answers: {}, freeText: "" };
  }
}

const primaryButtonStyle: CSSProperties = {
  background: "#1f6f62",
  border: 0,
  borderRadius: "0.5rem",
  color: "white",
  cursor: "pointer",
  font: "inherit",
  fontWeight: 700,
  marginTop: "1.25rem",
  minHeight: "3rem",
  padding: "0.75rem 1rem",
  width: "100%",
};

const secondaryButtonStyle: CSSProperties = {
  background: "white",
  border: "1px solid #1f6f62",
  borderRadius: "0.5rem",
  color: "#14544a",
  cursor: "pointer",
  font: "inherit",
  fontWeight: 700,
  marginTop: "1.25rem",
  minHeight: "3rem",
  padding: "0.75rem 1rem",
  width: "100%",
};

function choiceStyle(isSelected: boolean): CSSProperties {
  return {
    background: isSelected ? "#d8f0ea" : "white",
    border: `2px solid ${isSelected ? "#1f6f62" : "#9ab8b1"}`,
    borderRadius: "0.75rem",
    color: "#102a2a",
    cursor: "pointer",
    font: "inherit",
    minHeight: "3.5rem",
    padding: "0.875rem 1rem",
    textAlign: "left",
  };
}
