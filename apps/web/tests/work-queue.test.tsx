import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { WorkQueue, type NavigatorQueueItem } from "../components/navigator/work-queue";

const items: NavigatorQueueItem[] = [
  {
    created_at: "2026-08-18T10:00:00Z",
    due_at: "2026-08-18T14:00:00Z",
    evidence: [
      { field: "nausea_change", text: "worse" },
      { field: "free_text", text: "Nausea now interferes with meals." },
    ],
    kind: "symptom_change",
    need_id: "need-1",
    owner_id: "navigator-1",
    patient_display_name: "Maya Chen",
    patient_id: "patient-1",
    priority: {
      level: "high",
      reasons: ["worsening_report", "medication_uncertainty", "due_soon"],
      score: 115,
    },
  },
];

test("shows exact evidence and plain-language reasons for the selected queue item", () => {
  render(<WorkQueue items={items} onSelect={() => undefined} selectedNeedId={undefined} />);

  fireEvent.click(screen.getByRole("button", { name: /maya chen/i }));

  expect(screen.getByText("Worsening report")).toBeVisible();
  expect(screen.getByText("Medication uncertainty")).toBeVisible();
  expect(screen.getByText("Due soon")).toBeVisible();
  expect(screen.getByText(/interferes with meals/i)).toBeVisible();
  expect(screen.getByText(/not a clinical-risk score/i)).toBeVisible();
  expect(screen.queryByText("115")).not.toBeInTheDocument();
});

test("provides loading, API-error, and empty queue states", () => {
  const { rerender } = render(
    <WorkQueue items={[]} onSelect={() => undefined} selectedNeedId={undefined} state="loading" />,
  );
  expect(screen.getByText(/loading navigator queue/i)).toBeVisible();

  rerender(
    <WorkQueue
      error="The queue could not be loaded."
      items={[]}
      onSelect={() => undefined}
      selectedNeedId={undefined}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/i);

  rerender(<WorkQueue items={[]} onSelect={() => undefined} selectedNeedId={undefined} />);
  expect(screen.getByText(/no open navigation needs/i)).toBeVisible();
});
