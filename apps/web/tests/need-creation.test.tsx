import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

const api = vi.hoisted(() => ({
  getNavigatorNeedCandidates: vi.fn(), getNeedCreationHistory: vi.fn(),
  createNeedCreationProposal: vi.fn(), recordNeedCreationDecision: vi.fn(),
}));
vi.mock("../lib/api-client", async (original) => ({
  ...await original<typeof import("../lib/api-client")>(), ...api,
}));
import { NeedCandidates } from "../components/navigator/need-candidates";

const candidate = {
  patient_id: "patient", patient_display_name: "Synthetic Patient", care_episode_id: "episode",
  chain_root_id: "root", source_submission_id: "leaf", check_in_at: "2026-09-24T12:00:00Z",
  source_submitted_at: "2026-09-24T12:00:00Z", is_correction: false,
  evidence: { question: "Need synthetic transportation support?", value: "yes", text: "yes" },
  linked_needs: [], other_transportation_needs: [],
};
const proposal = {
  id: "proposal", patient_display_name: "Synthetic Patient", patient_id: "patient", care_episode_id: "episode",
  chain_root_id: "root", source_submission_id: "leaf", rationale: "Synthetic review",
  evidence: { question: { label: "Need synthetic transportation support?" }, display_text: "yes" },
  evidence_sha256: "abc123", policy_snapshot: { required_approval_count: 1, allow_self_approval: true },
  state: "pending", source_changed: false, decisions: [], proposed_at: "2026-09-24T12:00:00Z",
};

beforeEach(() => {
  vi.resetAllMocks();
  api.getNavigatorNeedCandidates.mockResolvedValue({ candidates: [candidate], unavailable: [] });
  api.getNeedCreationHistory.mockResolvedValue({ proposals: [] });
  api.createNeedCreationProposal.mockResolvedValue(proposal);
  api.recordNeedCreationDecision.mockResolvedValue({ outcome: "created", reported_need_id: "need" });
});

test("navigator reviews server evidence before explicit approval creates a need", async () => {
  render(<NeedCandidates enabled />);
  fireEvent.change(await screen.findByLabelText("Reason for proposing this need"), { target: { value: "Synthetic review" } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare approval review" }));
  expect(await screen.findByRole("button", { name: "Approve and create reported need" })).toBeVisible();
  expect(api.recordNeedCreationDecision).not.toHaveBeenCalled();
  expect(api.createNeedCreationProposal).toHaveBeenCalledWith(expect.objectContaining({
    chain_root_id: "root", source_submission_id: "leaf", rationale: "Synthetic review",
  }));
  fireEvent.click(screen.getByRole("button", { name: "Approve and create reported need" }));
  expect(await screen.findByText(/Reported need created/)).toBeVisible();
  expect(api.recordNeedCreationDecision).toHaveBeenCalledWith("proposal", expect.objectContaining({ decision: "approved" }));
});

test("uncertain approval retries the identical request", async () => {
  api.getNeedCreationHistory.mockResolvedValue({ proposals: [proposal] });
  api.recordNeedCreationDecision.mockRejectedValueOnce(new Error("network response lost"))
    .mockResolvedValueOnce({ outcome: "created", reported_need_id: "need" });
  render(<NeedCandidates enabled />);
  fireEvent.click(await screen.findByRole("button", { name: "Approve and create reported need" }));
  fireEvent.click(await screen.findByRole("button", { name: "Retry approval" }));
  await waitFor(() => expect(api.recordNeedCreationDecision).toHaveBeenCalledTimes(2));
  expect(api.recordNeedCreationDecision.mock.calls[1]).toEqual(api.recordNeedCreationDecision.mock.calls[0]);
});

test("later negative correction remains visible in creation history", async () => {
  api.getNavigatorNeedCandidates.mockResolvedValue({ candidates: [], unavailable: [] });
  api.getNeedCreationHistory.mockResolvedValue({ proposals: [{ ...proposal, state: "created", source_changed: true,
    decisions: [{ id: "decision", outcome: "created", authorized_by_user_id: "navigator", authorized_at: "2026-09-24T12:01:00Z", reported_need_id: "need" }],
  }] });
  render(<NeedCandidates enabled />);
  expect(await screen.findByText(/A later correction exists/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Approve and create reported need" })).not.toBeInTheDocument();
});
