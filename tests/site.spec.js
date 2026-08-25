const fs = require("fs");
const crypto = require("crypto");
const path = require("path");
const { execFileSync } = require("child_process");
const { test, expect, chromium } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const site = require("../site.config.json");

const viewports = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 390, height: 844 }
];
const screenshotDir = path.join("test-results", "screenshots");
const captureFilenames = new Set();
const captureRecords = [];

function attributesFor(tag) {
  return Object.fromEntries([...tag.matchAll(/([:\w-]+)\s*=\s*"([^"]*)"/g)].map((match) => [match[1], match[2]]));
}

function sourceMetadata(pageName) {
  const source = fs.readFileSync(path.join(__dirname, "..", pageName), "utf8");
  const title = source.match(/<title>([^<]+)<\/title>/)?.[1];
  const metadata = { title };
  for (const tag of source.match(/<meta\s+[^>]+>/g) || []) {
    const attrs = attributesFor(tag);
    const key = attrs.name || attrs.property;
    if (key === "description" || key?.startsWith("og:") || key?.startsWith("twitter:")) {
      metadata[key] = attrs.content;
    }
  }
  for (const tag of source.match(/<link\s+[^>]+>/g) || []) {
    const attrs = attributesFor(tag);
    if (attrs.rel === "canonical") {
      metadata.canonical = attrs.href;
    }
  }
  return metadata;
}

function gitOutput(...args) {
  return execFileSync("git", args, { cwd: path.join(__dirname, ".."), encoding: "utf8" }).trim();
}

function requireStagedTreeMatchesWorktree() {
  const status = execFileSync(
    "git",
    ["status", "--porcelain=v1", "--untracked-files=all"],
    { cwd: path.join(__dirname, ".."), encoding: "utf8" }
  );
  const divergent = status
    .split("\n")
    .filter(Boolean)
    .filter((line) => line.startsWith("??") || line[1] !== " ");
  if (divergent.length > 0) {
    throw new Error(`capture provenance requires the worktree to match the staged tree:\n${divergent.join("\n")}`);
  }
}

function registerCaptureNames(filenames) {
  expect(new Set(filenames).size, "capture filenames must be unique within the page/viewport set").toBe(filenames.length);
  for (const filename of filenames) {
    expect(captureFilenames.has(filename), `capture filename collision: ${filename}`).toBe(false);
    captureFilenames.add(filename);
  }
}

function writeCaptureMetadata(browserVersion) {
  requireStagedTreeMatchesWorktree();
  const executable = chromium.executablePath();
  const metadata = {
    baseCommit: gitOutput("rev-parse", "HEAD"),
    treeHash: gitOutput("write-tree"),
    dirty: gitOutput("status", "--porcelain").length > 0,
    capturedAt: new Date().toISOString(),
    browser: {
      name: "chromium",
      version: browserVersion,
      revision: executable.match(/chromium-(\d+)/)?.[1],
      deviceScaleFactor: 1
    },
    prefersReducedMotion: "reduce",
    viewports,
    captures: captureRecords
  };
  fs.writeFileSync(path.join(screenshotDir, "capture-meta.json"), `${JSON.stringify(metadata, null, 2)}\n`);
}

async function discoverCaptureElements(page) {
  const elements = await page.locator("body > header, main > section, body > footer").evaluateAll((topLevelElements) => {
    const tagIndexes = new Map();
    const slug = (value) => value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 40);
    return topLevelElements.map((element, index) => {
      const tag = element.tagName.toLowerCase();
      const tagIndex = tagIndexes.get(tag) || 0;
      tagIndexes.set(tag, tagIndex + 1);
      const labelledBy = element.getAttribute("aria-labelledby");
      const heading = element.querySelector("h1,h2,h3");
      const label = element.id
        || (labelledBy && document.getElementById(labelledBy) ? labelledBy : "")
        || (heading ? slug(heading.textContent.trim()) : "")
        || `${tag}-${String(tagIndex).padStart(2, "0")}`;
      return { index, label, captureId: element.getAttribute("data-capture") };
    });
  });
  const missing = elements.filter(({ captureId }) => !captureId);
  if (missing.length > 0) {
    throw new Error(`top-level capture elements are missing data-capture: ${missing.map(({ label }) => label).join(", ")}`);
  }
  const captureIds = elements.map(({ captureId }) => captureId);
  const duplicates = captureIds.filter((captureId, index) => captureIds.indexOf(captureId) !== index);
  if (duplicates.length > 0) {
    throw new Error(`top-level data-capture values must be unique: ${[...new Set(duplicates)].join(", ")}`);
  }
  for (const { label, captureId } of elements) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(captureId)) {
      throw new Error(`data-capture must be a stable lowercase slug: ${captureId}`);
    }
    if (captureId !== label) {
      throw new Error(`data-capture ${captureId} must preserve filename label ${label}`);
    }
  }
  return elements.map((element) => ({
    index: element.index,
    label: element.label,
    selector: `[data-capture="${element.captureId}"]`
  }));
}

function routeFor(pageName) {
  return pageName === "index.html" ? "/" : `/${pageName}`;
}

function localRequestLabel(requestUrl) {
  const url = new URL(requestUrl);
  if (!["127.0.0.1", "localhost"].includes(url.hostname)) {
    return null;
  }

  return `${url.pathname}${url.search}`;
}

async function gotoWithConsoleChecks(page, pageName) {
  const errors = [];
  const failedResources = [];

  page.on("console", (message) => {
    if (message.type() === "error") {
      errors.push(message.text());
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    const label = localRequestLabel(response.url());
    if (label && response.status() >= 400) {
      failedResources.push(`${response.status()} ${label}`);
    }
  });
  page.on("requestfailed", (request) => {
    const label = localRequestLabel(request.url());
    if (label) {
      failedResources.push(`${label}: ${request.failure()?.errorText || "request failed"}`);
    }
  });

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(routeFor(pageName), { waitUntil: "networkidle" });
  expect(errors, `console/page errors on ${pageName}`).toEqual([]);
  expect(failedResources, `failed local resources on ${pageName}`).toEqual([]);
}

async function waitForPageImages(page) {
  await page.locator("img").evaluateAll(async (images) => {
    await Promise.all(images.map(async (image) => {
      if (!image.complete || image.naturalWidth === 0) {
        await image.decode();
      }
      if (image.naturalWidth === 0) {
        throw new Error(`image did not decode: ${image.currentSrc || image.src}`);
      }
    }));
  });
}

for (const pageName of site.pages) {
  for (const viewport of viewports) {
    test(`${pageName} renders at ${viewport.name}`, async ({ page, browser }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await gotoWithConsoleChecks(page, pageName);
      await waitForPageImages(page);

      await expect(page.locator("body")).toBeVisible();
      const overflow = await page.evaluate(() => (
        document.documentElement.scrollWidth - document.documentElement.clientWidth
      ));
      expect(overflow, `${pageName} should not horizontally overflow at ${viewport.name}`).toBeLessThanOrEqual(1);

      fs.mkdirSync(screenshotDir, { recursive: true });
      const prefix = `${pageName.replace(/\.html$/, "")}-${viewport.name}`;
      const screenshotPath = path.join(screenshotDir, `${prefix}--full-page.png`);
      const captureElements = await discoverCaptureElements(page);
      const scrollHeight = await page.evaluate(() => document.documentElement.scrollHeight);
      const step = viewport.height - 120;
      const maxScroll = Math.max(0, scrollHeight - viewport.height);
      const tileOffsets = [];
      for (let offset = 0; offset < maxScroll; offset += step) {
        tileOffsets.push(offset);
      }
      if (tileOffsets.at(-1) !== maxScroll) {
        tileOffsets.push(maxScroll);
      }
      const sectionFiles = captureElements.map(({ label }) => `${prefix}--${label}.png`);
      const tileFiles = tileOffsets.map((_, index) => `${prefix}--tile-${String(index).padStart(2, "0")}.png`);
      const captureFiles = [`${prefix}--full-page.png`, ...sectionFiles, ...tileFiles];
      registerCaptureNames(captureFiles);

      await page.screenshot({ path: screenshotPath, fullPage: true, animations: "disabled" });
      expect(fs.statSync(screenshotPath).size).toBeGreaterThan(20000);

      const selectorCaptureStyle = await page.addStyleTag({
        content: ".site-header { position: static !important; }"
      });
      for (const [elementIndex, element] of captureElements.entries()) {
        const filename = sectionFiles[elementIndex];
        const target = page.locator(element.selector);
        await expect(target, `${element.selector} must resolve once before capture`).toHaveCount(1);
        await target.screenshot({
          path: path.join(screenshotDir, filename),
          animations: "disabled"
        });
        captureRecords.push({ page: pageName, viewport: viewport.name, kind: "element", label: element.label, selector: element.selector, file: filename });
      }
      await selectorCaptureStyle.evaluate((style) => style.remove());

      for (const [tileIndex, offset] of tileOffsets.entries()) {
        await page.evaluate((scrollY) => window.scrollTo(0, scrollY), offset);
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const filename = tileFiles[tileIndex];
        await page.screenshot({ path: path.join(screenshotDir, filename), animations: "disabled" });
        captureRecords.push({ page: pageName, viewport: viewport.name, kind: "tile", scrollY: offset, file: filename });
      }
      captureRecords.push({ page: pageName, viewport: viewport.name, kind: "fullPage", file: path.basename(screenshotPath) });
      writeCaptureMetadata(browser.version());

      if (pageName === "index.html" && viewport.name === "mobile") {
        await page.evaluate(() => window.scrollTo(0, 0));
        const heroFit = await page.locator("#hero-title").evaluate((heading) => {
          const style = getComputedStyle(heading);
          const lineHeight = Number.parseFloat(style.lineHeight);
          const headingRect = heading.getBoundingClientRect();
          const ledeRect = document.querySelector(".hero-lede").getBoundingClientRect();
          const ledeLineHeight = Number.parseFloat(getComputedStyle(document.querySelector(".hero-lede")).lineHeight);
          const formRect = document.querySelector(".hero .waitlist-form").getBoundingClientRect();
          return {
            lines: Math.round(headingRect.height / lineHeight),
            headingBottom: headingRect.bottom,
            ledeFirstLineBottom: ledeRect.top + ledeLineHeight,
            formTop: formRect.top
          };
        });
        expect(heroFit.lines, "mobile hero heading must use at most four lines").toBeLessThanOrEqual(4);
        expect(heroFit.headingBottom, "mobile hero heading must fit in the first viewport").toBeLessThanOrEqual(viewport.height);
        expect(heroFit.ledeFirstLineBottom, "the first mobile lede line must fit in the first viewport").toBeLessThanOrEqual(viewport.height);
        expect(heroFit.formTop, "the hero form must be reachable within one short scroll").toBeLessThanOrEqual(viewport.height * 1.5);
      }
    });
  }

  test(`${pageName} has no automated WCAG A/AA violations`, async ({ page }) => {
    await gotoWithConsoleChecks(page, pageName);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();

    expect(results.violations).toEqual([]);
  });
}

test("capture selector contract rejects missing and duplicate attributes on every rendered page", async ({ page }) => {
  for (const pageName of site.pages) {
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await gotoWithConsoleChecks(page, pageName);
      const pristine = await discoverCaptureElements(page);
      expect(pristine.length).toBe(await page.locator("body > header, main > section, body > footer").count());

      await page.locator("body > header, main > section, body > footer").first().evaluate((element) => element.removeAttribute("data-capture"));
      await expect(discoverCaptureElements(page)).rejects.toThrow("missing data-capture");

      await gotoWithConsoleChecks(page, pageName);
      const topLevel = page.locator("body > header, main > section, body > footer");
      const firstCaptureId = await topLevel.first().getAttribute("data-capture");
      await topLevel.nth(1).evaluate((element, captureId) => element.setAttribute("data-capture", captureId), firstCaptureId);
      await expect(discoverCaptureElements(page)).rejects.toThrow("must be unique");
    }
  }
});

test("capture metadata selectors independently reproduce every element capture", async ({ page }) => {
  const metadata = JSON.parse(fs.readFileSync(path.join(screenshotDir, "capture-meta.json"), "utf8"));
  for (const pageName of site.pages) {
    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await gotoWithConsoleChecks(page, pageName);
      const records = metadata.captures.filter((capture) => (
        capture.kind === "element" && capture.page === pageName && capture.viewport === viewport.name
      ));
      const selectors = records.map(({ selector }) => selector);
      const labels = records.map(({ label }) => label);
      expect(new Set(selectors).size).toBe(selectors.length);
      expect(new Set(labels).size).toBe(labels.length);
      expect(records.length).toBe(await page.locator("body > header, main > section, body > footer").count());
      for (const record of records) {
        expect(record.selector).not.toBe(record.label);
        expect(record.file).toContain(`--${record.label}.png`);
        const target = page.locator(record.selector);
        await expect(target, `${record.selector} from capture-meta.json must reselect exactly once`).toHaveCount(1);
        expect(await target.evaluate((element) => element.matches("body > header, main > section, body > footer"))).toBe(true);
      }
    }
  }
});

test("rendered metadata matches the independent contract and source on every page", async ({ page }) => {
  for (const pageName of site.pages) {
    await gotoWithConsoleChecks(page, pageName);
    const rendered = await page.evaluate(() => {
      const values = { title: document.title };
      for (const meta of document.querySelectorAll("meta[name='description'], meta[property^='og:'], meta[name^='twitter:']")) {
        values[meta.getAttribute("name") || meta.getAttribute("property")] = meta.content;
      }
      values.canonical = document.querySelector("link[rel='canonical']")?.href;
      return values;
    });
    expect({
      title: rendered.title,
      description: rendered.description,
      canonical: rendered.canonical
    }).toEqual(site.metadataParity.canonicalByPage[pageName]);
    expect(rendered).toEqual(sourceMetadata(pageName));
  }
});

test("homepage states the public promise in order", async ({ page }) => {
  await gotoWithConsoleChecks(page, "index.html");
  const contract = site.promiseContract["index.html"];
  const headings = page.locator("h1");
  await expect(headings).toHaveCount(1);
  await expect(page.locator("h1,h2,h3").first()).toHaveAttribute("id", "hero-title");
  await expect(page.locator(contract.captureSelector)).toHaveText(contract.capture);
  const rendered = await page.evaluate(({ captureSelector, ledeSelector }) => {
    const capture = document.querySelector(captureSelector);
    const lede = document.querySelector(ledeSelector);
    return {
      capture: capture.textContent.trim().replace(/\s+/g, " "),
      lede: lede.textContent.trim().replace(/\s+/g, " "),
      followsCapture: Boolean(capture.compareDocumentPosition(lede) & Node.DOCUMENT_POSITION_FOLLOWING)
    };
  }, contract);
  expect(rendered.followsCapture).toBe(true);
  expect(rendered.lede.indexOf(contract.record)).toBeLessThan(rendered.lede.indexOf(contract.interpretation));
  expect(`${rendered.capture} ${rendered.lede}`).toBe(contract.promiseVerbatim);
  for (const supportContract of contract.supportContracts) {
    const target = page.locator(supportContract.selector);
    await expect(target, `${supportContract.name} selector must resolve once`).toHaveCount(1);
    for (const required of supportContract.mustContain) {
      await expect(target).toContainText(required);
    }
  }
});

test("the comparative-evidence claim keeps its condition", async ({ page }) => {
  await gotoWithConsoleChecks(page, "index.html");
  const contract = site.comparativeClaim["index.html"];
  const paragraph = page.locator(contract.selector);
  await expect(paragraph).toHaveCount(1);
  for (const required of contract.mustContain) {
    await expect(paragraph).toContainText(required);
  }
  const body = await page.locator("body").innerText();
  for (const prohibited of contract.mustNotContain) {
    expect(body.toLowerCase()).not.toContain(prohibited.toLowerCase());
  }
});

test("interpretation and voice stay below the fold", async ({ page }) => {
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await gotoWithConsoleChecks(page, "index.html");
    expect(await page.evaluate(() => window.scrollY)).toBe(0);
    const interpretation = page.locator(".feature-flow article.feature-row:nth-of-type(3) .feature-copy p:not(.flow-step)");
    await expect(interpretation).toHaveCount(1);
    const interpretationBox = await interpretation.boundingBox();
    expect(interpretationBox.y, `interpretation must be below the ${viewport.name} fold`).toBeGreaterThan(viewport.height);

    const voiceMentions = await page.locator("body").evaluate(() => {
      const matches = [];
      const seen = new Set();
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if (!/\b(?:voice|recordings?)\b/i.test(walker.currentNode.textContent)) {
          continue;
        }
        const element = walker.currentNode.parentElement;
        if (!element || seen.has(element)) {
          continue;
        }
        seen.add(element);
        matches.push({
          text: element.textContent.trim().replace(/\s+/g, " "),
          y: element.getBoundingClientRect().top + window.scrollY
        });
      }
      return matches;
    });
    expect(voiceMentions.length, "homepage must retain at least one below-fold voice mention").toBeGreaterThan(0);
    for (const mention of voiceMentions) {
      expect(mention.y, `${JSON.stringify(mention.text)} must be below the ${viewport.name} fold`).toBeGreaterThan(viewport.height);
    }
  }
});

test("desktop and mobile expose the same heading hierarchy", async ({ page }) => {
  const hierarchies = [];
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await gotoWithConsoleChecks(page, "index.html");
    hierarchies.push(await page.locator("h1,h2,h3").evaluateAll((elements) => (
      elements.map((element) => `${element.tagName}|${element.textContent.trim().replace(/\s+/g, " ")}`)
    )));
  }
  expect(hierarchies[1]).toEqual(hierarchies[0]);
});

test("no prohibited claim string is rendered", async ({ page }) => {
  for (const pageName of site.pages) {
    await gotoWithConsoleChecks(page, pageName);
    const body = (await page.locator("body").innerText()).toLowerCase();
    for (const prohibited of site.prohibitedPageText[pageName] || []) {
      expect(body).not.toContain(prohibited.toLowerCase());
    }
  }
});

test("keyboard focus reaches the skip link and both signup buttons with a visible indicator", async ({ page }) => {
  await gotoWithConsoleChecks(page, "index.html");
  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await expect(page.locator(".skip-link")).toBeVisible();

  const buttons = page.locator("button[type='submit']");
  await expect(buttons).toHaveCount(2);
  let reached = 0;
  for (let tab = 0; tab < 100 && reached < 2; tab += 1) {
    await page.keyboard.press("Tab");
    if (await buttons.nth(reached).evaluate((button) => document.activeElement === button)) {
      const outlineStyle = await buttons.nth(reached).evaluate((button) => getComputedStyle(button).outlineStyle);
      expect(outlineStyle).not.toBe("none");
      reached += 1;
    }
  }
  expect(reached).toBe(2);

  const firstFaq = page.locator("details").first();
  const firstSummary = firstFaq.locator("summary");
  await firstSummary.focus();
  await page.keyboard.press("Enter");
  await expect(firstFaq).toHaveAttribute("open", "");
  await page.keyboard.press("Space");
  await expect(firstFaq).not.toHaveAttribute("open", "");
});

test("email placeholders and Buttondown forms are wired", async ({ page }) => {
  for (const [pageName, emails] of Object.entries(site.requiredPageMailtoLinks)) {
    await gotoWithConsoleChecks(page, pageName);
    for (const email of emails) {
      await expect(page.locator(`a[href="mailto:${email}"]`).first()).toBeVisible();
    }
  }

  await gotoWithConsoleChecks(page, "index.html");
  for (const action of site.allowedFormActions) {
    await expect(page.locator(`form[action="${action}"]`)).toHaveCount(2);
  }
});

test("support page exposes working contact and policy links", async ({ page }) => {
  await gotoWithConsoleChecks(page, "support.html");

  await expect(page.locator('a[href="mailto:support@tendijournal.app"]').first()).toBeVisible();
  await expect(page.locator('a[href="privacy.html"]').first()).toBeVisible();
  const feedbackSection = page.locator(".content-section").filter({
    has: page.getByRole("heading", { name: "Send feedback or report a problem" })
  });
  await expect(feedbackSection.locator('a[href="https://feedback.tendijournal.app"]')).toBeVisible();
});

test("homepage navigation links to support", async ({ page }) => {
  await gotoWithConsoleChecks(page, "index.html");

  const primaryNavigation = page.getByRole("navigation", { name: "Primary" });
  await expect(primaryNavigation.getByRole("link", { name: "Support" })).toHaveAttribute("href", "support.html");
});

test("the social card was rendered by the pinned browser", async ({ browser }) => {
  test.skip(!site.socialCard, "socialCard provenance is not configured yet");
  const executable = chromium.executablePath();
  const revision = executable.match(/chromium-(\d+)/)?.[1];
  expect(revision).toBe(site.socialCard.chromiumRevision);
  expect(browser.version()).toBe(site.socialCard.browserVersion);

  const png = fs.readFileSync(path.join(__dirname, "..", "social-card.png"));
  expect(png.readUInt32BE(16)).toBe(site.socialCard.width);
  expect(png.readUInt32BE(20)).toBe(site.socialCard.height);
  expect(crypto.createHash("sha256").update(png).digest("hex")).toBe(site.socialCard.pngSha256);
});
