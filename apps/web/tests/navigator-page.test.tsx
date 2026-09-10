import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { NavigatorNeedWorkspaceResponse, NavigatorPatientCaseResponse, NavigatorQueueResponse } from "../lib/api-client";

const api = vi.hoisted(() => ({
  bootstrapNavigatorQueue: vi.fn(),
  getNavigatorNeedWorkspace: vi.fn(),
  getNavigatorPatientCase: vi.fn(),
}));

vi.mock("../lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api-client")>();
  return { ...actual, ...api };
});

import NavigatorDemoPage from "../app/demo/navigator/page";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const queue: NavigatorQueueResponse = {
  items: [
    {
      created_at: "2026-08-18T10:00:00Z",
      due_at: null,
      evidence: [],
      kind: "transportation",
      need_id: "need-a",
      owner_id: null,
      patient_display_name: "Patient A",
      patient_id: "patient-a",
      priority: { level: "routine", reasons: [], score: 0 },
    },
    {
      created_at: "2026-08-18T10:01:00Z",
      due_at: null,
      evidence: [],
      kind: "transportation",
      need_id: "need-b",
      owner_id: null,
      patient_display_name: "Patient B",
      patient_id: "patient-b",
      priority: { level: "routine", reasons: [], score: 0 },
    },
  ],
};

function patientCase(name: string): NavigatorPatientCaseResponse {
  return {
    longitudinal_submissions: [],
    navigation_tasks: [],
    open_needs: [],
    patient: { display_name: name },
    safety_signals: [],
    upcoming_synthetic_appointment: null,
  };
}

beforeEach(() => {
  api.bootstrapNavigatorQueue.mockReset();
  api.getNavigatorPatientCase.mockReset();
  api.getNavigatorNeedWorkspace.mockReset();
  api.getNavigatorNeedWorkspace.mockResolvedValue(emptyWorkspace());
});

function emptyWorkspace(): NavigatorNeedWorkspaceResponse {
  return {
    comparisons: {
      between_check_ins: { label: "Between check-ins", status: "insufficient_history" },
      correction: { label: "Correction", status: "insufficient_history" },
    },
    evidence: [],
    follow_ups: [],
    need: {
      care_episode_id: "episode-a",
      created_at: "2026-08-18T10:00:00Z",
      effective_state: "open",
      id: "need-a",
      kind: "transportation",
      patient_display_name: "Patient A",
      patient_id: "patient-a",
      raw_status: "open",
      reopened_from_need_id: null,
    },
    outcome: null,
    tasks: [],
    timeline: [],
  };
}

test("keeps the selected patient case when an older request resolves last", async () => {
  const patientA = deferred<NavigatorPatientCaseResponse>();
  const patientB = deferred<NavigatorPatientCaseResponse>();
  api.bootstrapNavigatorQueue.mockResolvedValue(queue);
  api.getNavigatorPatientCase.mockImplementation((patientId: string) =>
    patientId === "patient-a" ? patientA.promise : patientB.promise,
  );

  render(<NavigatorDemoPage />);

  await waitFor(() => {
    expect(api.getNavigatorPatientCase).toHaveBeenCalledWith("patient-a", expect.any(AbortSignal));
  });
  const firstSignal = api.getNavigatorPatientCase.mock.calls[0][1] as AbortSignal;
  fireEvent.click(screen.getByRole("button", { name: /patient b/i }));
  await waitFor(() => {
    expect(api.getNavigatorPatientCase).toHaveBeenCalledWith("patient-b", expect.any(AbortSignal));
  });
  expect(firstSignal.aborted).toBe(true);

  patientB.resolve(patientCase("Patient B"));
  const caseRegion = screen.getByRole("region", { name: "Patient case" });
  await within(caseRegion).findByRole("heading", { name: "Patient B", level: 2 });
  patientA.resolve(patientCase("Patient A"));

  await waitFor(() => {
    expect(within(caseRegion).getByRole("heading", { name: "Patient B", level: 2 })).toBeVisible();
    expect(
      within(caseRegion).queryByRole("heading", { name: "Patient A", level: 2 }),
    ).not.toBeInTheDocument();
  });
});

test("renders open needs from the canonical patient case instead of the queue snapshot", async () => {
  api.bootstrapNavigatorQueue.mockResolvedValue(queue);
  api.getNavigatorPatientCase.mockResolvedValue({
    ...patientCase("Patient A"),
    open_needs: [
      {
        ...queue.items[0],
        kind: "medication_question",
        priority: {
          level: "medium",
          reasons: ["medication_uncertainty"],
          score: 50,
        },
      },
    ],
  });

  render(<NavigatorDemoPage />);

  const caseRegion = await screen.findByRole("region", { name: "Patient case" });
  expect(
    await within(caseRegion).findByText(/medication_question: medication uncertainty/i),
  ).toBeVisible();
  expect(within(caseRegion).queryByText(/transportation:/i)).not.toBeInTheDocument();
});
