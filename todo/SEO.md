# SEO plan: make Specodex the answer when an engineer searches a part number

Status: 🚧 in progress. Phase 0 metadata foundation shipped 2026-04-28
(robots.txt, static sitemap, OG/Twitter cards, JSON-LD `WebSite` +
`Organization`, canonical URL). The structural lifts — SPA crawlability,
per-product titles/meta, dynamic sitemap, category/manufacturer/comparison
pages, OG image generator — remain. The product *is* the SEO asset; every
product row in DynamoDB is a long-tail landing page waiting to be rendered.

This doc is the **how**. Audience and channels live in
[MARKETING.md](MARKETING.md); the two are paired.

## What's shipped (2026-04-28)

Landed in `app/frontend/`:

- `public/robots.txt` — explicit allow + disallow `/admin`, `/api/`,
  sitemap reference.
- `public/sitemap.xml` — minimal static sitemap (homepage + `/welcome`).
  Per-product entries are deferred (see Phase 1b below).
- `index.html` — added canonical link, Open Graph tags (with 1200×630 OG
  image placeholder at `/og-default.png`), Twitter card tags, JSON-LD
  `WebSite` (with `SearchAction`) + `Organization`, refined meta
  description, `robots: index,follow`. Title shifted from "Product
  selection" to "Cross-vendor industrial spec database" — better
  query coverage on the head term. Both static files are served by
  the existing CloudFront S3 default behavior — no infra change.

**Open follow-ups from this batch:**

- `og-default.png` does not exist yet. Until it's generated, link
  unfurls fall back to text. Trivial (1200×630 PNG dropped into
  `public/`); blocked only on a design pass — see Phase 2e for the
  long-term per-product OG generator.
- Canonical URL is `https://datasheets.advin.io/`. Flip to
  `https://specodex.com/` when [REBRAND.md](REBRAND.md) Phase 4c
  (DNS cutover) lands. Affects `index.html` (3 places: canonical,
  og:url, JSON-LD `url`/`@id`/`urlTemplate`), `public/robots.txt`
  (sitemap line), `public/sitemap.xml` (every `<loc>`).

## The thesis

When a mechatronics engineer searches `mitsubishi mr-j5-200a4 specs` or
`yaskawa sgm7j-04afa61 datasheet`, the first 10 results today are some
mix of:

1. The manufacturer's own product page (often gated, often slow).
2. A PDF host (`docs.rs-online.com`, `automation24.com`, etc.) — a raw
   datasheet, no filtering.
3. Distributor stocking pages (Digi-Key, Mouser, AutomationDirect) —
   buy-now pages, minimal spec rendering.
4. Forum threads with someone asking a specific question.

None of these solve the engineer's actual job, which is *cross-vendor
spec comparison*. Specodex's structural advantage: one indexed,
canonical, fast-loading, schema-marked-up page per product, with cross-
links to comparable products. If we get the technical SEO right, we
should be able to rank in the first page for tens of thousands of
specific part-number queries within 6-12 months — not by being clever
on copy, but by being the only result that's actually structured.

## Current SEO state — baseline audit

What exists at `<https://datasheets.advin.io>` (and at `<https://specodex.com>`
once DNS cuts over per [REBRAND.md](REBRAND.md)):

| Item | State | Remaining action |
|---|---|---|
| `robots.txt` | ✅ shipped 2026-04-28 — `app/frontend/public/robots.txt` | Verify in prod; flip canonical URL post-DNS-cutover. |
| `sitemap.xml` (static, homepage only) | ✅ shipped 2026-04-28 | — |
| `sitemap.xml` (per-product, dynamic) | ❌ missing | Auto-generate from DynamoDB on every deploy (Phase 1b). |
| `<title>`, `<meta description>` (homepage) | ✅ shipped 2026-04-28 | — |
| `<title>`, `<meta description>` (per-product) | ❌ static (SPA) — same `<title>` on every route | Per-product values via prerender or SSR (Phase 1a/1d). |
| Open Graph / Twitter cards (homepage) | ✅ shipped 2026-04-28 | Generate `og-default.png` (1200×630). |
| Open Graph / Twitter cards (per-product) | ❌ missing | Per-product OG tags + per-product OG image (Phase 1d, 2e). |
| Schema.org structured data (`WebSite` + `Organization`) | ✅ shipped 2026-04-28 | — |
| Schema.org structured data (`Product` per page) | ❌ missing | Inject `Product` JSON-LD per product page (Phase 1d). |
| Canonical URL (homepage) | ✅ shipped 2026-04-28 | Flip to `specodex.com` post-cutover. |
| Canonical URLs (per-product) | ❌ missing | Set canonical to the `/products/{type}/{slug}` route (Phase 1e). |
| Crawlability of SPA routes | ❌ poor — Vite SPA renders client-side | **The big one.** Prerender or SSR all product pages (Phase 1a). |
| Internal linking | ❌ none — no link graph between products | Comparison links, "similar products", category indexes (Phase 2). |
| Page speed (LCP, INP) | ❌ unmeasured | Lighthouse CI in `verify` step (Phase 1f). |
| HTTPS, HTTP/2, compression | ✅ via CloudFront | — |
| Mobile-friendly | ✅ per `app/frontend` responsive | — |
| GitHub Pages `docs/` | ✅ live, has the marketing landing | Add blog and crosslinks (Phase 2d). |

The three existentials (SPA crawlability, dynamic sitemap, per-product
structured data) are the unfinished foundation; the homepage now passes
a basic Lighthouse SEO audit but the long-tail catalog pages don't yet
exist as crawlable URLs.

## Phase 1 — technical foundation (must ship before any marketing push)

### 1a. SPA crawlability — pick one of three

The frontend is React + Vite, deployed to S3 + CloudFront. Out of the box,
Googlebot will execute JS and *might* see the product pages, but it's slow,
unreliable, and other crawlers (Bing, DuckDuckGo, social card scrapers,
LLM crawlers) won't. Three options, ranked by fit:

- **Option A — Build-time prerender via `vite-plugin-prerender` or
  similar.** At `vite build`, hit the `/api/products` endpoint, generate
  one static `.html` per product with the right `<title>`, `<meta>`, and
  JSON-LD baked in, write them to S3 with the SPA shell as fallback for
  unknown routes. **Recommended.** Fits the existing CDK frontend stack
  with zero infra changes. Cost: build time grows with product count;
  at 100k products, expect a 10-15 minute build. Mitigation: only
  rebuild changed pages (incremental).
- **Option B — Edge SSR via Lambda@Edge.** CloudFront origin-request
  triggers a Lambda that renders the page server-side. More moving parts;
  Lambda@Edge regional cold starts are real. Skip unless A doesn't scale.
- **Option C — Prerender.io / Rendertron.** Detect crawler User-Agent
  at the edge, route to a headless-Chrome-as-a-service. Cloaking-adjacent
  (Google says it's fine, but it's brittle). Skip.

**Decision: Option A.** Stub `vite-plugin-prerender` config in
`app/frontend/vite.config.ts`, drive product list from a build-time
fetch against the staging API. Ship behind a `--prerender` flag in
`./Quickstart deploy` first; once green for a week on staging, make
it default.

### 1b. Sitemap — dynamic, per-product

A static placeholder ships at `app/frontend/public/sitemap.xml`
(homepage + `/welcome` only). Replace it with a deploy-time generator:

- New `cli/sitemap.py` module: scans DynamoDB, emits one `<url>`
  per product at `/products/{type}/{slug}`, plus static routes
  (`/`, `/welcome`, `/about`, category pages).
- `<lastmod>` populated from `updated_at` (fall back to
  `created_at`). `<changefreq>weekly</changefreq>` on category
  pages, `<changefreq>monthly</changefreq>` on product pages.
  **Don't lie about changefreq** — Google ignores it but other
  crawlers trust it.
- Switch to `sitemap-index.xml` when products exceed 50k URLs.
- Wired into `./Quickstart deploy`: write to S3 root with a 1-day
  cache, replacing the static placeholder.
- Submit to Search Console + Bing Webmaster after first prod deploy.

### 1c. `robots.txt`

Shipped 2026-04-28 at `app/frontend/public/robots.txt`. Vite copies it
to `dist/`; CloudFront's S3 default behavior serves it from the bucket
root. No CDK change was needed.

Open: flip the `Sitemap:` line from `datasheets.advin.io` to
`specodex.com` when REBRAND Phase 4c lands.

### 1d. Per-product `<title>`, `<meta>`, JSON-LD

Homepage `<title>`, `<meta description>`, OG/Twitter cards, and a
site-level JSON-LD `WebSite` + `Organization` graph shipped 2026-04-28
in `app/frontend/index.html`. The hard part — per-product values —
still needs Phase 1a's prerender/SSR before it can land. The formulas
below define what each prerendered page should emit.

Title formula:

```
{Manufacturer} {Part Number} — {Type} {one key spec} | Specodex
```

Examples:
- `Mitsubishi MR-J5-200A4 — Drive 2 kW 200 V | Specodex`
- `Yaskawa SGM7J-04AFA61 — Motor 400 W servo with brake | Specodex`

Length budget: 60 chars target, 70 hard cap. Truncate mid-spec rather
than truncating the part number — engineers search by part number.

Meta description formula (155 char target):

```
{Type} from {Manufacturer}, part {Part Number}. {2-3 key specs}. View full
spec table, datasheet, and cross-vendor alternatives on Specodex.
```

JSON-LD per product page (`<script type="application/ld+json">`),
matching `schema.org/Product`:

```json
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "Mitsubishi MR-J5-200A4",
  "manufacturer": { "@type": "Organization", "name": "Mitsubishi Electric" },
  "category": "Servo Drive",
  "mpn": "MR-J5-200A4",
  "description": "...",
  "additionalProperty": [
    { "@type": "PropertyValue", "name": "Rated Power", "value": "2", "unitCode": "KWT" },
    { "@type": "PropertyValue", "name": "Input Voltage", "value": "200", "unitCode": "VLT" }
  ],
  "url": "https://specodex.com/products/drive/mitsubishi-mr-j5-200a4"
}
```

Map Specodex's `ValueUnit` / `MinMaxUnit` shapes (see [UNITS.md](UNITS.md))
into `additionalProperty` entries. Use UN/CEFACT unit codes (`KWT`, `MTR`,
`HUR`, `NEW`, etc.) where they exist; fall back to a custom `unitText`
when not. **Watch out:** Schema.org `unitCode` expects UN/CEFACT, not
SI strings — this is the most common mistake in this kind of markup.

Open Graph + Twitter card tags on every product page so Slack /
Discord / Twitter / LinkedIn link unfurls don't look broken. OG image:
generated at build time from a per-type template (see "OG image
generator" below).

### 1e. Canonical URLs

Every product has exactly one canonical URL: `/products/{type}/{slug}`,
where `slug` is `{manufacturer}-{part_number}` lowercased and
hyphenated. Self-canonical on the canonical URL; `<link rel="canonical">`
on any alias (e.g. if a product is reachable via the search filter URL).

### 1f. Lighthouse CI in `verify`

Add a Lighthouse audit step to `./Quickstart verify`, gated at:

- LCP < 2.5s
- INP < 200ms
- CLS < 0.1
- SEO score > 95

Fail the build if regressions exceed thresholds. The frontend bundle
is small enough that hitting these on CloudFront is realistic; the
risk is JSON-LD payload bloat on product pages with hundreds of
fields. Measure before tuning.

## Phase 2 — content scaffolding (concurrent with phase 1)

### 2a. Category index pages

`/products/motor`, `/products/drive`, etc. Each one renders:

- H1 with a one-sentence definition of the product type.
- A filter UI snapshot (top 50 by `rated_power` or whatever the type's
  natural sort is).
- Links to all sub-categories (e.g. servo motor / stepper motor / DC
  motor under `/products/motor`).
- Internal links to the top 10 manufacturers, each a `/products/motor?manufacturer=X`
  page.

Each category page is its own indexable URL, with its own title /
meta / H1. Category pages are typically the highest-traffic SEO
pages on this kind of site — the long-tail product pages get visits
from part-number searches, but category pages catch "best servo
motors", "industrial drive comparison", and similar broad queries.

### 2b. Manufacturer index pages

`/manufacturer/{slug}` — one per vendor we've ingested. Renders all of
that vendor's products grouped by type, with a one-paragraph intro
("Mitsubishi Electric manufactures industrial servos under the MR-J
family..."). Auto-generate the intro from a template + the ingested
data; mark up with `Organization` schema.

These pages are SEO gold for vendor-specific searches that don't include
a part number ("yaskawa sigma servo motors", "abb acs880 family").

### 2c. Comparison pages — programmatic

`/compare/{type}/{manufacturer-a}-vs-{manufacturer-b}`, e.g.
`/compare/drive/yaskawa-vs-mitsubishi`. Side-by-side spec table for
the top N products from each vendor, with H2s for each spec category.

There are O(vendor² × type) of these; cap to the top vendor pairs per
type so we don't generate 100k empty comparison pages with thin
content (Google penalizes that). Heuristic: only generate the comparison
page if both vendors have ≥ 5 products of that type.

### 2d. Engineering blog at `docs/blog/`

GitHub Pages already serves `docs/`. Extend with `docs/blog/`,
Jekyll-driven. Three foundational posts (also listed in
[MARKETING.md](MARKETING.md)):

- **Page-finder benchmark write-up** — technical, links to repo.
- **Cross-vendor servo motor benchmark** — pure data, ranks 5-10
  vendors by spec dimensions.
- **Building a 3-axis motion stage** — application walkthrough.

Cross-link from the blog into the catalog (every product mention is a
link to its Specodex page) and from the catalog footer into the blog.
The link graph between `docs/blog/` and `app.specodex.com/products/...`
is the second-biggest SEO lever after prerendering.

### 2e. OG image generator

A `cli/og-image.py` (or Node equivalent in `app/frontend/scripts/`)
that renders a 1200×630 PNG per product at build time. Field-manual
aesthetic — paper background, OD-green header bar, part number in
condensed sans, key specs in tabular monospace. Stored in S3 at
`/og/{slug}.png`.

Without these, Slack/LinkedIn previews show a generic favicon and the
URL — high friction for engineer-to-engineer sharing, which is the
primary distribution channel per [MARKETING.md](MARKETING.md).

## Phase 3 — keyword strategy

Layered by intent and volume. Don't chase head terms; the long tail
adds up.

### Tier 1 — exact part numbers (highest intent, lowest volume each)

- Pattern: `{manufacturer} {part_number}` and `{part_number} datasheet`
  and `{part_number} specs`.
- Volume per query: 10-1,000/month each.
- Volume in aggregate: massive — the catalog already has thousands of
  parts.
- Strategy: prerendered product page with structured data wins these
  as long as it's indexed. No additional work needed beyond Phase 1.
- Competition: vendor pages (slow, gated), distributor pages (transactional),
  PDF mirrors (no UX). Specodex wins on UX once indexed.

### Tier 2 — manufacturer + family (medium volume, high intent)

- Pattern: `mitsubishi mr-j5`, `yaskawa sigma-7`, `abb acs880`, `siemens
  sinamics s120`.
- Volume: 1k-10k/month each.
- Strategy: manufacturer + category index pages (Phase 2a/2b). H1 must
  contain the family name; URL slug must too.

### Tier 3 — spec-driven category searches (medium volume, medium intent)

- Pattern: `2kw servo motor 200v`, `industrial drive ip65 fieldbus
  ethercat`, `gearhead ratio 100:1 backlash`.
- Volume: 100-1k/month each.
- Strategy: filtered category pages where every filter combination is
  a canonical URL with its own title and H1 (`/products/motor?rated_power_min=2000&rated_voltage=200`
  → also reachable as `/products/motor/2kw-200v`). **Dangerous if not
  capped** — could generate millions of thin URLs. Cap to 200-500
  pre-curated filter pages chosen by search-volume data.

### Tier 4 — broad informational (high volume, low intent)

- Pattern: `what is a servo drive`, `how to choose a gearhead`,
  `motor torque vs speed curve`.
- Volume: 5k-50k/month each.
- Strategy: blog content (Phase 2d). Specodex isn't going to outrank
  Wikipedia / vendor whitepapers on these — but a well-written post
  with embedded interactive search ("here's a live filter for what we
  just discussed") earns its placement.

### Don't pursue

- Generic head terms (`industrial motor`, `electric drive`). Volume
  is real but intent is wrong — those searchers want a Wikipedia
  article, not a spec database.
- Brand keywords for major distributors (`mouser`, `digikey`,
  `automationdirect`). Trademark issues, no upside.
- Geo-targeted variants (`servo motor canada`, `drive supplier
  germany`). Specodex isn't a distributor; these searchers want a
  buy page, not a comparison.

## Phase 4 — backlinks

Don't buy links. Earn them.

| Source type | Approach | Expected lift |
|---|---|---|
| Open-source repo on GitHub | Submit to `awesome-industrial`, `awesome-robotics`, `awesome-mechatronics`, `awesome-engineering`. Each merged PR is a high-DA link. | 10-30 referring domains over 6 months |
| Hacker News | Show HN (per [MARKETING.md](MARKETING.md) Phase 1) | 1 HN link, plus ~20-50 secondary blog mentions if it ranks |
| Engineering blogs | Cross-link from individual engineers' blogs after they discover the tool. Largely passive but accelerated by shipping useful content. | Slow, compounds |
| Forum threads | Eng-Tips, ControlBooth, r/PLC. These are mostly nofollow but they drive referral traffic and signal real-user usage. | High referral, low SEO direct |
| Wikipedia | Specodex won't have its own page; that's fine. But the *external links* sections of pages on individual products / families occasionally accept a high-quality reference. Don't add yourself; let it happen. | Slow, occasional |
| Trade press | Per [MARKETING.md](MARKETING.md) Phase 2. One placement is worth ~5 referring domains in the trade-press citation graph. | 5-15 referring domains |
| University / academic | Mechatronics professors who reference the tool in coursework or projects. | Slow, very high DA when it lands |

The unifying rule: links happen because the tool is useful, not because
we asked. Any "link-building" tactic that's separable from "make the
tool better" is a yellow flag.

## Phase 5 — measurement

Ship before any of the above is complete:

- **Google Search Console** verified on `specodex.com` (DNS TXT or HTML
  meta tag, do both for redundancy).
- **Bing Webmaster Tools** likewise.
- **GA4 or Plausible** for traffic. Plausible if we want to advertise
  "we don't track you" as a positioning element; GA4 if we want the
  free integration with Search Console. **Lean Plausible** — fits the
  field-manual / no-marketing-fluff vibe.
- **Lighthouse CI** results stored as artifacts of every CI run.
- **Search Console weekly export** to S3 — manual review for now.
  (`./Quickstart godmode` is scoped to catalog data quality, not
  search analytics.)

KPIs by month-6 (calibrate as we measure):

| Metric | Target |
|---|---|
| Indexed pages (Search Console) | > 80% of catalog |
| Average position for tier-1 (exact part number) queries | < 10 |
| Average position for tier-2 (manufacturer + family) queries | < 30 |
| Click-through rate, indexed pages | > 4% |
| Organic sessions / week | 5,000 |
| Referring domains (real, non-spam) | 50 |
| Core Web Vitals "Good" rate | > 90% of sessions |

## Phasing — gates

| Phase | Window | Gate to next |
|---|---|---|
| **0 — Audit.** ✅ done 2026-04-28. Static metadata baseline shipped: `robots.txt`, static homepage `sitemap.xml`, homepage `<title>`/`<meta>`, canonical, OG/Twitter cards, JSON-LD `WebSite` + `Organization`. | — | Phase 1 begins. |
| **1 — Foundation.** Prerender (1a), dynamic per-product sitemap (1b), per-product meta + JSON-LD (1d), per-product canonical (1e), Lighthouse CI (1f). Also: generate `og-default.png`. | 2-3 weeks. | Search Console shows ≥ 100 indexed product pages; Lighthouse SEO score > 95. |
| **2 — Content scaffolding.** Category, manufacturer, comparison pages. Blog scaffolding + first 3 posts. OG images. | 4-6 weeks. | Internal link graph has ≥ 5 inbound links per product page on average. |
| **3 — Amplify.** Coordinate with [MARKETING.md](MARKETING.md) launch. Search Console + Bing live. | Concurrent with marketing Phase 1. | First HN front-page; first organic position-1 ranking on a tier-1 query. |
| **4 — Iterate.** Monthly review of Search Console queries vs. coverage. Add filter-page slugs based on observed search demand. Re-measure Core Web Vitals on every deploy. | Ongoing. | — |

## Risks

- **Prerender + DynamoDB schema drift.** When [UNITS.md](UNITS.md)'s
  Phase 5 backfill runs, every prerendered page becomes stale. Solve:
  rebuild prerender as a deploy-time step that always pulls the latest
  data, never a checked-in artifact.
- **Thin / duplicate content from over-generation.** Comparison pages
  and filter-slug pages can balloon into millions of low-value URLs
  if not capped. Cap aggressively in Phase 2.
- **JSON-LD that doesn't validate.** Use Google's Rich Results Test on
  10 sample product pages before shipping; any structured-data error
  silently demotes the page.
- **Crawler budget.** Once the catalog grows past ~100k products,
  Googlebot may not crawl all of them. Solve via a tight sitemap,
  good internal linking, and high-priority-page hints in `<priority>`
  on the sitemap entries.
- **Robots.txt blocking the API in dev surfaces.** If staging gets a
  no-index header for safety and someone forgets to flip it on prod,
  the entire site disappears from Google. Solve: an explicit assertion
  in `./Quickstart smoke` that production HTML responses do *not* have
  `X-Robots-Tag: noindex` or a `<meta name="robots" content="noindex">`.

## Triggers

| Trigger (files / topics in your current task) | Surface |
|---|---|
| `app/frontend/vite.config.ts` build config; SSR / prerender / `vite-plugin-*` discussions | this doc (Phase 1a) |
| `app/infrastructure/lib/frontend-stack.ts` CloudFront behaviors; cache rules; S3 path mappings for `robots.txt`, `sitemap.xml`, `/og/` | this doc |
| `cli/sitemap.py` (does not exist yet — flag the deferred work) | this doc (Phase 1b) |
| `<title>`, `<meta>`, `<link rel=canonical>`, JSON-LD, `application/ld+json`, `schema.org` references in any frontend file | this doc (Phase 1d/1e) |
| `docs/blog/` creation, Jekyll config, GitHub Pages content beyond the landing page | this doc (Phase 2d) |
| User mentions "SEO", "ranking", "Google", "Search Console", "indexable", "sitemap", "structured data", "rich results", "OG image", "social card", "Lighthouse", "Core Web Vitals" | this doc |
| Schema for product pages — `additionalProperty`, `unitCode`, `Product` JSON-LD — interacts with `ValueUnit` shape | this doc + [UNITS.md](UNITS.md) |
| Prerender stale-data / rebuild-on-deploy concerns when the data shape changes | this doc + [UNITS.md](UNITS.md) |
| `cli/og-image.py` or any OG-image generation work | this doc (Phase 2e) |
| Comparison-page URL patterns, programmatic-page generation, filter-slug routes | this doc (Phase 2c, 3-tier3) |
