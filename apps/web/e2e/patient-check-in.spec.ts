import { expect, test } from "@playwright/test";

test("submits a first synthetic check-in and then corrects it", async ({ page }) => {
  const firstSubmissionId = "45c18270-f6c2-4e6a-81d6-a54535af9fd7";
  let activeSubmissionId: string | null = null;
  let submissionAttempt = 0;
  await page.route("**/api/v1/demo/session/supporting_actor", (route) =>
    route.fulfill({ status: 204 }),
  );
  await page.route("**/api/v1/patient/check-ins/current", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: "a6c304e8-8070-4a65-90cc-168a4fb6d998",
        title: "Today's check-in",
        questionnaire_version: "breast-active-v1",
        active_submission_id: activeSubmissionId,
        questions: [
          {
            link_id: "nausea_change",
            label: "Since your last check-in, is your nausea better, the same, or worse?",
            options: [
              { value: "better", label: "It is better" },
              { value: "same", label: "About the same" },
              { value: "worse", label: "It is worse" },
            ],
          },
          {
            link_id: "transportation",
            label: "Do you need transportation support?",
            options: [
              { value: "yes", label: "Yes" },
              { value: "no", label: "No" },
            ],
          },
        ],
      }),
    }),
  );
  await page.route("**/api/v1/patient/check-ins/*/submissions", async (route) => {
    submissionAttempt += 1;
    const payload = route.request().postDataJSON();
    if (submissionAttempt === 1) {
      expect(payload.supersedes_submission_id).toBeUndefined();
      activeSubmissionId = firstSubmissionId;
      await route.fulfill({
        contentType: "application/json",
        status: 201,
        body: JSON.stringify({
          id: firstSubmissionId,
          status: "submitted",
          questionnaire_version: "breast-active-v1",
          submitted_at: "2026-08-17T12:00:00+00:00",
          supersedes_submission_id: null,
        }),
      });
      return;
    }
    expect(payload.supersedes_submission_id).toBe(firstSubmissionId);
    if (submissionAttempt === 2) {
      await route.fulfill({
        contentType: "application/json",
        status: 422,
        body: JSON.stringify({
          detail: {
            code: "answers_invalid",
            message: "Please correct the highlighted answer",
          },
        }),
      });
      return;
    }
    expect(payload.answers[0].value).toBe("same");
    await route.fulfill({
      contentType: "application/json",
      status: 201,
      body: JSON.stringify({
        id: "f5658c1c-3a98-43d1-9925-4c9d278713e6",
        status: "submitted",
        questionnaire_version: "breast-active-v1",
        submitted_at: "2026-08-17T12:00:00+00:00",
        supersedes_submission_id: firstSubmissionId,
      }),
    });
  });

  await page.goto("/demo/patient");

  await expect(page.getByRole("note", { name: /synthetic demonstration warning/i })).toBeVisible();
  await expect(page.getByText(/need urgent help/i)).toBeVisible();
  await page.getByRole("button", { name: "It is worse" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Question 2 of 2")).toBeVisible();
  await page.getByRole("button", { name: "No" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
  await page.getByRole("button", { name: "Submit check-in" }).click();
  await expect(page.getByRole("heading", { name: /synthetic check-in was saved/i })).toBeVisible();

  await page.reload();

  await page.getByRole("button", { name: "It is worse" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "No" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
  await page.getByRole("button", { name: "Submit correction" }).click();

  await expect(page.getByText("Please correct the highlighted answer", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "About the same" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Question 2 of 2")).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Submit correction" }).click();

  await expect(page.getByRole("heading", { name: /synthetic correction was saved/i })).toBeVisible();
});
