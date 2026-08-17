import { expect, test } from "@playwright/test";

test("moves the synthetic patient check-in to review before submitting", async ({ page }) => {
  await page.goto("/demo/patient");

  await expect(page.getByRole("note", { name: /synthetic demonstration warning/i })).toBeVisible();
  await expect(page.getByText(/need urgent help/i)).toBeVisible();
  await page.getByRole("button", { name: "It is worse" }).click();
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(page.getByRole("heading", { name: "Review your check-in" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit check-in" })).toBeVisible();
});
