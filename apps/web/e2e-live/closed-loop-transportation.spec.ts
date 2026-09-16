import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import type { NavigatorNeedWorkspaceResponse, NavigatorQueueResponse } from "../lib/api-client";

function platformEnvironment(): NodeJS.ProcessEnv {
  const allowed = [
    "PATH",
    "Path",
    "PATHEXT",
    "COMSPEC",
    "SystemRoot",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "CI",
  ];
  return {
    NODE_ENV: process.env.NODE_ENV,
    ...Object.fromEntries(
      allowed.flatMap((name) => (process.env[name] ? [[name, process.env[name]]] : [])),
    ),
  };
}

async function expectPageFitsViewport(page: Page) {
  if (test.info().project.name === "mobile-chromium") {
    expect(page.viewportSize()!.width).toBeLessThan(600);
  }
  const widths = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
}

test("persists the synthetic transportation journey from review through closure", async ({ browser }) => {
  test.setTimeout(60_000);
  const navigatorContext = await browser.newContext();
  const patientContext = await browser.newContext();
  const navigator = await navigatorContext.newPage();
  const patient = await patientContext.newPage();

  const initialQueueResponse = navigator.waitForResponse((response) => response.url().endsWith("/api/v1/navigator/queue") && response.ok());
  await navigator.goto("/demo/navigator");
  await expect(navigator.getByRole("heading", { name: "Navigator command center" })).toBeVisible();
  const initialQueue = await (await initialQueueResponse).json() as NavigatorQueueResponse;
  const transportationCandidates = await Promise.all(initialQueue.items.filter((item) => item.kind === "transportation").map(async (item) => {
    const response = await navigator.request.get(`/api/v1/navigator/needs/${item.need_id}/workspace`);
    expect(response.ok()).toBe(true);
    return { item, data: await response.json() as NavigatorNeedWorkspaceResponse };
  }));
  const originalCandidates = transportationCandidates.filter(({ data }) => data.tasks.some((task) => task.title === "Arrange transportation for oncology follow-up"));
  expect(originalCandidates).toHaveLength(1);
  const original = originalCandidates[0];
  const initialCorrection = original.data.comparisons.correction;
  const originalSourceId = initialCorrection.current_submission_id!;
  const originalPredecessorId = initialCorrection.previous_submission_id!;
  expect(initialCorrection.status).toBe("available");
  expect(original.data.comparisons.between_check_ins.status).toBe("available");
  expect(original.data.comparisons.between_check_ins.current_submission_id).toBe(originalSourceId);
  expect(original.data.comparisons.between_check_ins.previous_submission_id).not.toBe(originalPredecessorId);
  expect(original.item.due_at).toBeNull();
  const transportationQueueItem = navigator.getByLabel("Queue items").getByRole("button")
    .filter({ hasText: original.item.patient_display_name })
    .filter({ hasText: `${original.item.priority.level} operational priority` })
    .filter({ hasNotText: /Due / });
  await expect(transportationQueueItem).toHaveCount(1);
  await transportationQueueItem.focus();
  await navigator.keyboard.press("Enter");
  const viewport = navigator.viewportSize();
  expect(viewport).not.toBeNull();
  if (test.info().project.name === "mobile-chromium") {
    expect(viewport!.width).toBeLessThan(600);
  }
  const workspace = navigator.getByRole("region", { name: "Selected need workspace" });
  await expect(
    workspace.getByRole("heading", { name: "Exact evidence" }).locator("..").getByText("yes", { exact: true }),
  ).toBeVisible();
  await expectPageFitsViewport(navigator);

  await patient.goto("/demo/patient");
  const patientFlow = patient.getByRole("region", { name: "Patient check-in", exact: true });
  const history = patient.getByRole("region", { name: "Journey history" });
  await expect(history.getByText("Check-in submitted", { exact: true })).toHaveCount(2);
  await expect(history.getByText("Check-in corrected", { exact: true })).toHaveCount(1);
  const submittedBefore = await history.getByText("Check-in submitted", { exact: true }).count();
  const correctedBefore = await history.getByText("Check-in corrected", { exact: true }).count();
  expect(submittedBefore).toBe(2);
  expect(correctedBefore).toBe(1);
  await expect(patientFlow.getByRole("button", { name: "New check-in", exact: true })).toBeVisible();
  await expectPageFitsViewport(patient);
  await patientFlow.getByRole("button", { name: "New check-in", exact: true }).click();
  await patientFlow.getByRole("button", { name: "It is worse" }).click();
  await patientFlow.getByRole("button", { name: "Continue" }).click();
  await patientFlow.getByRole("button", { name: "Yes", exact: true }).click();
  await patientFlow.getByRole("button", { name: "Continue" }).click();
  const newSubmissionResponse = patient.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/submissions"));
  await patientFlow.getByRole("button", { name: "Submit check-in", exact: true }).click();
  const newSubmission = await (await newSubmissionResponse).json() as { id: string; supersedes_submission_id: string | null };
  expect(newSubmission.supersedes_submission_id).toBeNull();
  expect(newSubmission.id).not.toBe(originalSourceId);
  await expect(patientFlow.getByRole("heading", { name: "Your synthetic check-in was saved", exact: true })).toBeVisible();
  await expect(history.getByText("Check-in submitted", { exact: true })).toHaveCount(submittedBefore + 1);
  await expect(history.getByText("Check-in corrected", { exact: true })).toHaveCount(correctedBefore);

  await workspace.getByRole("button", { name: "Refresh selected need" }).click();
  const independent = workspace.getByRole("region", { name: "Latest independent check-ins" });
  const correction = workspace.getByRole("region", { name: "Selected need correction" });
  const evidence = workspace.getByRole("region", { name: "Exact evidence" });
  await expect(independent).toContainText(`Previous submission: ${originalSourceId}`);
  await expect(independent).toContainText(`Current submission: ${newSubmission.id}`);
  await expect(independent.getByRole("row", { name: "pain_change same worse", exact: true })).toBeVisible();
  await expectPageFitsViewport(navigator);
  await expect(correction).toContainText(`Previous submission: ${originalPredecessorId}`);
  await expect(correction).toContainText(`Current submission: ${originalSourceId}`);
  await expect(evidence.getByText("same", { exact: true })).toBeVisible();
  await expect(evidence.getByText("yes", { exact: true })).toBeVisible();
  await expect(evidence).toContainText(`Source submission: ${originalSourceId}`);

  const refreshedDefinitionResponse = patient.waitForResponse((response) => response.url().endsWith("/api/v1/patient/check-ins/current") && response.ok());
  await patientFlow.getByRole("button", { name: "Start another check-in" }).click();
  const refreshedDefinition = await (await refreshedDefinitionResponse).json() as { active_submission_id: string | null };
  expect(refreshedDefinition.active_submission_id).toBe(newSubmission.id);
  await patientFlow.getByRole("button", { name: "Correct latest submission" }).click();
  await patientFlow.getByRole("button", { name: "About the same" }).click();
  await patientFlow.getByRole("button", { name: "Continue" }).click();
  await patientFlow.getByRole("button", { name: "Yes", exact: true }).click();
  await patientFlow.getByRole("button", { name: "Continue" }).click();
  const correctedSubmissionResponse = patient.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/submissions"));
  await patientFlow.getByRole("button", { name: "Submit correction", exact: true }).click();
  const correctedSubmission = await (await correctedSubmissionResponse).json() as { id: string; supersedes_submission_id: string | null };
  expect(correctedSubmission.supersedes_submission_id).toBe(newSubmission.id);
  await expect(patientFlow.getByRole("heading", { name: "Your synthetic correction was saved", exact: true })).toBeVisible();
  await expect(history.getByText("Check-in submitted", { exact: true })).toHaveCount(submittedBefore + 1);
  await expect(history.getByText("Check-in corrected", { exact: true })).toHaveCount(correctedBefore + 1);
  await workspace.getByRole("button", { name: "Refresh selected need" }).click();
  await expect(independent).toContainText(`Previous submission: ${originalSourceId}`);
  await expect(independent).toContainText(`Current submission: ${correctedSubmission.id}`);
  await expect(independent).toContainText("No field differences between these submissions.");
  await expect(correction).toContainText(`Current submission: ${originalSourceId}`);
  await expect(evidence.getByText("same", { exact: true })).toBeVisible();
  await expect(evidence).toContainText(`Source submission: ${originalSourceId}`);

  await workspace.getByRole("radio", { name: /arrange transportation for oncology follow-up/i }).click();
  await workspace.getByRole("button", { name: "Approve proposal" }).click();

  const due = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  const localDue = new Date(due.getTime() - due.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
  await workspace.getByLabel("Task due time").fill(localDue);
  await workspace.getByRole("button", { name: "Claim task" }).click();
  await expect(workspace.getByText(/task assigned/i)).toBeVisible();
  await workspace.getByRole("button", { name: "Start task" }).click();
  await expect(workspace.getByText(/task in progress/i)).toBeVisible();
  await workspace.getByRole("button", { name: "Complete task" }).click();
  await expect(workspace.getByRole("status")).toHaveText(/task completed.*follow-up requested/i);

  await patient.reload();
  const followUp = patient.getByRole("region", { name: "Patient follow-up" });
  await expect(followUp.getByText(/did this navigation support address your reported need/i)).toBeVisible();
  await followUp.getByRole("radio", { name: "Resolved", exact: true }).click();
  await followUp.getByRole("button", { name: "Send follow-up response" }).click();
  await expect(followUp.getByText(/response saved/i)).toBeVisible();

  await workspace.getByRole("button", { name: "Refresh selected need" }).click();
  await expect(workspace.getByText(/patient responded: resolved/i)).toBeVisible();
  await workspace.getByRole("radio", { name: "Resolved outcome", exact: true }).click();
  await workspace.getByRole("button", { name: "Preview closure" }).click();
  await expect(workspace.getByText(/no active tasks will be cancelled/i)).toBeVisible();
  await workspace.getByRole("button", { name: "Confirm outcome" }).click();
  await expect(workspace.getByRole("status")).toHaveText(/need closed as resolved/i);
  await expect(navigator.getByLabel("Queue items").getByRole("button")).toHaveCount(1);
  await expect(workspace.getByText("Outcome recorded. Need closed as resolved.", { exact: true })).toBeVisible();

  await navigator.reload();
  await expect(navigator.getByRole("region", { name: "Selected need workspace" })).toContainText(
    "Need closed as resolved",
  );
  await patient.reload();
  await expect(patient.getByRole("region", { name: "Patient follow-up" })).toContainText(
    "Response saved",
  );
  await expect(patient.getByRole("region", { name: "Journey history" })).toContainText(
    "Outcome recorded",
  );

  assertDatabaseJourney(newSubmission.id, correctedSubmission.id, original.item.need_id);
  await patientContext.close();
  await navigatorContext.close();
});

function assertDatabaseJourney(newSubmissionId: string, correctedSubmissionId: string, originalNeedId: string) {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error("DATABASE_URL is required for live database assertions");
  const migrationUsername = process.env.OJCC_MIGRATION_USERNAME;
  if (!migrationUsername) throw new Error("OJCC_MIGRATION_USERNAME is required");
  const runtimeUsername = decodeURIComponent(new URL(databaseUrl).username);
  const apiRoot = path.resolve(process.cwd(), "../../services/api");
  const verification = `
import json, os, sys
from sqlalchemy import create_engine, text
from scripts.seed_demo import DEMO_IDS
new_submission_id, corrected_submission_id, original_need_id = sys.argv[1:]
assert original_need_id == str(DEMO_IDS["transportation_need"])
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as connection:
    row = connection.execute(text("""
        SELECT
          current_user,
          session_user,
          (SELECT count(*) FROM follow_up_request r JOIN navigation_task t ON t.id=r.navigation_task_id WHERE t.title='Arrange transportation for oncology follow-up') AS requests,
          (SELECT count(*) FROM follow_up_response r JOIN follow_up_request q ON q.id=r.follow_up_request_id JOIN navigation_task t ON t.id=q.navigation_task_id WHERE t.title='Arrange transportation for oncology follow-up') AS responses,
          (SELECT count(*) FROM outcome o JOIN reported_need n ON n.id=o.reported_need_id JOIN navigation_task t ON t.reported_need_id=n.id WHERE t.title='Arrange transportation for oncology follow-up') AS outcomes,
          (SELECT count(*) FROM navigation_task t JOIN proposed_change p ON p.id=t.authorized_proposed_change_id WHERE t.title='Arrange transportation for oncology follow-up' AND p.value_schema_version=2) AS exact_bindings,
          (SELECT count(*) FROM audit_event e JOIN navigation_task t ON t.id=e.entity_id WHERE t.title='Arrange transportation for oncology follow-up') AS task_audits
    """)).one()
    submission_ids = [str(DEMO_IDS["submission_earlier"]), str(DEMO_IDS["submission_v1"]), str(DEMO_IDS["submission_v2"]), new_submission_id, corrected_submission_id]
    submissions = [dict(record) for record in connection.execute(text("""
        SELECT id::text AS id, supersedes_submission_id::text AS predecessor,
          check_in_definition_id::text AS definition_id, care_episode_id::text AS episode_id,
          answers->'items' AS items
        FROM check_in_submission WHERE id IN (CAST(:earlier AS uuid), CAST(:v1 AS uuid), CAST(:v2 AS uuid), CAST(:new AS uuid), CAST(:correction AS uuid))
    """), dict(zip(["earlier", "v1", "v2", "new", "correction"], submission_ids))).mappings()]
    recurrent = connection.execute(text("""
        SELECT status::text AS status, reopened_from_need_id::text AS predecessor,
          source_submission_id::text AS source
        FROM reported_need WHERE id=CAST(:id AS uuid)
    """), {"id": str(DEMO_IDS["open_need"])}).mappings().one()
    original_source = connection.execute(text("SELECT source_submission_id::text FROM reported_need WHERE id=CAST(:id AS uuid)"), {"id": original_need_id}).scalar_one()
print(json.dumps({"journey": list(row), "submissions": submissions, "original_source": original_source,
  "recurrent": dict(recurrent), "ids": {key: str(DEMO_IDS[key]) for key in ["submission_earlier", "submission_v1", "submission_v2", "definition_v2", "episode", "closed_need"]}}))
engine.dispose()
`;
  const output = execFileSync(
    "python",
    ["-c", verification, newSubmissionId, correctedSubmissionId, originalNeedId],
    {
      cwd: apiRoot,
      encoding: "utf8",
      env: { ...platformEnvironment(), DATABASE_URL: databaseUrl },
    },
  );
  const persisted = JSON.parse(output.trim()) as {
    journey: [string, string, number, number, number, number, number];
    submissions: Array<{ id: string; predecessor: string | null; definition_id: string; episode_id: string; items: Array<{ link_id: string; value: unknown }> }>;
    original_source: string;
    recurrent: { status: string; predecessor: string; source: string | null };
    ids: Record<string, string>;
  };
  const [currentUser, sessionUser, requests, responses, outcomes, bindings, audits] = persisted.journey;
  expect(currentUser).toBe(runtimeUsername);
  expect(sessionUser).toBe(runtimeUsername);
  expect(currentUser).not.toBe(migrationUsername);
  expect({ requests, responses, outcomes, bindings }).toEqual({
    requests: 1,
    responses: 1,
    outcomes: 1,
    bindings: 1,
  });
  expect(audits).toBeGreaterThanOrEqual(3);
  expect(persisted.submissions).toHaveLength(5);
  for (const [id, predecessor, pain, transportation] of [
    [persisted.ids.submission_earlier, null, "better", "no"],
    [persisted.ids.submission_v1, null, "worse", "yes"],
    [persisted.ids.submission_v2, persisted.ids.submission_v1, "same", "yes"],
    [newSubmissionId, null, "worse", "yes"],
    [correctedSubmissionId, newSubmissionId, "same", "yes"],
  ]) {
    const submission = persisted.submissions.find((item) => item.id === id);
    expect(submission).toBeDefined();
    expect(submission!.predecessor).toBe(predecessor);
    expect(submission!.definition_id).toBe(persisted.ids.definition_v2);
    expect(submission!.episode_id).toBe(persisted.ids.episode);
    expect(submission!.items.find((item) => item.link_id === "pain_change")?.value).toBe(pain);
    expect(submission!.items.find((item) => item.link_id === "transportation")?.value).toBe(transportation);
  }
  expect(persisted.original_source).toBe(persisted.ids.submission_v2);
  expect(persisted.recurrent).toEqual({ status: "open", predecessor: persisted.ids.closed_need, source: null });
}
