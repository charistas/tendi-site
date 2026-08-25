const { defineConfig } = require("@playwright/test");
const site = require("./site.config.json");

const port = Number(process.env.PORT || site.previewPort);

module.exports = defineConfig({
  testDir: "./tests",
  globalSetup: require.resolve("./tests/global-setup.js"),
  timeout: 30000,
  outputDir: "test-results/playwright",
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }]]
    : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  webServer: {
    command: `python3 -m http.server ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI && !process.env.TENDI_SITE_STRICT_SERVER,
    stdout: "pipe",
    stderr: "pipe"
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" }
    }
  ]
});
