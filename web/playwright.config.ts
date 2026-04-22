import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8765",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // Start backend and built frontend before running e2e tests
  webServer: {
    command: "uv run fleetroll web",
    url: "http://127.0.0.1:8765/api/health",
    reuseExistingServer: !process.env["CI"],
    cwd: "../",
  },
});
