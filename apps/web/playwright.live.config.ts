import { defineConfig, devices } from "@playwright/test";

const isMobile = process.env.OJCC_LIVE_DEVICE === "mobile";

export default defineConfig({
  testDir: "./e2e-live",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3011",
    ...(isMobile ? devices["Pixel 7"] : devices["Desktop Chrome"]),
  },
  webServer: [
    {
      command: "uv run --project . --extra dev uvicorn app.main:app --host 127.0.0.1 --port 8011",
      cwd: "../../services/api",
      env: {
        ...process.env,
        APP_ENV: "local",
      },
      port: 8011,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3011",
      cwd: ".",
      env: {
        ...process.env,
        OJCC_API_ORIGIN: "http://127.0.0.1:8011",
      },
      port: 3011,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [
    {
      name: isMobile ? "mobile-chromium" : "desktop-chromium",
      use: isMobile ? { ...devices["Pixel 7"] } : { ...devices["Desktop Chrome"] },
    },
  ],
});
