import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import type { PatientTimelineResponse } from "../lib/api-client";

const api = vi.hoisted(() => ({
  bootstrapPatientCheckIn: vi.fn(), getPatientFollowUps: vi.fn(),
  getPatientTimeline: vi.fn(), submitCheckIn: vi.fn(), respondToFollowUp: vi.fn(),
}));
vi.mock("../lib/api-client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../lib/api-client")>(), ...api,
}));
import PatientDemoPage from "../app/demo/patient/page";

const definition = {
  id: "definition", title: "Synthetic check-in", questionnaire_version: "v2", active_submission_id: "old",
  questions: [{ link_id: "pain", label: "Synthetic pain?", options: [{ value: "same", label: "Same" }] }],
};
const saved = { id: "new", status: "submitted", questionnaire_version: "v2", submitted_at: "2026-02-08T09:00:00Z", supersedes_submission_id: null };
const history: PatientTimelineResponse = { events: [{
  event_id: "submission:new", kind: "check_in_submitted", occurred_at: saved.submitted_at,
  source_id: "new", source_type: "check_in_submission", need_id: null, task_id: null,
  summary: "Check-in submitted.", detail: { correction_of_submission_id: null, inherited: false },
}] };

beforeEach(() => {
  vi.resetAllMocks();
  window.localStorage.clear();
  api.bootstrapPatientCheckIn.mockResolvedValue(definition);
  api.getPatientFollowUps.mockResolvedValue({ items: [] });
  api.getPatientTimeline.mockResolvedValue({ events: [] });
  api.submitCheckIn.mockResolvedValue(saved);
});

async function saveNewCheckIn() {
  fireEvent.click(await screen.findByRole("button", { name: "New check-in" }));
  fireEvent.click(screen.getByRole("button", { name: "Same" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit check-in" }));
  await screen.findByRole("heading", { name: "Your synthetic check-in was saved" });
}

test("refreshes journey history after a successful save without resetting the success screen", async () => {
  api.getPatientTimeline.mockResolvedValueOnce({ events: [] }).mockResolvedValue(history);
  render(<PatientDemoPage />);
  await saveNewCheckIn();
  expect(await within(screen.getByRole("region", { name: "Journey history" })).findByText("Check-in submitted.")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Your synthetic check-in was saved" })).toBeVisible();
});

test("keeps a successful save successful if refreshing history fails", async () => {
  api.getPatientTimeline.mockResolvedValueOnce({ events: [] }).mockRejectedValueOnce(new Error("history unavailable")).mockResolvedValue(history);
  render(<PatientDemoPage />);
  await saveNewCheckIn();
  expect(await screen.findByRole("alert")).toHaveTextContent(/history.*unavailable/i);
  expect(screen.queryByRole("button", { name: "Submit check-in" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry journey history" }));
  expect(await within(screen.getByRole("region", { name: "Journey history" })).findByText("Check-in submitted.")).toBeVisible();
  expect(api.submitCheckIn).toHaveBeenCalledTimes(1);
});

test("loads the canonical correction target before starting another check-in", async () => {
  api.bootstrapPatientCheckIn.mockResolvedValueOnce(definition).mockResolvedValue({ ...definition, active_submission_id: "new" });
  render(<PatientDemoPage />);
  await saveNewCheckIn();
  fireEvent.click(screen.getByRole("button", { name: "Start another check-in" }));
  fireEvent.click(await screen.findByRole("button", { name: "Correct latest submission" }));
  fireEvent.click(screen.getByRole("button", { name: "Same" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit correction" }));
  await screen.findByRole("heading", { name: "Your synthetic correction was saved" });
  expect(api.submitCheckIn.mock.calls[1][1].supersedes_submission_id).toBe("new");
});

test("retains the success screen when loading the next check-in fails", async () => {
  api.bootstrapPatientCheckIn.mockResolvedValueOnce(definition).mockRejectedValue(new Error("session unavailable"));
  render(<PatientDemoPage />);
  await saveNewCheckIn();
  fireEvent.click(screen.getByRole("button", { name: "Start another check-in" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/check-in was saved/i));
  expect(screen.getByRole("heading", { name: "Your synthetic check-in was saved" })).toBeVisible();
  expect(api.submitCheckIn).toHaveBeenCalledTimes(1);
});
