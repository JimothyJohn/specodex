# Specodex — public-facing facts

This file is a compact, deliberately-public statement of what Specodex
is, who runs it, what its data sources are, and what to do if you're
a manufacturer who'd like to either provide enhanced listings or
request takedown of yours. If you're a curious user, the [README](README.md)
has the engineering picture. If you're here for legal / compliance /
relationship reasons, you're in the right place.

---

## Who runs this

Specodex is built and operated by **Nick Armenta** (advin.io), as a
solo bootstrapped project. The deploy chain runs on AWS (CloudFront,
Lambda, DynamoDB, S3) and the source is open under MIT at
<https://github.com/JimothyJohn/specodex>.

**Contact:** `nick@advin.io`. Single inbox for press, manufacturer
relations, takedown requests, and partnership inquiries. Response
target is 5 business days; faster for security or copyright issues.

---

## Data sources and reproduction stance

Specodex's catalog is built from publicly-available manufacturer
datasheets (PDFs and product webpages). For each product row, the
source URL is preserved and visible to the end user — every cell
traces back to its original document.

What we reproduce:

- **Structured numeric specifications** — torque, voltage, dimensions,
  ratios, encoder protocols, etc. These are facts about products.
  Facts are not copyrightable in the U.S. (Feist Publications v.
  Rural Telephone Service, 499 U.S. 340) or in most jurisdictions
  Specodex serves. We reproduce them as part of a transformative,
  comparative database.
- **Product names, model numbers, manufacturer names.** Identifiers,
  used nominatively to refer to the products being compared.
- **Hyperlinks back to the source datasheet** on the manufacturer's
  own site, so the original document stays the canonical reference.

What we do **not** reproduce:

- Verbatim manufacturer marketing copy ("the X-series delivers
  industry-leading performance for demanding applications…").
- Manufacturer photographs or rendered images of products. (We
  display generic category icons; if a manufacturer would like their
  own image used, see the opt-in below.)
- Manufacturer trademarks or logos for any purpose other than
  identifying the product being compared.
- Anything from a datasheet behind a login wall, or that the
  manufacturer's robots.txt asks crawlers not to index.

---

## Takedown — how to request one

Email `nick@advin.io` with:

1. The manufacturer + product line you represent.
2. The specific URL or identifier within Specodex.
3. The basis (copyright, trademark, factual error, internal policy
   change).

Acknowledgement within 2 business days; action within 5 business days.
Default action for any borderline case is **delist while we review**.
We don't litigate over individual products. The relationship matters
more than the row.

---

## Opt-in — manufacturers who want a *better* listing

If you'd like Specodex to use your official product imagery, link to
your canonical product pages, surface fields you think matter that we
don't index yet, or include first-party application notes / sizing
calculators, we want to talk. Email the same address with subject
`Specodex opt-in: <your company>`.

There is no fee, paid placement, or pay-to-rank. Featured listings are
informational, not promotional, and the catalog stays editorially
neutral across vendors. (If we ever add paid surfaces — e.g. premium
data feeds for distributors — they'll be clearly labeled and never
mixed into the user-facing comparison UI.)

---

## Continuity — what happens if I'm unreachable

Solo bootstrapped projects fail when the founder gets sick, takes a
job offer, or otherwise drops off the grid. This section is the
plan for that.

**If I'm out of contact for 60 days:**

- A trusted contact (named in a sealed envelope, kept off the public
  internet) holds the AWS root credentials and a runbook for putting
  the production deploy into read-only mode without breaking
  customers' workflows.
- The repository at <https://github.com/JimothyJohn/specodex> stays
  available regardless. The MIT license means anyone can fork and
  continue the project; the data export cadence (DynamoDB → S3,
  weekly) means a fork can rebuild a catalog from scratch.
- Paying customers (when there are any) get prorated refunds and
  30-day data export per the terms of service.

**Backups and exports:**

- DynamoDB is backed up via AWS PITR (point-in-time recovery) on all
  stages.
- A weekly data export to S3 keeps a portable JSON copy of the
  catalog independent of AWS-specific recovery mechanisms.
- The source-PDF library stays in S3 with cross-region replication
  on prod.

**Why this is here:** prospects, partners, and future hires all
benefit from knowing the bus factor is acknowledged, not hidden.
Engineers reading this should be able to evaluate the project on its
merits without privately wondering if we'll be here in 24 months.
The honest answer is "we plan to be, and here's what happens if I'm
not."

---

## License

- **Code:** MIT (see [LICENSE](LICENSE)). Use it, fork it, vendor it
  into your own product. Attribution appreciated, not enforced.
- **Catalog data:** the structured spec data Specodex publishes is
  factual and not subject to copyright; you may scrape, reuse, or
  redistribute it. Aggregations, schemas, and presentation choices
  are © 2025 Nick Armenta. If you're building something that wants
  programmatic access at meaningful volume, the friendly path is to
  ask about an API key — `nick@advin.io`.
- **Manufacturer trademarks** (product names, logos, model numbers)
  remain property of their respective owners and are used here only
  for nominative identification.

---

## Last updated

This document is reviewed at least quarterly, or whenever Specodex's
data sources or hosting arrangements change. Last review: 2026-05-09.
