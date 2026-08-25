import { spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cacheRoot = path.join(root, ".cache");
fs.mkdirSync(cacheRoot, { recursive: true });

function availablePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function runCase(name, marker, expectedMessage) {
  const temporary = fs.mkdtempSync(path.join(cacheRoot, `server-integrity-${name}-`));
  const documentRoot = path.join(temporary, "document-root");
  const testDir = path.join(temporary, "tests");
  const sentinel = path.join(temporary, "test-body-executed");
  fs.mkdirSync(documentRoot);
  fs.mkdirSync(testDir);
  fs.writeFileSync(path.join(documentRoot, "index.html"), "<!doctype html><title>Foreign root</title>\n");
  if (marker !== null) {
    fs.writeFileSync(path.join(documentRoot, ".site-root-marker"), `${marker}\n`);
  }
  fs.writeFileSync(
    path.join(testDir, "sentinel.spec.js"),
    `const fs = require("node:fs"); const { test } = require("@playwright/test"); test("body must not execute", () => fs.writeFileSync(${JSON.stringify(sentinel)}, "executed"));\n`
  );

  const port = await availablePort();
  const configPath = path.join(temporary, "playwright.config.js");
  fs.writeFileSync(configPath, `
const { defineConfig } = require(${JSON.stringify(path.join(root, "node_modules", "@playwright", "test"))});
module.exports = defineConfig({
  testDir: ${JSON.stringify(testDir)},
  outputDir: ${JSON.stringify(path.join(temporary, "playwright-output"))},
  globalSetup: ${JSON.stringify(path.join(root, "tests", "global-setup.js"))},
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:${port}" },
  webServer: {
    command: ${JSON.stringify(`python3 -m http.server ${port} --directory ${documentRoot}`)},
    url: "http://127.0.0.1:${port}",
    reuseExistingServer: false
  }
});
`);

  try {
    const result = spawnSync(
      path.join(root, "node_modules", ".bin", "playwright"),
      ["test", "--config", configPath],
      {
        cwd: root,
        encoding: "utf8",
        env: {
          ...process.env,
          PWTEST_CACHE_DIR: path.join(cacheRoot, "playwright-transform-cache"),
          TMPDIR: path.join(cacheRoot, "tmp")
        }
      }
    );
    const output = `${result.stdout}\n${result.stderr}`;
    if (result.status === 0) {
      throw new Error(`${name}: foreign-root run unexpectedly passed`);
    }
    if (!output.includes(expectedMessage)) {
      throw new Error(`${name}: expected ${JSON.stringify(expectedMessage)} in output\n${output}`);
    }
    if (fs.existsSync(sentinel)) {
      throw new Error(`${name}: test body executed before global-setup rejection`);
    }
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

fs.mkdirSync(path.join(cacheRoot, "tmp"), { recursive: true });
await runCase("mismatch", "not-tendi-site", "Site root marker mismatch");
await runCase("missing", null, "Site root marker missing");
console.log("Server-integrity regression passed: mismatched and missing markers failed in global setup before any test body executed.");
