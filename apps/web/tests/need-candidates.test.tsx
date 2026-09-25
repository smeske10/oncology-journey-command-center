import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const api = vi.hoisted(() => ({ getNavigatorNeedCandidates: vi.fn(), getNeedCreationHistory: vi.fn() }));
vi.mock("../lib/api-client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../lib/api-client")>(), ...api,
}));

import { NeedCandidates } from "../components/navigator/need-candidates";

function reports(name: string) {
  return { candidates: [{
    patient_id: name, patient_display_name: name, care_episode_id: "episode-a",
    chain_root_id: "root-a", source_submission_id: "correction-a",
    check_in_at: "2026-09-23T10:00:00Z", source_submitted_at: "2026-09-24T10:00:00Z",
    is_correction: true,
    evidence: { field: "transportation", question: "Need synthetic transportation support?", value: "yes", text: "yes" },
    linked_needs: [{ id: "need-closed", kind: "transportation", effective_state: "closed", source_submission_id: "root-a", reopened_from_need_id: null }],
    other_transportation_needs: [{ id: "need-recurrent", kind: "transportation", effective_state: "open", source_submission_id: null, reopened_from_need_id: "need-closed" }],
  }], unavailable: [] };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => { api.getNavigatorNeedCandidates.mockReset(); api.getNeedCreationHistory.mockResolvedValue({ proposals: [] }); });

test("shows exact correction evidence and every related need without review decisions", async () => {
  api.getNavigatorNeedCandidates.mockResolvedValue(reports("Patient A"));
  render(<NeedCandidates enabled />);
  expect(await screen.findByRole("heading", { name: "Patient A" })).toBeVisible();
  expect(screen.getByText("yes", { exact: true })).toBeVisible();
  expect(screen.getByText(/correction of this check-in/i)).toBeVisible();
  expect(screen.getByText("Need need-closed", { exact: true })).toBeVisible();
  expect(screen.getByText("Need need-recurrent", { exact: true })).toBeVisible();
  expect(screen.getByText(/closed/, { selector: "strong" })).toBeVisible();
  expect(screen.getByText(/open/, { selector: "strong" })).toBeVisible();
  expect(screen.queryByRole("button", { name: /approve|decline|reviewed/i })).not.toBeInTheDocument();
});

test("refresh failure clears prior evidence and allows retry", async () => {
  api.getNavigatorNeedCandidates.mockResolvedValueOnce(reports("Patient A"))
    .mockRejectedValueOnce(new Error("unavailable"))
    .mockResolvedValueOnce({ candidates: [], unavailable: [] });
  render(<NeedCandidates enabled />);
  await screen.findByRole("heading", { name: "Patient A" });
  fireEvent.click(screen.getByRole("button", { name: "Refresh transportation reports" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded/i);
  expect(screen.queryByRole("heading", { name: "Patient A" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh transportation reports" }));
  expect(await screen.findByText(/no positive transportation reports/i)).toBeVisible();
});

test("a late response cannot replace newer patient evidence", async () => {
  const old = deferred<ReturnType<typeof reports>>();
  const latest = deferred<ReturnType<typeof reports>>();
  api.getNavigatorNeedCandidates.mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise);
  render(<NeedCandidates enabled />);
  await waitFor(() => expect(api.getNavigatorNeedCandidates).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Refresh transportation reports" }));
  await act(async () => latest.resolve(reports("Patient B")));
  expect(await screen.findByRole("heading", { name: "Patient B" })).toBeVisible();
  await act(async () => old.resolve(reports("Patient A")));
  expect(screen.queryByRole("heading", { name: "Patient A" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Patient B" })).toBeVisible();
});

test("unsupported and ambiguous reports are visibly unavailable", async () => {
  api.getNavigatorNeedCandidates.mockResolvedValue({ candidates: [], unavailable: [{
    patient_id: "a", patient_display_name: "Patient A", care_episode_id: "episode-a",
    chain_root_id: null, reason: "ambiguous_lineage",
  }, {
    patient_id: "b", patient_display_name: "Patient B", care_episode_id: "episode-b",
    chain_root_id: "root-b", reason: "unsupported_questionnaire",
  }] });
  render(<NeedCandidates enabled />);
  expect(await screen.findByText(/correction history is ambiguous/i)).toBeVisible();
  expect(screen.getByText(/questionnaire is not supported/i)).toBeVisible();
});
