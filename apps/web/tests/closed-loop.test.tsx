import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { components } from "../lib/api-types";
import { FollowUpPanel } from "../components/patient/follow-up-panel";
import { NeedWorkspace } from "../components/navigator/need-workspace";
import { ApiError } from "../lib/api-client";
import type { NavigatorPatientCaseResponse, NavigatorQueueResponse } from "../lib/api-client";

const api = vi.hoisted(() => ({
  bootstrapNavigatorQueue: vi.fn(),
  getNavigatorNeedWorkspace: vi.fn(),
  getNavigatorPatientCase: vi.fn(),
  decideProposal: vi.fn(),
  claimTask: vi.fn(),
  startTask: vi.fn(),
  completeTask: vi.fn(),
  getOutcomePreview: vi.fn(),
  recordOutcome: vi.fn(),
}));

vi.mock("../lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api-client")>();
  return { ...actual, ...api };
});

import NavigatorDemoPage from "../app/demo/navigator/page";

type Workspace = components["schemas"]["NavigatorNeedWorkspaceRead"];

const queue: NavigatorQueueResponse = {
  items: [
    {
      created_at: "2026-02-07T12:00:00Z",
      due_at: null,
      evidence: [{ field: "transportation", text: "yes" }],
      kind: "transportation",
      need_id: "need-transportation",
      owner_id: null,
      patient_display_name: "Synthetic Patient",
      patient_id: "patient-synthetic",
      priority: { level: "routine", reasons: ["configured_kind_transportation"], score: 10 },
    },
  ],
};

const patientCase: NavigatorPatientCaseResponse = {
  longitudinal_submissions: [],
  navigation_tasks: [],
  open_needs: queue.items,
  patient: { id: "patient-synthetic", display_name: "Synthetic Patient" },
  safety_signals: [],
  upcoming_synthetic_appointment: null,
};

const workspace: Workspace = {
  comparisons: {
    between_check_ins: {
      current_submission_id: "submission-2",
      previous_submission_id: "submission-1",
      deltas: [{
        current_present: true,
        current_value: "yes",
        field_identifier: "transportation",
        previous_present: true,
        previous_value: "yes",
      }],
      label: "Between check-ins",
      status: "available",
    },
    correction: {
      current_submission_id: "submission-2",
      previous_submission_id: "submission-1",
      deltas: [],
      label: "Correction",
      status: "available",
    },
  },
  evidence: [
    {
      display_text: "Need synthetic transportation support?: yes",
      field_identifier: "transportation",
      provenance_kind: "source_submission",
      source_submission_id: "submission-2",
      value: "yes",
      value_present: true,
    },
  ],
  follow_ups: [],
  need: {
    care_episode_id: "episode-1",
    created_at: "2026-02-07T12:00:00Z",
    effective_state: "open",
    id: "need-transportation",
    kind: "transportation",
    patient_display_name: "Synthetic Patient",
    patient_id: "patient-synthetic",
    raw_status: "open",
    reopened_from_need_id: null,
  },
  outcome: null,
  tasks: [
    {
      assignee_user_id: null,
      authorized_proposed_change_id: null,
      completed_at: null,
      created_at: "2026-02-07T12:00:00Z",
      due_at: null,
      id: "task-transportation",
      proposals: [
        {
          change_type: "authorize_navigation_task",
          decisions: [],
          execution_authorized: false,
          id: "proposal-transportation",
          policy: {
            allow_self_approval: false,
            deterministic_severity_threshold: null,
            id: "policy-task",
            required_approval_count: 1,
            required_approver_role: "navigator",
            version: 1,
          },
          proposed_at: "2026-02-07T12:00:00Z",
          proposed_value: { title: "Arrange transportation for oncology follow-up", resources: [] },
          rationale: "Synthetic transportation support proposed from the latest check-in.",
          resources: [
            {
              approved_at: null,
              category: "transportation",
              delivered_at: null,
              id: "snapshot-transportation",
              match_rationale: "Serves the upcoming oncology visit.",
              metadata: { synthetic: true },
              name: "Synthetic community ride network",
              proposed_at: "2026-02-07T12:00:00Z",
              resource_id: "resource-transportation",
              url: "https://example.test/community-rides",
            },
          ],
          reviewable: true,
          root_proposal_id: "proposal-transportation",
          state: "pending",
          supersedes_proposed_change_id: null,
          supported: true,
          title: "Arrange transportation for oncology follow-up",
          unsupported_reason: null,
          value_schema_id: "ojcc.authorize-navigation-task",
          value_schema_version: 2,
        },
      ],
      status: "open",
      title: "Arrange transportation for oncology follow-up",
    },
  ],
  timeline: [],
};

beforeEach(() => {
  window.localStorage.clear();
  for (const mock of Object.values(api)) mock.mockReset();
  api.bootstrapNavigatorQueue.mockResolvedValue(queue);
  api.getNavigatorPatientCase.mockResolvedValue(patientCase);
  api.getNavigatorNeedWorkspace.mockResolvedValue(workspace);
});

test("reviews exact evidence, one explicit proposal, resources, and policy before approval", async () => {
  render(<NavigatorDemoPage />);

  await screen.findByText("Need synthetic transportation support?: yes");
  const workspaceRegion = screen.getByRole("region", { name: "Selected need workspace" });
  expect(within(workspaceRegion).getByText("Need synthetic transportation support?: yes")).toBeVisible();
  expect(within(workspaceRegion).getByText("Synthetic community ride network")).toBeVisible();
  expect(within(workspaceRegion).getByText(/one navigator approval required/i)).toBeVisible();

  fireEvent.click(within(workspaceRegion).getByRole("radio", { name: /arrange transportation/i }));
  expect(within(workspaceRegion).getByRole("button", { name: "Approve proposal" })).toBeEnabled();
  expect(within(workspaceRegion).getByRole("button", { name: "Decline proposal" })).toBeEnabled();
});

test("requires a decline reason and does not submit twice while approval is pending", async () => {
  let resolve!: () => void;
  api.decideProposal.mockImplementation(() => new Promise((done) => {
    resolve = () => done({ proposal_state: "declined" });
  }));
  render(<NavigatorDemoPage />);

  await screen.findByText("Need synthetic transportation support?: yes");
  const workspaceRegion = screen.getByRole("region", { name: "Selected need workspace" });
  fireEvent.click(within(workspaceRegion).getByRole("radio", { name: /arrange transportation/i }));
  fireEvent.click(within(workspaceRegion).getByRole("button", { name: "Decline proposal" }));
  expect(within(workspaceRegion).getByRole("alert")).toHaveTextContent(/reason is required/i);

  fireEvent.change(within(workspaceRegion).getByRole("textbox", { name: "Decline reason" }), {
    target: { value: "Resource is not available for the appointment time." },
  });
  const decline = within(workspaceRegion).getByRole("button", { name: "Decline proposal" });
  fireEvent.click(decline);
  fireEvent.click(decline);
  expect(api.decideProposal).toHaveBeenCalledTimes(1);
  expect(decline).toBeDisabled();
  resolve();
  await waitFor(() => expect(api.getNavigatorNeedWorkspace).toHaveBeenCalledTimes(2));
});

test("sends an aware due instant, refetches a 409, and preserves the entered due value", async () => {
  const approved = structuredClone(workspace);
  approved.tasks[0].proposals[0].state = "approved";
  approved.tasks[0].proposals[0].reviewable = false;
  api.claimTask.mockRejectedValue(
    new ApiError("Task changed concurrently.", "persistence", "task_state_conflict", 409),
  );
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  render(<NeedWorkspace onRefresh={onRefresh} workspace={approved} />);

  fireEvent.click(screen.getByRole("radio", { name: /arrange transportation/i }));
  const due = screen.getByLabelText("Task due time");
  fireEvent.change(due, { target: { value: "2099-09-10T14:30" } });
  fireEvent.click(screen.getByRole("button", { name: "Claim task" }));

  await waitFor(() => expect(api.claimTask).toHaveBeenCalledOnce());
  expect(api.claimTask.mock.calls[0][2]).toMatch(/^2099-09-10T\d{2}:30:00\.000Z$/);
  await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
  expect(due).toHaveValue("2099-09-10T14:30");
  expect(screen.getByRole("alert")).toHaveTextContent(/canonical data was refreshed/i);
});

test("blocks an invalid due value and unsupported proposal execution", () => {
  const unsupported = structuredClone(workspace);
  unsupported.tasks[0].proposals[0].state = "approved";
  unsupported.tasks[0].proposals[0].supported = false;
  unsupported.tasks[0].proposals[0].unsupported_reason = "Unsupported schema version";
  render(<NeedWorkspace onRefresh={vi.fn().mockResolvedValue(undefined)} workspace={unsupported} />);

  fireEvent.click(screen.getByRole("radio", { name: /arrange transportation/i }));
  expect(screen.getByRole("button", { name: "Claim task" })).toBeDisabled();
  expect(screen.getByText(/unsupported schema version/i)).toBeVisible();
});

test("validates a missing due time before transport", () => {
  const approved = structuredClone(workspace);
  approved.tasks[0].proposals[0].state = "approved";
  approved.tasks[0].proposals[0].reviewable = false;
  render(<NeedWorkspace onRefresh={vi.fn().mockResolvedValue(undefined)} workspace={approved} />);

  fireEvent.click(screen.getByRole("radio", { name: /arrange transportation/i }));
  fireEvent.click(screen.getByRole("button", { name: "Claim task" }));

  expect(screen.getByRole("alert")).toHaveTextContent(/valid task due time/i);
  expect(api.claimTask).not.toHaveBeenCalled();
});

test("makes an assigned task read-only when the API due instant is naive", () => {
  const assigned = structuredClone(workspace);
  assigned.tasks[0].status = "assigned";
  assigned.tasks[0].due_at = "2099-09-10T14:30:00";
  assigned.tasks[0].assignee_user_id = "another-navigator";
  render(<NeedWorkspace onRefresh={vi.fn().mockResolvedValue(undefined)} workspace={assigned} />);

  expect(screen.getByRole("alert")).toHaveTextContent(/missing a timezone offset/i);
  expect(screen.getByRole("button", { name: "Start task" })).toBeDisabled();
});

test("reuses one Outcome idempotency key for an uncertain retry of the same payload", async () => {
  const completed = structuredClone(workspace);
  completed.tasks[0].status = "completed";
  completed.tasks[0].completed_at = "2026-09-10T12:00:00Z";
  api.getOutcomePreview.mockResolvedValue({ need_id: workspace.need.id, tasks: [] });
  api.recordOutcome
    .mockRejectedValueOnce(new Error("connection ended after send"))
    .mockResolvedValueOnce({
      cancelled_task_ids: [],
      disposition: "resolved",
      need_id: workspace.need.id,
      note: null,
      outcome_id: "outcome-1",
      recorded_at: "2026-09-10T12:30:00Z",
      recorded_by_user_id: "navigator-1",
    });
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  render(<NeedWorkspace onRefresh={onRefresh} workspace={completed} />);

  fireEvent.click(screen.getByRole("radio", { name: /^Resolved outcome$/ }));
  fireEvent.click(screen.getByRole("button", { name: "Preview closure" }));
  await screen.findByText(/no active tasks will be cancelled/i);
  fireEvent.click(screen.getByRole("button", { name: "Confirm outcome" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Confirm outcome" }));

  await waitFor(() => expect(api.recordOutcome).toHaveBeenCalledTimes(2));
  expect(api.recordOutcome.mock.calls[0][2]).toBe(api.recordOutcome.mock.calls[1][2]);
});

test("blocks PHI-like Outcome notes before creating an idempotent command", async () => {
  api.getOutcomePreview.mockResolvedValue({ need_id: workspace.need.id, tasks: [] });
  render(<NeedWorkspace onRefresh={vi.fn().mockResolvedValue(undefined)} workspace={workspace} />);

  fireEvent.click(screen.getByRole("radio", { name: /^Resolved outcome$/ }));
  fireEvent.change(screen.getByRole("textbox", { name: /outcome note/i }), {
    target: { value: "Email person@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview closure" }));
  await screen.findByText(/no active tasks will be cancelled/i);
  fireEvent.click(screen.getByRole("button", { name: "Confirm outcome" }));

  expect(screen.getByRole("alert")).toHaveTextContent(/remove names, contact details/i);
  expect(api.recordOutcome).not.toHaveBeenCalled();
});

const pendingFollowUp: components["schemas"]["PatientFollowUpRead"] = {
  need_id: "need-transportation",
  note: null,
  prompt: "Did this navigation support address your reported need?",
  prompt_version: 1,
  request_id: "request-1",
  requested_at: "2026-09-10T12:00:00Z",
  responded_at: null,
  response: null,
  status: "awaiting_response",
  task_id: "task-transportation",
};

test("normalizes a blank follow-up note and prevents a slow double submission", async () => {
  let resolve!: () => void;
  const onRespond = vi.fn().mockImplementation(() => new Promise((done) => { resolve = () => done(undefined); }));
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  render(<FollowUpPanel items={[pendingFollowUp]} onRefresh={onRefresh} onRespond={onRespond} />);

  fireEvent.click(screen.getByRole("radio", { name: /^Resolved$/ }));
  fireEvent.change(screen.getByRole("textbox", { name: /add a note/i }), { target: { value: "   " } });
  const send = screen.getByRole("button", { name: "Send follow-up response" });
  fireEvent.click(send);
  fireEvent.click(send);

  expect(onRespond).toHaveBeenCalledOnce();
  expect(onRespond).toHaveBeenCalledWith("request-1", { response: "resolved", note: undefined });
  expect(screen.getByRole("button", { name: /saving response/i })).toBeDisabled();
  resolve();
  await waitFor(() => expect(onRefresh).toHaveBeenCalledOnce());
});

test("blocks PHI-like follow-up notes before transport", () => {
  const onRespond = vi.fn();
  render(
    <FollowUpPanel
      items={[pendingFollowUp]}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onRespond={onRespond}
    />,
  );

  fireEvent.click(screen.getByRole("radio", { name: "I still need help" }));
  fireEvent.change(screen.getByRole("textbox", { name: /add a note/i }), {
    target: { value: "Call me at 212-555-1212" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send follow-up response" }));

  expect(screen.getByRole("alert")).toHaveTextContent(/remove names, contact details/i);
  expect(onRespond).not.toHaveBeenCalled();
});

test("renders answered and closed follow-ups as read-only history", () => {
  render(
    <FollowUpPanel
      items={[
        { ...pendingFollowUp, response: "resolved", responded_at: "2026-09-10T13:00:00Z", status: "answered" },
        { ...pendingFollowUp, request_id: "request-2", status: "unavailable_need_closed" },
      ]}
      onRefresh={vi.fn().mockResolvedValue(undefined)}
      onRespond={vi.fn()}
    />,
  );

  expect(screen.getByText("Response saved: resolved.")).toBeVisible();
  expect(screen.getByText(/request is closed because the need was already closed/i)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Send follow-up response" })).not.toBeInTheDocument();
});

test("shows role recovery for a revoked navigator session", async () => {
  api.decideProposal.mockRejectedValue(
    new ApiError("Current navigator authority is required.", "configuration", undefined, 403),
  );
  render(<NeedWorkspace onRefresh={vi.fn().mockResolvedValue(undefined)} workspace={workspace} />);

  fireEvent.click(screen.getByRole("radio", { name: /arrange transportation/i }));
  fireEvent.click(screen.getByRole("button", { name: "Approve proposal" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/navigator session needs recovery/i);
});

test("retains a selected closed workspace when the queue becomes empty", async () => {
  const closed = structuredClone(workspace);
  closed.need.effective_state = "closed";
  closed.outcome = {
    disposition: "resolved",
    id: "outcome-1",
    note: null,
    recorded_at: "2026-09-10T12:30:00Z",
    recorded_by_user_id: "navigator-1",
  };
  window.localStorage.setItem(
    "ojcc-navigator-selection",
    JSON.stringify({ needId: workspace.need.id, patientId: workspace.need.patient_id }),
  );
  api.bootstrapNavigatorQueue.mockResolvedValue({ items: [] });
  api.getNavigatorNeedWorkspace.mockResolvedValue(closed);

  render(<NavigatorDemoPage />);

  expect(await screen.findByText(/no open navigation needs right now/i)).toBeVisible();
  expect(await screen.findByText("Outcome recorded. Need closed as resolved.", { exact: true })).toBeVisible();
});
