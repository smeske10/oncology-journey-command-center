import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { NavigatorPatientCaseResponse, NavigatorQueueResponse } from "../lib/api-client";

const api = vi.hoisted(() => ({
  bootstrapNavigatorQueue: vi.fn(),
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
});

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
  await screen.findByRole("heading", { name: "Patient B" });
  patientA.resolve(patientCase("Patient A"));

  await waitFor(() => {
    expect(screen.getByRole("heading", { name: "Patient B" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Patient A" })).not.toBeInTheDocument();
  });
});
