# Tendi Launch Site

Static GitHub Pages site for [tendijournal.app](https://tendijournal.app), the public support, privacy, and launch page for Tendi.

## Site Role

The site should communicate the current Tendi product:

- Check in with one mood. Your entries build a record, and Tendi is honest about what that record can actually show.
- Quick check-ins, practical journal history, Month Map, Year Map, Herbarium, and careful insights.
- Local-first privacy, no account requirement, optional iCloud sync, optional analytics, and no journal-content server access.
- Support and privacy information for App Store review and users.

Do not reintroduce retired Garden/Mind Garden positioning. The current visual archive is Month Map plus Journal-owned Herbarium.

## App Store Link

The live App Store URL is not currently stored in this repository. Once Apple's listing is available:

1. Replace the homepage release note with the official App Store badge and link.
2. Use Apple's official badge artwork and marketing guidelines.
3. Re-check the link on desktop and mobile before publishing.

## Local Development

Preview with a static server:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`.

## Verification

Install test dependencies and Playwright's Chromium browser once:

```bash
npm ci
npm run install:browsers
```

Run the full local gate before handoff:

```bash
npm test
```

This runs static metadata/link/content checks plus Playwright browser rendering and axe accessibility smoke tests. Screenshots are written to `test-results/screenshots/` for desktop `1280x900` and mobile `390x844` review.

## Launch QA Notes

- Pages: `index.html`, `privacy.html`, `support.html`
- Cloudflare security hardening: follow `cloudflare-security.md` before launch or after DNS/security changes.
- Metadata: canonical URLs, Open Graph, Twitter preview cards, theme color, PNG favicon, and social preview image are defined.
- Accessibility: semantic sections, skip link, visible keyboard focus, responsive navigation, and minimum touch target sizing are included.
- Privacy: public policy is aligned with the local-first app posture, optional iCloud sync, HealthKit, Journaling Suggestions, optional TelemetryDeck analytics, user-initiated Find a Helpline crisis resource links, app/support and privacy/security contact routing, Fider public-feedback processing, Cloudflare hosting/security processing, Buttondown launch-update email processing, the outbound Buy Me a Coffee link, and no journal-content server access.
- Verification should include desktop and mobile rendering, link checks, and social card inspection.

## Manual Asset Pipeline

The site stays plain static HTML/CSS; these are one-off asset generation steps, not a build system.

### Fraunces

- Source: `https://raw.githubusercontent.com/google/fonts/main/ofl/fraunces/Fraunces%5BSOFT%2CWONK%2Copsz%2Cwght%5D.ttf`
- License: `https://raw.githubusercontent.com/google/fonts/main/ofl/fraunces/OFL.txt`, copied to `assets/fonts/OFL.txt`
- Output: `assets/fonts/Fraunces-opsz-wght-latin.woff2`

```bash
curl -L 'https://raw.githubusercontent.com/google/fonts/main/ofl/fraunces/Fraunces%5BSOFT%2CWONK%2Copsz%2Cwght%5D.ttf' -o /private/tmp/Fraunces-variable.ttf
curl -L 'https://raw.githubusercontent.com/google/fonts/main/ofl/fraunces/OFL.txt' -o assets/fonts/OFL.txt
pyftsubset /private/tmp/Fraunces-variable.ttf \
  --output-file=assets/fonts/Fraunces-opsz-wght-latin.woff2 \
  --flavor=woff2 \
  --layout-features='*' \
  --unicodes='U+000D,U+0020-007E,U+00A0-00FF,U+2010-2015,U+2018-201D,U+2022,U+2026,U+2192,U+2212'
```

### Social Card

Regenerate `social-card.png` from `social-card.svg` with the repository's pinned Playwright Chromium:

```bash
npm run card
```

The renderer owns its local server, waits for the bundled Fraunces font and raster assets, asserts a `1200x630` PNG, and prints the PNG digest, Chromium revision, and browser version recorded in `site.config.json`. The digest is a change-detection control for the pinned browser and font, not a cryptographic reproducibility claim: a Playwright browser upgrade can change antialiasing, so re-render and re-pin the digest and browser identity together in the same commit.

### Screenshot WebP Variants

The PNG screenshots remain as `<picture>` fallbacks. Current source PNGs are 424 x 920 simulator captures.

The current PNGs predate `tendi#88`; a refresh from the app repository's `visual` lane is pending.

```bash
cwebp -quiet -q 82 assets/screenshot-home.png -o assets/screenshot-home.webp
cwebp -quiet -q 82 assets/screenshot-checkin.png -o assets/screenshot-checkin.webp
cwebp -quiet -q 82 assets/screenshot-entry-detail.png -o assets/screenshot-entry-detail.webp
cwebp -quiet -q 82 assets/screenshot-insights.png -o assets/screenshot-insights.webp
```
