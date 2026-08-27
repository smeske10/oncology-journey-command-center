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

test("returns to a correction screen for validation errors", async () => {
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

test("restores an answer draft from this browser", () => {
  window.localStorage.setItem(
    "ojcc-check-in:a6c304e8-8070-4a65-90cc-168a4fb6d998",
    JSON.stringify({ answers: { nausea_change: "worse" }, freeText: "Saved context" }),
  );

  render(<CheckInFlow definition={definition} onSubmit={vi.fn().mockResolvedValue(undefined)} />);

  expect(screen.getByRole("button", { name: "It is worse" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("textbox", { name: /add context/i })).toHaveValue("Saved context");
});
