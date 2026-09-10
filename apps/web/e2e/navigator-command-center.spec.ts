import { expect, test } from "@playwright/test";

test("shows explainable canonical open work without routine closure leakage", async ({ page }) => {
  const patientId = "a2a0c52e-982e-4cb1-9921-518d6536305d";
  const openNeedId = "19cc6cba-069b-474a-b81a-56a536f6b7b1";

  await page.route("**/api/v1/demo/session/navigator", (route) =>
    route.fulfill({ status: 204 }),
  );
  await page.route("**/api/v1/navigator/queue", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            need_id: openNeedId,
            patient_id: patientId,
            patient_display_name: "Maya Chen",
            kind: "symptom_change",
            priority: {
              level: "high",
              score: 105,
              reasons: ["worsening_report", "due_soon"],
            },
            evidence: [
              { field: "nausea_change", text: "worse" },
              { field: "free_text", text: "Nausea now interferes with meals." },
            ],
            created_at: "2026-08-17T12:00:00Z",
            due_at: "2026-08-17T16:00:00Z",
            owner_id: null,
          },
        ],
      }),
    }),
  );
  await page.route(`**/api/v1/navigator/patients/${patientId}/case`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        patient: {
          id: patientId,
          display_name: "Maya Chen",
          diagnosis: "Synthetic active-treatment breast cancer pathway",
          consent_status: "synthetic demo consented",
        },
        longitudinal_submissions: [],
        open_needs: [
          {
            need_id: openNeedId,
            patient_id: patientId,
            patient_display_name: "Maya Chen",
            kind: "symptom_change",
            priority: {
              level: "high",
              score: 105,
              reasons: ["worsening_report", "due_soon"],
            },
            evidence: [{ field: "nausea_change", text: "worse" }],
            created_at: "2026-08-17T12:00:00Z",
            due_at: "2026-08-17T16:00:00Z",
            owner_id: null,
          },
        ],
        safety_signals: [
          {
            id: "956b0eac-47f6-44c4-9d27-7dcaaf8c74b3",
            rule_code: "synthetic-review-required",
            severity: "routine",
            status: "dismissed",
            evidence: [{ field: "nausea_change", text: "worse" }],
            created_at: "2026-08-17T12:00:00Z",
          },
        ],
        navigation_tasks: [
          {
            id: "44bde1a2-1981-4579-85cb-1f073d267617",
            reported_need_id: openNeedId,
            title: "Review reported nausea",
            status: "open",
            due_at: null,
            owner_id: null,
            created_at: "2026-08-17T12:00:00Z",
          },
        ],
        upcoming_synthetic_appointment: null,
      }),
    }),
  );
  await page.route(`**/api/v1/navigator/needs/${openNeedId}/workspace`, (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: { code: "need_not_found", message: "Not in smoke fixture" } }),
    }),
  );

  await page.goto("/demo/navigator");

  await expect(page.getByRole("heading", { name: "Navigator command center" })).toBeVisible();
  await expect(page.getByText("Worsening report", { exact: true })).toBeVisible();
  await expect(page.getByText(/nausea now interferes with meals/i)).toBeVisible();
  const patientCase = page.getByRole("region", { name: "Patient case" });
  await expect(patientCase.getByText(/synthetic-review-required — dismissed/i)).toBeVisible();
  await expect(patientCase.getByText(/review reported nausea — open/i)).toBeVisible();
  await expect(page.getByText(/need_closed/i)).toHaveCount(0);
});
