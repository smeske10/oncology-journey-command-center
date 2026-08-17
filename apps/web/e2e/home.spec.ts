import { expect, test } from "@playwright/test";

test("shows the synthetic oncology navigation landing page", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: /oncology journey command center/i }),
  ).toBeVisible();
  await expect(page.getByText(/synthetic data only/i)).toBeVisible();
});
