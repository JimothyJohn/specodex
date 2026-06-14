# PRICING — populate `msrp` at scale from public price sources

> 🚧 in progress — accepted by Nick 2026-06-12, resolving the
> DB_CLEANUP Phase 2 "populate vs drop `msrp`" decision as
> **populate**. Phase 1 shipped #268 (`./Quickstart price-book`);
> Phase 3 no-key items shipped #270; **Phase 2 (Serper `/shopping`
> tier) shipped 2026-06-12** — `SERPER_API_KEY` turned out to be in
> the shell environment all along (just not in `.env`), so the tier
> went live the same day. Remaining: finish the backfill sweeps and
> keep the per-vendor source map below current.
>
> **Phase 1 field note (2026-06-12).** The ABB/Baldor 501 Index
> parses clean (6,890 rows → 6,817 in-band pairs) but joins 0 rows:
> it covers Baldor-Reliance NEMA *stock* motors, while our unpriced
> Baldor rows are BSM *servo* motors and our ABB rows are ACS/ACQ
> drives. Baldor's servo "price list" lead (BR1202) turned out to be
> the *specs* catalog — its number-dense pages are torque/RPM tables,
> which is exactly why `find_price_pages` requires price words AND
> dollar density before sending a page to the LLM. The only servo
> price list found (CA1202) is from 2011 — stale data, skipped.
> **Verdict: with current part-number hygiene, no public price book
> matches our inventory. OEM stores + the shopping tier are the
> productive paths; price-book stays useful for vendors whose
> catalogs we ingest fresh (Worldwide Electric and US Motors prove
> the pattern).** A live dry-run also caught frame-size codes
> (`R1`/`R2`) stored as part numbers joining against short catalog
> numbers — fixed with `MIN_JOIN_KEY_LEN = 4`.

## Per-vendor source map (live results, 2026-06-12)

| Vendor (unpriced) | Working source | Observed yield / notes |
|---|---|---|
| Mitsubishi (1,571) | `shop1.us.mitsubishielectric.com` JSON-LD (oem) | 6/12 pilot; store 429s hard — needs `--rate-limit 6` + the 3x Retry-After loop; misses are descriptive/fragment PNs |
| AMC (1,169) | Electromate direct slug (distributor) + shopping fallback | ~18-30% Electromate, shopping covers part of the rest |
| Yaskawa (1,191) | shopping tier | 4/10 pilot, sane medians; `-OY` (Omron-Yaskawa) variants miss |
| Delta/Fuji/Hitachi/ABB/Siemens/AB/Parker (~7,800) | shopping tier (sweep running) | DrivesWarehouse search ignores its query param — rejected; Galco 403s the honest UA — rejected |
| Bodine (316) | shopping only | their store 302s unknown slugs to the parent category — direct-slug pattern REJECTED (and the fetcher now treats parent-redirects as misses) |
| WEG (598) | none | stored PNs are frame codes (`K39 W22`), not catalog numbers — unfixable at the pricing layer; needs PN hygiene at ingest |
| Pittman/ElectroCraft/STOBER/Tolomatic (~1,615) | shopping (low expectations) | custom/quote-only vendors |

**Operational learnings encoded in code:** Newark search cut (100%
timeout, 0 hits ever); robots-dead tiers cut (#270); parent-redirect
guard in `fetch.py`; 429 retry loop; `--tiers` + `--rate-limit` +
`--max-shopping-calls` flags on `price-enrich`.

## Evidence (2026-06-11, products-dev)

Coverage: drive 70/13,295 (0.5%), motor 801/7,786 (10.3%), all other
types 0/1,191. ≈21,500 rows missing `msrp`.

The two load-bearing facts, both measured this session:

1. **870 of the 871 priced rows have no `msrp_source_url`** — they did
   NOT come from the price-enrich crawler. They were extracted during
   normal catalog ingest, because `msrp` rides along in the Gemini
   extraction schema and Worldwide Electric (445) / US Motors (292)
   print list prices in their catalogs. **Printed price catalogs are
   the only source that has ever produced at scale.**
2. **A live `--dry-run --limit 8` of the crawler extracted 0/8.**
   Failure modes are structural, not tuning:
   - `robots.txt` disallows the search endpoints on Wolf Automation,
     Motion Industries, Allied, Radwell, and PLCCenter (we respect
     robots — those resolver tiers are permanently dead as built).
   - Galco 403s plain HTTP; Newark times out; Grainger needs JS but
     Playwright browsers aren't installed (`playwright install`).
   - Kyklo storefronts 302-to-root (parts not carried).
   - SERP tier silently no-ops — `SERPER_API_KEY` is not in `.env`.
   - Templated part numbers (e.g. Allen-Bradley
     `21G11*F960JNONNNNN`, literal `*` = option placeholder) can
     never match a store SKU. These need to be detected and skipped
     (or expanded) before any per-PN lookup.

Top missing manufacturers: ABB 2,259 · Mitsubishi 1,605 · AMC 1,441 ·
Yaskawa 1,307 · Siemens 1,168 · Allen-Bradley 1,113 · Fuji 1,044 ·
Parker 942 · Delta 852 · WEG 531.

## Phase 1 — price-book ingestion (bulk; the proven path)

One public price book covers hundreds–thousands of PNs in one
operator run, vs. one crawl per PN. Verified-public sources:

- **ABB/Baldor "501 Stock Product Price File"** — an Excel file of
  catalog number → list price, published on baldor.com
  (`/brands/baldor-reliance/product-support/501-stock-product-price-file`).
  Structured join, no LLM at all.
- **Baldor-Reliance CA501 pricing catalog** (PDF, multiple editions
  incl. 2025) — page_finder → Gemini path.
- **WEG price catalogs** (e.g. `static.weg.net/medias/downloadcenter/
  .../WEG-WMO-zest_weg_motors--price-english-web.pdf`); WEG US price
  book to be located per region.
- **KB Electronics / Dart Controls price schedules** — the simple-DC
  drive vendors just ingested via `cli/data/simple_dc_drives.json`;
  both publish dealer price sheets. Verify at run time.

Build: new CLI `./Quickstart price-book <pdf|xlsx|url> --manufacturer X
[--dry-run]`:

1. XLSX path: read (catalog number, list price) columns directly.
2. PDF path: `find_spec_pages_by_text` with price-table keywords
   (`list price`, `price`, `$`, `multiplier`) → Gemini with a
   `PricePair` response_schema (`part_number`, `price_usd`) — same
   machinery as product extraction.
3. Join on `normalize_string(part_number)` against rows of that
   manufacturer missing `msrp`; report match-rate; `--dry-run` prints
   the join table before any write.
4. Write `msrp = {value, "USD"}`, `msrp_source_url = <book URL>`,
   `msrp_fetched_at`. Never overwrite a populated `msrp`.

Property tests for the PN-normalization join and the price parser per
the repo convention (`test_<module>_property.py`).

## Phase 2 — Serper `/shopping` tier (per-PN, structured, no scraping)

`google.serper.dev/shopping` is a real endpoint (verified 403-without-
key, not 404). One POST per PN returns Google Shopping results
(merchant, price, title) as JSON — no fetching, no robots exposure, no
HTML parsing. Plan:

- New resolver tier `shopping` ahead of SERP-organic in
  `cli/price_enrich.py`, gated on `SERPER_API_KEY` (operator adds the
  key; free tier covers a pilot, paid is ~$0.30–1.00/1k queries — the
  whole 21.5K backlog is single-digit-to-low-double-digit dollars).
- Trusted-merchant allowlist (reuse + extend the resolver's domain
  tiers); take the modal/median USD price across allowlisted
  merchants; tag `source_type` so street price is distinguishable
  from a book list price downstream.
- Skip templated PNs (anything matching `[*#]` or option-blank
  patterns) — also applies to the existing cascade.
- Budget caps mirror `--max-serp-calls`.

Best expected yield: Mitsubishi, Delta, WEG, Fuji, Teco-class VFDs —
products actually sold online. Quote-only vendors (Yaskawa servo,
STOBER, Tolomatic) will mostly miss; that's fine, Phase 1 and the
aggregator tier carry those.

## Phase 3 — crawler salvage (only what the evidence supports)

- Remove the robots-disallowed search-endpoint candidates (Wolf,
  Motion Industries, Allied, Radwell `/search`, PLCCenter `/search`)
  from `resolver.py` — dead weight on every run.
- Replace them with SERP-organic → **product-detail** URLs: many
  sites disallow `/search` but allow product pages; the fetcher
  already robots-checks the final URL. Needs `SERPER_API_KEY`.
- `playwright install chromium` (browsers absent locally; the
  escalation path currently can't run at all).
- Expand the Kyklo storefront roster (one line per store in
  `_KYKLO_DISTRIBUTORS`); discovery via `inurl:shop.` +
  "powered by KYKLO" searches. Each store = JSON-LD `Product.offers`
  with SKU guard already handled.
- Extend `PRODUCT_CLASSES` in `cli/price_enrich.py` beyond
  drive/motor — gearhead, robot_arm, electric_cylinder,
  linear_actuator are currently unsupported (0% coverage).
- Fix the stats bug where `serp_calls` increments even when no key is
  configured (`_iter_candidates` counts before `serp_candidates`
  bails).

## Sequencing & ownership

Phase 1 first (largest yield, zero new vendors, no new deps beyond
maybe `openpyxl`), then 2 (needs Nick to provision `SERPER_API_KEY`),
then 3. Phases are independently shippable PRs. Re-run
`cli/audit_fields` after each phase; success metric is `msrp`
coverage by product type, reported in the PR body.
