import { defineConfig, devices } from "@playwright/test";

const isMobile = process.env.OJCC_LIVE_DEVICE === "mobile";

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for the live journey`);
  return value;
}

function platformEnvironment(): Record<string, string> {
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
  return Object.fromEntries(
    allowed.flatMap((name) => (process.env[name] ? [[name, process.env[name] as string]] : [])),
  );
}

const baseEnvironment = platformEnvironment();

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
      command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8011",
      cwd: "../../services/api",
      env: {
        ...baseEnvironment,
        DATABASE_URL: requiredEnvironment("DATABASE_URL"),
        APP_ENV: "local",
        DEMO_SESSION_SECRET: requiredEnvironment("DEMO_SESSION_SECRET"),
        DEMO_ORGANIZATION_ID: requiredEnvironment("DEMO_ORGANIZATION_ID"),
      },
      port: 8011,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3011",
      cwd: ".",
      env: {
        ...baseEnvironment,
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
