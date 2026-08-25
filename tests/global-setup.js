const fs = require("node:fs");
const path = require("node:path");
const site = require("../site.config.json");

module.exports = async function globalSetup(config) {
  const baseURL = config.projects[0]?.use?.baseURL
    || config.use?.baseURL
    || `http://127.0.0.1:${site.previewPort}`;
  const markerURL = new URL("/.site-root-marker", baseURL);
  let response;

  try {
    response = await fetch(markerURL);
  } catch (error) {
    throw new Error(`Site root marker request failed at ${markerURL}: ${error.message}`);
  }

  if (!response.ok) {
    throw new Error(`Site root marker missing at ${markerURL}: HTTP ${response.status}`);
  }

  const body = await response.text();
  if (body.trim() !== "tendi-site") {
    throw new Error(`Site root marker mismatch at ${markerURL}: expected tendi-site`);
  }

  const screenshotDir = path.join(__dirname, "..", "test-results", "screenshots");
  fs.rmSync(screenshotDir, { recursive: true, force: true });
  fs.mkdirSync(screenshotDir, { recursive: true });
};
