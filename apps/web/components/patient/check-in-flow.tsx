"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { ApiError } from "../../lib/api-client";
import type { components } from "../../lib/api-types";
import { DemoWarning } from "./demo-warning";

export type CheckInQuestion = {
  linkId: string;
  label: string;
  options: Array<{ value: string; label: string }>;
};

export type PatientCheckInDefinition = {
  activeSubmissionId?: string | null;
  id: string;
  title: string;
  questionnaireVersion: string;
  questions: CheckInQuestion[];
};

export type CheckInSubmissionInput = components["schemas"]["CheckInSubmissionCreate"];

type FlowStage = "question" | "review" | "submitting" | "success";
type Draft = { answers: Record<string, string>; freeText: string };
type Intent = "new" | "correction";

type CheckInFlowProps = {
  definition: PatientCheckInDefinition;
  onConfigurationError?: () => Promise<void>;
  onRestart?: () => Promise<void>;
  onSubmit: (submission: CheckInSubmissionInput) => Promise<unknown>;
};

export function CheckInFlow({ definition, onConfigurationError, onRestart, onSubmit }: CheckInFlowProps) {
  const [stage, setStage] = useState<FlowStage>("question");
  const [intent, setIntent] = useState<Intent | null>(definition.activeSubmissionId ? null : "new");
  const draftKey = scopedDraftKey(definition, intent ?? "new");
  const legacyDraftKey = `ojcc-check-in:${definition.id}`;
  const [legacyDraft, setLegacyDraft] = useState(() => readDraft(legacyDraftKey));
  const [draft, setDraft] = useState<Draft>(() => definition.activeSubmissionId ? emptyDraft() : readDraft(draftKey));
  const [questionIndex, setQuestionIndex] = useState(() =>
    definition.activeSubmissionId ? 0 : firstUnansweredIndex(definition.questions, readDraft(draftKey).answers),
  );
  const [error, setError] = useState("");
  const [restarting, setRestarting] = useState(false);
  const [savedDraftPending, setSavedDraftPending] = useState(false);
  const saving = useRef(false);
  const { answers, freeText } = draft;
  const question = definition.questions[questionIndex];
  const selectedAnswer = answers[question?.linkId];

  useEffect(() => {
    if (intent && stage !== "success") window.localStorage.setItem(draftKey, JSON.stringify(draft));
  }, [draft, draftKey, intent, stage]);

  const submission = useMemo<CheckInSubmissionInput>(
    () => ({
      questionnaire_version: definition.questionnaireVersion,
      answers: definition.questions.flatMap((item) => {
        const value = answers[item.linkId];
        return value ? [{ link_id: item.linkId, value }] : [];
      }),
      free_text: freeText.trim() || undefined,
      ...(intent === "correction" ? { supersedes_submission_id: definition.activeSubmissionId ?? undefined } : {}),
    }),
    [answers, definition, freeText, intent],
  );

  if (!question) return <p role="alert">This synthetic check-in is not available right now.</p>;

  async function submit() {
    if (saving.current || !intent) return;
    saving.current = true;
    setError("");
    setStage("submitting");
    try {
      await onSubmit(submission);
      setStage("success");
      clearSavedDraft();
    } catch (submissionError: unknown) {
      if (submissionError instanceof ApiError && submissionError.kind === "correction") {
        setError(submissionError.message);
        setQuestionIndex(0);
        setStage("question");
      } else if (submissionError instanceof ApiError && submissionError.kind === "configuration") {
        setError(submissionError.message);
        setStage("review");
        await onConfigurationError?.();
      } else {
        setError("We couldn't save your check-in. Your review is still here—please try again.");
        setStage("review");
      }
    } finally {
      saving.current = false;
    }
  }

  function clearSavedDraft() {
    try {
      window.localStorage.removeItem(draftKey);
      setSavedDraftPending(false);
      setError("");
    } catch {
      setSavedDraftPending(true);
      setError("Your check-in was saved, but its browser draft could not be cleared. Clear the draft before starting another check-in.");
    }
  }

  function chooseIntent(nextIntent: Intent) {
    if (saving.current) return;
    const nextDraft = readDraft(scopedDraftKey(definition, nextIntent));
    setIntent(nextIntent);
    setDraft(nextDraft);
    setQuestionIndex(firstUnansweredIndex(definition.questions, nextDraft.answers));
    setStage("question");
    setError("");
  }

  function recoverLegacyDraft() {
    const compatible = Object.entries(legacyDraft.answers).every(([linkId, value]) =>
      definition.questions.some((item) => item.linkId === linkId && item.options.some((option) => option.value === value)),
    );
    if (!compatible) {
      setError("This saved draft does not match the current questions. It remains saved in this browser.");
      return;
    }
    window.localStorage.setItem(draftKey, JSON.stringify(legacyDraft));
    setDraft(legacyDraft);
    setQuestionIndex(firstUnansweredIndex(definition.questions, legacyDraft.answers));
    window.localStorage.removeItem(legacyDraftKey);
    setLegacyDraft(emptyDraft());
  }

  async function restart() {
    if (restarting || savedDraftPending) return;
    setRestarting(true);
    setError("");
    try {
      await onRestart?.();
    } catch {
      setError("Your check-in was saved. We couldn't load the next check-in. Please try again.");
    } finally {
      setRestarting(false);
    }
  }

  function continueToNextStep() {
    setError("");
    if (questionIndex === definition.questions.length - 1) {
      setStage("review");
    } else {
      setQuestionIndex((current) => current + 1);
    }
  }

  return (
    <section aria-label="Patient check-in" style={mainStyle}>
      <header>
        <p style={eyebrowStyle}>ONCOLOGY JOURNEY</p>
        <h1 style={titleStyle}>{definition.title}</h1>
        <p aria-live="polite" style={{ marginTop: 0 }}>
          {!intent ? "Choose a new check-in or a correction."
            : stage === "question"
            ? `Question ${questionIndex + 1} of ${definition.questions.length}`
            : "Your progress is saved in this browser."}
        </p>
        <div aria-label="Check-in progress" style={progressTrackStyle}>
          <div
            style={{
              ...progressFillStyle,
              width: `${stage === "question" ? ((questionIndex + 1) / definition.questions.length) * 100 : 100}%`,
            }}
          />
        </div>
      </header>

      <DemoWarning />
      <aside role="note" style={urgentCareStyle}>
        <strong>Need urgent help?</strong> For urgent or emergency symptoms, call 911 or your local
        emergency service. This demo does not provide medical advice.
      </aside>

      {definition.activeSubmissionId && stage !== "success" && (
        <section aria-label="Check-in intent">
          <p>A new check-in records another point in your journey. A correction updates an earlier submission and preserves its history.</p>
          <button aria-pressed={intent === "new"} disabled={stage === "submitting"} onClick={() => chooseIntent("new")} style={secondaryButtonStyle} type="button">New check-in</button>
          <button aria-pressed={intent === "correction"} disabled={stage === "submitting"} onClick={() => chooseIntent("correction")} style={secondaryButtonStyle} type="button">Correct latest submission</button>
        </section>
      )}

      {intent && stage === "question" && (Object.keys(legacyDraft.answers).length > 0 || legacyDraft.freeText) && !Object.keys(answers).length && !freeText && (
        <aside>
          <p>An older saved draft is available. Recover it into this {intent === "new" ? "new check-in" : "correction"} and review its answers before submitting.</p>
          <button onClick={recoverLegacyDraft} style={secondaryButtonStyle} type="button">Recover saved draft</button>
        </aside>
      )}

      {intent && stage === "question" && (
        <section aria-labelledby="question-heading">
          {error && <p role="alert">{error}</p>}
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
          {questionIndex === definition.questions.length - 1 && (
            <>
              <label htmlFor="check-in-context" style={contextLabelStyle}>Add context (optional)</label>
              <p id="check-in-context-help">Do not include real names, contact information, or record numbers.</p>
              <textarea
                aria-describedby="check-in-context-help"
                id="check-in-context"
                maxLength={2000}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, freeText: event.target.value }))
                }
                rows={4}
                style={textareaStyle}
                value={freeText}
              />
            </>
          )}
          <button disabled={!selectedAnswer} onClick={continueToNextStep} style={primaryButtonStyle} type="button">
            Continue
          </button>
        </section>
      )}

      {(stage === "review" || stage === "submitting") && (
        <section aria-labelledby="review-heading">
          <h2 id="review-heading">Review your check-in</h2>
          {definition.questions.map((reviewQuestion) => (
            <p key={reviewQuestion.linkId}>
              <strong>{reviewQuestion.label}</strong>
              <br />
              {reviewQuestion.options.find((option) => option.value === answers[reviewQuestion.linkId])?.label}
            </p>
          ))}
          {freeText && <p><strong>Your context:</strong> {freeText}</p>}
          {error && <p role="alert">{error}</p>}
          <button disabled={stage === "submitting"} onClick={() => { setQuestionIndex(0); setStage("question"); }} style={secondaryButtonStyle} type="button">
            Edit answers
          </button>
          <button disabled={stage === "submitting"} onClick={submit} style={primaryButtonStyle} type="button">
            {stage === "submitting"
              ? "Saving..."
              : intent === "correction"
                ? "Submit correction"
                : "Submit check-in"}
          </button>
        </section>
      )}

      {stage === "success" && (
        <section aria-labelledby="success-heading">
          <h2 id="success-heading">
            {intent === "correction"
              ? "Your synthetic correction was saved"
              : "Your synthetic check-in was saved"}
          </h2>
          <p>Thank you. In this demo, any next step is reviewed by a human navigator.</p>
          {error && <p role="alert">{error}</p>}
          {savedDraftPending && <button onClick={clearSavedDraft} style={secondaryButtonStyle} type="button">Retry clearing saved draft</button>}
          {onRestart && <button disabled={restarting || savedDraftPending} onClick={() => void restart()} style={primaryButtonStyle} type="button">{restarting ? "Loading..." : "Start another check-in"}</button>}
        </section>
      )}
    </section>
  );
}

function emptyDraft(): Draft { return { answers: {}, freeText: "" }; }

function scopedDraftKey(definition: PatientCheckInDefinition, intent: Intent): string {
  return `ojcc-check-in:${definition.id}:${encodeURIComponent(definition.questionnaireVersion)}:${intent}:${intent === "correction" ? definition.activeSubmissionId : "new"}`;
}

function readDraft(draftKey: string): Draft {
  if (typeof window === "undefined") return { answers: {}, freeText: "" };
  const savedDraft = window.localStorage.getItem(draftKey);
  if (!savedDraft) return { answers: {}, freeText: "" };
  try {
    const draft = JSON.parse(savedDraft) as Partial<Draft>;
    if (!draft || typeof draft !== "object" || typeof draft.freeText !== "string" || !draft.answers || typeof draft.answers !== "object" || Array.isArray(draft.answers) || Object.values(draft.answers).some((value) => typeof value !== "string")) return emptyDraft();
    return { answers: draft.answers, freeText: draft.freeText };
  } catch {
    return { answers: {}, freeText: "" };
  }
}

function firstUnansweredIndex(questions: CheckInQuestion[], answers: Record<string, string>): number {
  const index = questions.findIndex((item) => !answers[item.linkId]);
  return index === -1 ? Math.max(questions.length - 1, 0) : index;
}

const mainStyle: CSSProperties = { background: "#f5f7f5", color: "#102a2a", fontFamily: "Arial, sans-serif", margin: "0 auto", maxWidth: "42rem", minHeight: "100vh", padding: "1.25rem" };
const eyebrowStyle: CSSProperties = { color: "#386d66", fontWeight: 700, letterSpacing: "0.04em", margin: 0 };
const titleStyle: CSSProperties = { fontSize: "clamp(1.75rem, 7vw, 2.5rem)", marginBottom: "0.25rem" };
const progressTrackStyle: CSSProperties = { background: "#d5e3df", borderRadius: 999, height: 8 };
const progressFillStyle: CSSProperties = { background: "#1f6f62", borderRadius: 999, height: "100%" };
const urgentCareStyle: CSSProperties = { borderLeft: "4px solid #9b1c1c", marginBlock: "1rem", paddingLeft: "0.875rem" };
const contextLabelStyle: CSSProperties = { display: "block", fontWeight: 700, marginTop: "1.5rem" };
const textareaStyle: CSSProperties = { boxSizing: "border-box", font: "inherit", padding: "0.75rem", width: "100%" };
const primaryButtonStyle: CSSProperties = { background: "#1f6f62", border: 0, borderRadius: "0.5rem", color: "white", cursor: "pointer", font: "inherit", fontWeight: 700, marginTop: "1.25rem", minHeight: "3rem", padding: "0.75rem 1rem", width: "100%" };
const secondaryButtonStyle: CSSProperties = { background: "white", border: "1px solid #1f6f62", borderRadius: "0.5rem", color: "#14544a", cursor: "pointer", font: "inherit", fontWeight: 700, marginTop: "1.25rem", minHeight: "3rem", padding: "0.75rem 1rem", width: "100%" };

function choiceStyle(isSelected: boolean): CSSProperties {
  return { background: isSelected ? "#d8f0ea" : "white", border: `2px solid ${isSelected ? "#1f6f62" : "#9ab8b1"}`, borderRadius: "0.75rem", color: "#102a2a", cursor: "pointer", font: "inherit", minHeight: "3.5rem", padding: "0.875rem 1rem", textAlign: "left" };
}
