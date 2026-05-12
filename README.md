# Bloomery Landing Page

Pre-launch landing page for [bloomery.app](https://bloomery.app) — a mood journaling app built on a "no punishment" philosophy.

## About Bloomery

Bloomery is an iOS mood journal that welcomes you back without judgment. Unlike streak-obsessed apps that punish inconsistency, Bloomery celebrates every check-in as a small act of self-care.

**Coming soon to the App Store.**

## Local Development

Run a local server to preview:

```bash
npx serve .
```

Or:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000` (or the port shown).

## Deployment

This site is deployed via GitHub Pages with a custom domain (bloomery.app) through Cloudflare.

## Built By

[Two Desks](https://twodesks.app) — a two-person indie team.

## Launch QA Notes

- Pages: `index.html`, `privacy.html`
- Metadata: canonical URLs, Open Graph, Twitter preview cards, theme color, SVG favicon, and social preview image are defined.
- Accessibility: semantic sections, skip link, explicit email label, visible keyboard focus, and 44px minimum link targets are included.
- Privacy: website signup, Buttondown, Cloudflare analytics, and on-device app data claims are aligned between landing and privacy pages.
- Verified on May 12, 2026 with `python3 -m http.server` at desktop `1440x900` and mobile `390x844` widths.
