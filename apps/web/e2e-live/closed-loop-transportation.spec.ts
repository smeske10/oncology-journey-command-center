import { execFileSync } from "node:child_process";
import path from "node:path";

import { expect, test } from "@playwright/test";

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

test("persists the synthetic transportation journey from review through closure", async ({ browser }) => {
  const navigatorContext = await browser.newContext();
  const patientContext = await browser.newContext();
  const navigator = await navigatorContext.newPage();
  const patient = await patientContext.newPage();

  await navigator.goto("/demo/navigator");
  await expect(navigator.getByRole("heading", { name: "Navigator command center" })).toBeVisible();
  const transportationQueueItem = navigator.getByLabel("Queue items").getByRole("button").nth(1);
  await transportationQueueItem.focus();
  await navigator.keyboard.press("Enter");
  const viewport = navigator.viewportSize();
  expect(viewport).not.toBeNull();
  if (test.info().project.name === "mobile-chromium") {
    expect(viewport!.width).toBeLessThan(600);
  }
  const pageWidths = await navigator.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(pageWidths.scroll).toBeLessThanOrEqual(pageWidths.client);
  const workspace = navigator.getByRole("region", { name: "Selected need workspace" });
  await expect(
    workspace.getByRole("heading", { name: "Exact evidence" }).locator("..").getByText("yes", { exact: true }),
  ).toBeVisible();
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

  await patient.goto("/demo/patient");
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

  assertDatabaseJourney();
  await patientContext.close();
  await navigatorContext.close();
});

function assertDatabaseJourney() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) throw new Error("DATABASE_URL is required for live database assertions");
  const migrationUsername = process.env.OJCC_MIGRATION_USERNAME;
  if (!migrationUsername) throw new Error("OJCC_MIGRATION_USERNAME is required");
  const runtimeUsername = decodeURIComponent(new URL(databaseUrl).username);
  const apiRoot = path.resolve(process.cwd(), "../../services/api");
  const verification = `
import json, os
from sqlalchemy import create_engine, text
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
print(json.dumps(list(row)))
engine.dispose()
`;
  const output = execFileSync(
    "python",
    ["-c", verification],
    {
      cwd: apiRoot,
      encoding: "utf8",
      env: { ...platformEnvironment(), DATABASE_URL: databaseUrl },
    },
  );
  const [currentUser, sessionUser, requests, responses, outcomes, bindings, audits] = JSON.parse(
    output.trim(),
  ) as [string, string, number, number, number, number, number];
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
}
