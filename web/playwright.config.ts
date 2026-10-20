import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  use: {
    baseURL: "http://localhost:18180",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "cd .. && uv run python scripts/serve-test.py",
    wait: { stderr: /Application startup complete/ },
    timeout: 30000,
  },
});
