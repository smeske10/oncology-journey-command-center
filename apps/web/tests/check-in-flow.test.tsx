import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { CheckInFlow } from "../components/patient/check-in-flow";

const definition = {
  id: "a6c304e8-8070-4a65-90cc-168a4fb6d998",
  title: "Today’s check-in",
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
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn’t save/i);
  });
  expect(screen.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
});
