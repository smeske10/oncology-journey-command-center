import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";

test("navigator approves exact evidence and sees a later negative correction", async ({ browser }) => {
  const patientContext = await browser.newContext();
  const navigatorContext = await browser.newContext();
  try {
    const patient = patientContext.request;
    expect((await patient.post("/api/v1/demo/session/supporting_actor")).ok()).toBe(true);
    const definition = await (await patient.get("/api/v1/patient/check-ins/current")).json();
    const body = { questionnaire_version: definition.questionnaire_version,
      answers: [{ link_id: "pain_change", value: "same" }, { link_id: "transportation", value: "yes" }], free_text: "" };
    const submitted = await patient.post(`/api/v1/patient/check-ins/${definition.id}/submissions`, { data: body });
    expect(submitted.ok()).toBe(true);
    const root = (await submitted.json()).id as string;
    const page = await navigatorContext.newPage();
    await page.goto("/demo/navigator");
    const report = page.getByRole("article", { name: `Transportation report ${root}`, exact: true });
    await report.getByLabel("Reason for proposing this need").fill("Review synthetic transportation report");
    const proposalResponse = page.waitForResponse((r) => r.url().endsWith("/need-creation-proposals") && r.request().method() === "POST");
    await report.getByRole("button", { name: "Prepare approval review" }).click();
    const proposal = await (await proposalResponse).json();
    const review = page.getByRole("region", { name: `Creation approval ${proposal.id}`, exact: true });
    await expect(review.getByRole("button", { name: "Approve and create reported need" })).toBeVisible();
    const decisionResponse = page.waitForResponse((r) => r.url().endsWith(`/${proposal.id}/decisions`));
    await review.getByRole("button", { name: "Approve and create reported need" }).click();
    const decision = await (await decisionResponse).json();
    expect(decision.outcome).toBe("created");
    await expect(page.getByText("Reported need created. Approval and evidence are saved in history.")).toBeVisible();
    const corrected = await patient.post(`/api/v1/patient/check-ins/${definition.id}/submissions`, {
      data: { ...body, supersedes_submission_id: root,
        answers: [{ link_id: "pain_change", value: "same" }, { link_id: "transportation", value: "no" }] },
    });
    expect(corrected.ok()).toBe(true);
    await page.reload();
    await expect(review.getByText(/A later correction exists/)).toBeVisible();
    await expect(review.getByText("no", { exact: true })).toBeVisible();
    await expect(review.getByRole("button", { name: "Approve and create reported need" })).toHaveCount(0);
    const widths = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client);
    await page.screenshot({ path: test.info().outputPath("reported-need-approval.png"), fullPage: true });

    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !/^(PG|DATABASE_URL$|MIGRATION_DATABASE_URL$|BOOTSTRAP_DATABASE_URL$|LIVE_)/i.test(key)));
    const proof = execFileSync("python", ["-c", `
import json,os,sys
from sqlalchemy import create_engine,text
engine=create_engine(os.environ["DATABASE_URL"])
with engine.connect() as c:
    r=c.execute(text("""SELECT current_user,n.source_submission_id::text,n.status::text,
        (SELECT count(*) FROM need_creation_decision WHERE reported_need_id=n.id),
        (SELECT count(*) FROM audit_event WHERE entity_id=CAST(:decision AS uuid))
        FROM reported_need n WHERE n.id=CAST(:need AS uuid)"""),
        {"need":sys.argv[1],"decision":sys.argv[2]}).one()
    print(json.dumps(list(r)))
engine.dispose()
`, decision.reported_need_id, decision.id], {
      cwd: path.resolve(process.cwd(), "../../services/api"), encoding: "utf8",
      env: { ...env, NODE_ENV: process.env.NODE_ENV, DATABASE_URL: process.env.DATABASE_URL },
    });
    expect(JSON.parse(proof.trim())).toEqual(["ojcc_api", root, "open", 1, 1]);
  } finally {
    await patientContext.close();
    await navigatorContext.close();
  }
});
