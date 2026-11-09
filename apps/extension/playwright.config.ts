import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  use: { baseURL: "http://127.0.0.1:5173" },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
  },
  projects: [
    {
      name: "sidepanel",
      testMatch: ["panel.spec.ts", "redesign.spec.ts"],
      use: { viewport: { width: 390, height: 844 } },
    },
    { name: "extension", testMatch: "extension.spec.ts" },
    {
      name: "integration",
      testMatch: "integration.spec.ts",
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
});
