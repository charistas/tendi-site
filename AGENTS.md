# Tendi Site Guidance

## Project Snapshot

- Static GitHub Pages site for `https://tendijournal.app`.
- Purpose: public launch, support, privacy, and App Store review site for Tendi.
- Pages: `index.html`, `privacy.html`, `support.html`, `.well-known/security.txt`, `llms.txt`, `robots.txt`, `sitemap.xml`.
- Runtime code is plain HTML, CSS, and a small `assets/site.js` helper for email-link generation, current year text, and reveal animations.
- Deployment is GitHub Pages behind Cloudflare with custom domain `tendijournal.app`.

## Product Truth

- Tendi is a mood journal for iPhone and Apple Watch from Two Desks.
- The public promise is: Check in with one mood. Your entries build a record, and Tendi is honest about what that record can actually show.
- Its clause order is fixed as capture, then accumulated record, then honest interpretation; no surface may reorder it.
- Supporting product details include mood-first check-ins, practical journal history, Month Map, Year Map, Herbarium, optional iCloud sync, optional HealthKit, optional analytics, and no account requirement.
- The app code and canonical app docs live in `/Users/charistas/Dev/tendi`.
- Start with `/Users/charistas/Dev/tendi/docs/README.md` when site copy depends on current app behavior.

## Product And Privacy Constraints

- Do not reintroduce retired Bloomery, Classic Garden, Mind Garden, Past Gardens/Terrarium, Mossbear, or procedural Garden positioning.
- Current visual archive language is Month Map, Year Map, and Journal-owned Herbarium.
- Check-in language must avoid streak pressure, guilt, or punitive missed-day framing.
- Tendi is a self-reflection and journaling tool, not a medical, diagnostic, therapy, medication, or crisis-response product.
- The website launch forms submit to Buttondown. Journal content is never sent to Buttondown.
- The website source does not include client-side analytics scripts. Do not claim Cloudflare Web Analytics is active unless it is intentionally enabled and the privacy policy is updated.
- Optional app analytics are TelemetryDeck, off by default, and must not include journal text, notes, photos, voice recordings, HealthKit samples, precise location, email addresses, or advertising identifiers.

## App Store Link

The live App Store URL is not currently stored in this repo. Once Apple's listing is available:

1. Replace the homepage release note with the official App Store badge and link.
2. Use Apple's official badge artwork and marketing guidelines.
3. Re-check the link on desktop and mobile before publishing.

## Development

Use port `8000` for local preview:

```bash
python3 -m http.server 8000
```

Open `http://localhost:8000`.

## Verification

Install dependencies with `npm ci` after cloning or when `package-lock.json` changes.
Install Playwright's Chromium browser with `npm run install:browsers` before the first browser test run on a fresh machine.

Run the full local gate before handoff:

```bash
npm test
```

Useful focused checks:

```bash
npm run verify
npm run test:browser
```

The browser check starts a local static server, runs desktop `1280x900` and mobile `390x844` rendering checks, writes screenshots under `test-results/screenshots/`, and runs axe accessibility smoke tests. Review screenshots when layout, copy length, imagery, or CSS changes.

## Editing Rules

- Keep the site static; do not add React, Vite, Tailwind, or a bundler.
- Keep page metadata aligned across canonical URL, Open Graph, Twitter tags, `sitemap.xml`, `robots.txt`, and `llms.txt`.
- If public app behavior or privacy copy changes, cross-check `/Users/charistas/Dev/tendi/docs/04-technical/app-store-privacy.md` and the current source behavior.
- Keep `_config.yml` excluding agent, test, and tooling files from GitHub Pages while including `.well-known`; do not re-add `.nojekyll` unless Pages deployment moves off repository-root publishing.
- Keep `social-card.png` at `1200x630` when regenerating it.
- Keep Cloudflare notes in `cloudflare-security.md` current if DNS, headers, CSP, or Cloudflare injection behavior changes.

### Public Claim Ceiling

Before changing public positioning or product claims, read `/Users/charistas/Dev/tendi/docs/03-design/content-guidelines.md` §Public Positioning Copy. The site-specific enforced boundary is `site.config.json` `claimFamilies`; update public copy and its contract tests together, and treat any exemption as an explicit reviewed ledger entry.

## Cloudflare Gotchas

- The live site may include Cloudflare-injected security behavior that is not present locally.
- Do not add a strict CSP while Cloudflare is injecting or rewriting live HTML.
- After deploy, fetch the live domain and verify headers, Buttondown forms, contact links, privacy wording, and any Cloudflare-injected scripts.
