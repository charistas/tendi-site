# Tendi Launch Site

Static GitHub Pages site for [tendijournal.app](https://tendijournal.app), the public support, privacy, and launch page for Tendi.

## Site Role

The site should communicate the current Tendi product:

- Mood journaling without streaks, guilt, or missed-day pressure.
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

## Launch QA Notes

- Pages: `index.html`, `privacy.html`
- Cloudflare security hardening: follow `cloudflare-security.md` before launch or after DNS/security changes.
- Metadata: canonical URLs, Open Graph, Twitter preview cards, theme color, PNG favicon, and social preview image are defined.
- Accessibility: semantic sections, skip link, visible keyboard focus, responsive navigation, and minimum touch target sizing are included.
- Privacy: public policy is aligned with the local-first app posture, optional iCloud sync, HealthKit, optional TelemetryDeck analytics, user-initiated Find a Helpline crisis resource links, support email, Cloudflare hosting/security processing, Buttondown launch-update email processing, and no journal-content server access.
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

### Screenshot WebP Variants

The PNG screenshots remain as `<picture>` fallbacks. Current source PNGs are 424 x 920 simulator captures.

```bash
cwebp -quiet -q 82 assets/screenshot-home.png -o assets/screenshot-home.webp
cwebp -quiet -q 82 assets/screenshot-checkin.png -o assets/screenshot-checkin.webp
cwebp -quiet -q 82 assets/screenshot-entry-detail.png -o assets/screenshot-entry-detail.webp
cwebp -quiet -q 82 assets/screenshot-insights.png -o assets/screenshot-insights.webp
```
