import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const marker = "tendi-site";
const outputPath = path.join(root, "social-card.png");
const contentTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".woff2", "font/woff2"]
]);
const requiredRasterResources = [
  "/assets/app-icon.png",
  "/assets/screenshot-home.png",
  "/assets/screenshot-insights.png"
];
const requiredResources = [
  ...requiredRasterResources,
  "/assets/fonts/Fraunces-opsz-wght-latin.woff2"
];

function send(response, status, contentType, body) {
  response.writeHead(status, {
    "Cache-Control": "no-store",
    "Content-Type": contentType
  });
  response.end(body);
}

const svg = await fs.readFile(path.join(root, "social-card.svg"), "utf8");
const wrapper = `<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {
  font-family: "Fraunces";
  src: url("/assets/fonts/Fraunces-opsz-wght-latin.woff2") format("woff2");
  font-style: normal;
  font-weight: 100 900;
  font-display: block;
}
html, body { margin: 0; width: 1200px; height: 630px; overflow: hidden; }
svg { display: block; width: 1200px; height: 630px; }
</style></head><body>${svg}</body></html>`;

const server = http.createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, "http://127.0.0.1").pathname;
    if (pathname === "/__social_card_renderer.html") {
      send(response, 200, "text/html; charset=utf-8", wrapper);
      return;
    }
    if (pathname === "/.site-root-marker") {
      send(response, 200, "text/plain; charset=utf-8", `${marker}\n`);
      return;
    }
    const candidate = path.resolve(root, `.${decodeURIComponent(pathname)}`);
    if (!candidate.startsWith(`${root}${path.sep}`)) {
      send(response, 403, "text/plain; charset=utf-8", "Forbidden");
      return;
    }
    const body = await fs.readFile(candidate);
    send(response, 200, contentTypes.get(path.extname(candidate)) || "application/octet-stream", body);
  } catch (error) {
    if (error?.code === "ENOENT") {
      send(response, 404, "text/plain; charset=utf-8", "Not found");
      return;
    }
    send(response, 500, "text/plain; charset=utf-8", "Internal server error");
  }
});

let browser;
try {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("renderer server did not expose a TCP port");
  }
  const baseURL = `http://127.0.0.1:${address.port}`;
  const markerResponse = await fetch(`${baseURL}/.site-root-marker`, { cache: "no-store" });
  if (!markerResponse.ok || (await markerResponse.text()).trim() !== marker) {
    throw new Error("renderer server root marker mismatch");
  }

  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
  const responseStatuses = new Map(requiredResources.map((resource) => [resource, []]));
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    if (responseStatuses.has(pathname)) {
      responseStatuses.get(pathname).push(response.status());
    }
  });
  await page.goto(`${baseURL}/__social_card_renderer.html`, { waitUntil: "networkidle" });
  await page.evaluate(async (rasterResources) => {
    await document.fonts.ready;
    const requiredFontQueries = [
      '700 74px "Fraunces"',
      '700 42px "Fraunces"',
      '800 24px "Fraunces"',
      '400 22px "Fraunces"',
      '800 26px "Fraunces"'
    ];
    if (requiredFontQueries.some((query) => !document.fonts.check(query))) {
      throw new Error("Fraunces did not load for every card weight and size");
    }
    const embeddedRasters = new Set(
      [...document.querySelectorAll("svg image[href]")]
        .map((image) => new URL(image.getAttribute("href"), document.baseURI).pathname)
    );
    for (const resource of rasterResources) {
      if (!embeddedRasters.has(resource)) {
        throw new Error(`required raster is not embedded in the SVG: ${resource}`);
      }
      const response = await fetch(resource, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`required raster returned HTTP ${response.status}: ${resource}`);
      }
      const bitmap = await createImageBitmap(await response.blob());
      if (bitmap.width < 1 || bitmap.height < 1) {
        bitmap.close();
        throw new Error(`required raster decoded without dimensions: ${resource}`);
      }
      bitmap.close();
    }
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, requiredRasterResources);

  const resources = await page.evaluate(() => (
    performance.getEntriesByType("resource").map((entry) => ({
      pathname: new URL(entry.name).pathname,
      decodedBodySize: entry.decodedBodySize
    }))
  ));
  for (const resource of requiredResources) {
    const statuses = responseStatuses.get(resource);
    if (!statuses.includes(200)) {
      throw new Error(`required renderer resource did not return HTTP 200: ${resource}; statuses=${statuses.join(",")}`);
    }
    const loaded = resources.some((entry) => entry.pathname === resource && entry.decodedBodySize > 0);
    if (!loaded) {
      throw new Error(`required renderer resource did not complete with a non-zero decoded body: ${resource}`);
    }
  }

  await page.screenshot({
    path: outputPath,
    animations: "disabled",
    clip: { x: 0, y: 0, width: 1200, height: 630 }
  });
  const png = await fs.readFile(outputPath);
  if (png.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a") {
    throw new Error("renderer output is not a PNG");
  }
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  if (width !== 1200 || height !== 630) {
    throw new Error(`renderer output is ${width}x${height}, expected 1200x630`);
  }

  const executable = chromium.executablePath();
  const revision = executable.match(/chromium-(\d+)/)?.[1];
  if (!revision) {
    throw new Error(`could not read Chromium revision from ${executable}`);
  }
  const digest = await crypto.subtle.digest("SHA-256", png);
  const sha256 = Buffer.from(digest).toString("hex");
  process.stdout.write(`social-card.png sha256=${sha256} chromiumRevision=${revision} browserVersion=${browser.version()}\n`);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
