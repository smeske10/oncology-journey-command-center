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

function isolatedEnvironment(approved: Record<string, string>): Record<string, string> {
  // Playwright merges process.env into web-server env. Undefined overrides remove
  // every inherited key before the approved platform and child values are added.
  const inheritedEnvironmentRemovals = Object.fromEntries(
    Object.keys(process.env).map((name) => [name, undefined]),
  ) as Record<string, string>;
  return {
    ...inheritedEnvironmentRemovals,
    ...platformEnvironment(),
    ...approved,
  };
}

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
      env: isolatedEnvironment({
        DATABASE_URL: requiredEnvironment("DATABASE_URL"),
        APP_ENV: "local",
        DEMO_SESSION_SECRET: requiredEnvironment("DEMO_SESSION_SECRET"),
        DEMO_ORGANIZATION_ID: requiredEnvironment("DEMO_ORGANIZATION_ID"),
        DEMO_ACTORS_JSON: requiredEnvironment("DEMO_ACTORS_JSON"),
      }),
      port: 8011,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3011",
      cwd: ".",
      env: isolatedEnvironment({
        OJCC_API_ORIGIN: "http://127.0.0.1:8011",
      }),
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
