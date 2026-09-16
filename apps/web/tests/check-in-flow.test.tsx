import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { CheckInFlow } from "../components/patient/check-in-flow";
import { ApiError } from "../lib/api-client";

const definition = {
  id: "a6c304e8-8070-4a65-90cc-168a4fb6d998",
  title: "Today's check-in",
  questionnaireVersion: "breast-active-v1",
  questions: [
    {
      linkId: "nausea_change",
      label: "Since your last check-in, is your nausea better, the same, or worse?",
      options: [
        { value: "better", label: "It is better" },
        { value: "same", label: "About the same" },
        { value: "worse", label: "It is worse" },
      ],
    },
  ],
};

beforeEach(() => {
  window.localStorage.clear();
});

test("requires review before final submission", () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);

  render(<CheckInFlow definition={definition} onSubmit={onSubmit} />);

  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  expect(screen.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
  expect(onSubmit).not.toHaveBeenCalled();
});

test("keeps the review available after a persistence failure", async () => {
  const onSubmit = vi.fn().mockRejectedValue(new Error("save failed"));

  render(<CheckInFlow definition={definition} onSubmit={onSubmit} />);

  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));

  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn't save/i);
  });
  expect(screen.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
});

test("shows one question at a time and progresses through every required answer", () => {
  render(
    <CheckInFlow
      definition={{
        ...definition,
        questions: [
          ...definition.questions,
          {
            linkId: "transportation",
            label: "Do you need transportation support?",
            options: [
              { value: "yes", label: "Yes" },
              { value: "no", label: "No" },
            ],
          },
        ],
      }}
      onSubmit={vi.fn().mockResolvedValue(undefined)}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));

  expect(screen.getByText("Question 2 of 2")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Do you need transportation support?" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Review your check-in" })).not.toBeInTheDocument();
});

test("returns an invalid first submission to its answer screen", async () => {
  render(
    <CheckInFlow
      definition={definition}
      onSubmit={vi.fn().mockRejectedValue(new ApiError("Please correct the highlighted answer", "correction"))}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));

  await waitFor(() => {
    expect(screen.getByRole("alert")).toHaveTextContent(/please correct/i);
  });
  expect(screen.getByRole("heading", { name: /nausea better/i })).toBeVisible();
});

test("reloads the current definition instead of entering a correction loop", async () => {
  const onConfigurationError = vi.fn().mockResolvedValue(undefined);
  render(
    <CheckInFlow
      definition={definition}
      onConfigurationError={onConfigurationError}
      onSubmit={vi.fn().mockRejectedValue(
        new ApiError("This check-in changed. Reloading it now.", "configuration"),
      )}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));

  await waitFor(() => expect(onConfigurationError).toHaveBeenCalledOnce());
  expect(screen.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
});

test("returns to the first question when a completed multi-question review needs correction", async () => {
  render(
    <CheckInFlow
      definition={{
        ...definition,
        questions: [
          ...definition.questions,
          {
            linkId: "transportation",
            label: "Do you need transportation support?",
            options: [
              { value: "yes", label: "Yes" },
              { value: "no", label: "No" },
            ],
          },
        ],
      }}
      onSubmit={vi.fn().mockRejectedValue(new ApiError("Please correct an answer", "correction"))}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "No" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));

  expect(await screen.findByRole("heading", { name: /nausea better/i })).toBeVisible();
});

test("restores an answer draft from this browser", () => {
  window.localStorage.setItem(
    "ojcc-check-in:a6c304e8-8070-4a65-90cc-168a4fb6d998",
    JSON.stringify({ answers: { nausea_change: "worse" }, freeText: "Saved context" }),
  );

  render(<CheckInFlow definition={definition} onSubmit={vi.fn().mockResolvedValue(undefined)} />);

  fireEvent.click(screen.getByRole("button", { name: "Recover saved draft" }));
  expect(screen.getByRole("button", { name: "It is worse" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("textbox", { name: /add context/i })).toHaveValue("Saved context");
});

test("submits a correction against the canonical active submission", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);

  render(
    <CheckInFlow
      definition={{
        ...definition,
        activeSubmissionId: "45c18270-f6c2-4e6a-81d6-a54535af9fd7",
      }}
      onSubmit={onSubmit}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Correct latest submission" }));
  fireEvent.click(screen.getByRole("button", { name: "It is better" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit correction" }));

  await waitFor(() => {
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        supersedes_submission_id: "45c18270-f6c2-4e6a-81d6-a54535af9fd7",
      }),
    );
  });
});

test("starts an independent check-in despite existing history", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<CheckInFlow definition={{ ...definition, activeSubmissionId: "old-root" }} onSubmit={onSubmit} />);
  expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "New check-in" }));
  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));
  await screen.findByRole("heading", { name: "Your synthetic check-in was saved" });
  expect(onSubmit.mock.calls[0][0].supersedes_submission_id).toBeUndefined();
});

test("keeps new and correction drafts separate when switching intent", () => {
  render(<CheckInFlow definition={{ ...definition, activeSubmissionId: "old-root" }} onSubmit={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "New check-in" }));
  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Correct latest submission" }));
  expect(screen.getByRole("button", { name: "It is worse" })).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(screen.getByRole("button", { name: "It is better" }));
  fireEvent.click(screen.getByRole("button", { name: "New check-in" }));
  expect(screen.getByRole("button", { name: "It is worse" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Correct latest submission" }));
  expect(screen.getByRole("button", { name: "It is better" })).toHaveAttribute("aria-pressed", "true");
});

test("does not move a draft to a different correction target or questionnaire version", () => {
  const { unmount } = render(<CheckInFlow definition={{ ...definition, activeSubmissionId: "old-root" }} onSubmit={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Correct latest submission" }));
  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  unmount();
  const next = render(<CheckInFlow definition={{ ...definition, activeSubmissionId: "next-root" }} onSubmit={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Correct latest submission" }));
  expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  next.unmount();
  render(<CheckInFlow definition={{ ...definition, questionnaireVersion: "v2", activeSubmissionId: "old-root" }} onSubmit={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Correct latest submission" }));
  expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
});

test("does not offer resubmission when clearing a saved browser draft fails", async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const onRestart = vi.fn().mockResolvedValue(undefined);
  render(<CheckInFlow definition={definition} onSubmit={onSubmit} onRestart={onRestart} />);
  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  const remove = vi.spyOn(window.localStorage, "removeItem").mockImplementation(() => { throw new Error("storage blocked"); });
  try {
    fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));
    expect(await screen.findByRole("heading", { name: "Your synthetic check-in was saved" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Submit check-in" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/draft/i);
    expect(screen.getByRole("button", { name: "Start another check-in" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start another check-in" }));
    expect(onRestart).not.toHaveBeenCalled();
    remove.mockRestore();
    fireEvent.click(screen.getByRole("button", { name: "Retry clearing saved draft" }));
    expect(screen.getByRole("button", { name: "Start another check-in" })).toBeEnabled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
  } finally { remove.mockRestore(); }
});

test("preserves an incompatible legacy draft without silently loading it", () => {
  const key = `ojcc-check-in:${definition.id}`;
  const saved = JSON.stringify({ answers: { retired_question: "yes" }, freeText: "Old draft" });
  window.localStorage.setItem(key, saved);
  render(<CheckInFlow definition={definition} onSubmit={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Recover saved draft" }));
  expect(screen.getByRole("alert")).toHaveTextContent(/does not match/i);
  expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  expect(window.localStorage.getItem(key)).toBe(saved);
});

test("blocks repeated submission and intent changes while saving", async () => {
  let finish!: () => void;
  const onSubmit = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  render(<CheckInFlow definition={{ ...definition, activeSubmissionId: "old-root" }} onSubmit={onSubmit} />);
  fireEvent.click(screen.getByRole("button", { name: "New check-in" }));
  fireEvent.click(screen.getByRole("button", { name: "It is worse" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));
  const saving = screen.getByRole("button", { name: "Saving..." });
  expect(saving).toBeDisabled();
  fireEvent.click(saving);
  expect(screen.getByRole("button", { name: "Edit answers" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Correct latest submission" })).toBeDisabled();
  finish();
  await screen.findByRole("heading", { name: "Your synthetic check-in was saved" });
  expect(onSubmit).toHaveBeenCalledTimes(1);
});
