# Marketing plan: get Specodex into the hands of mechatronics engineers

Status: 📐 planned. No paid spend, no ads, no agency. Engineer-to-engineer
distribution, leaning on the niche-signal of the field-manual aesthetic and
the open-source repo as proof of seriousness. Stripe metered billing exists
in `stripe/` (Rust Lambda) — there's a paid surface to graduate to once
free distribution proves the audience.

This doc is the **what** and **where**. SEO is in [SEO.md](SEO.md); the two
overlap (programmatic product pages serve both) but the channels are
distinct and worth keeping separate.

## The audience, sharply

Not "industrial buyers" in the abstract. Concretely, the people who already
have a stack of vendor PDFs open in browser tabs at 4pm on a Wednesday:

| Segment | Title keywords | Where they live | What they want from Specodex |
|---|---|---|---|
| **Mechatronics design engineer** | mechatronics engineer, motion control engineer, controls engineer | LinkedIn, Eng-Tips, r/PLC, r/AskEngineers, vendor forums (Rockwell, Siemens, Beckhoff) | Side-by-side cross-vendor specs without filling out a "talk to sales" form |
| **System integrator / machine builder (OEM)** | systems engineer, machine designer, automation engineer, packaging engineer | CSIA member directory, Pack Expo / Automate booth ops, IMTS, ControlBooth | "Find me a motor that fits this envelope, this torque, this voltage, in stock from anyone" |
| **Robotics startup engineer** | robotics engineer, mech eng (robotics), R&D | r/robotics, ROS Discourse, HN, Bay Area / Boston meetups | Spec'ing prototype builds — payload-/reach-/repeatability-driven search |
| **Sourcing / supply-chain engineer** | sourcing engineer, supplier qualification, commodity manager | LinkedIn, ISM (Inst. for Supply Management), trade-rep newsletters | Apples-to-apples cost + lead-time + spec compare across approved vendors |
| **University / capstone teams** | mechatronics professor, FSAE/FRC mentor, senior design advisor | r/EngineeringStudents, ASME student chapters, FIRST mentor lists | Free, citation-able spec source for student build reports |
| **Consulting / contract design firms** | consulting mechanical engineer, contract automation | LinkedIn, ENR-style trade press, niche newsletters | Same as integrators but billed hourly, so time-savings is the pitch |

The unifying trait: **they all know what `rotor_inertia=4.5e-5 kg·m²` means
and resent UIs that hide it behind "request a quote".** Marketing copy
addressed to anyone who *doesn't* already know that is wasted spend.

## Positioning

**Tagline (canonical, do not soften):**

> A product selection frontend that only an engineer could love.

**Elevator (one paragraph, for cold-channel use):**

> Specodex indexes industrial electromechanical product specs — motors,
> drives, gearheads, contactors, actuators, robot arms — across
> manufacturers, into a single filterable database. Search by the numbers
> that matter (rated power, continuous torque, fieldbus, IP rating), see
> every vendor side-by-side, follow the link straight to the original
> datasheet. No quote-gates, no marketing fluff, no "contact sales".
> Free at <https://datasheets.advin.io>; source on GitHub.

**Anti-positioning (what we are *not*):**

- Not a marketplace. We don't sell parts, we don't take referral fees on
  buys, we don't shadow-rank vendors. (Once that changes, the positioning
  changes — flag it.)
- Not a CAD/PDM/PLM tool. We don't model, we don't quote, we don't BOM-export
  into SAP. (BOM-style copy/export is a planned UX feature; that's
  "convenience for the engineer at their desk", not "PLM integration".)
- Not vendor-affiliated. The site shows ABB, Siemens, Rockwell, Yaskawa,
  Mitsubishi, Schneider, Oriental Motor, Maxon, Nidec, Omron, etc. without
  preferential ordering. This neutrality is the product.

## Channels, ranked by ROI

Engineer audiences ignore advertising and respond to peer signal,
demonstrated competence, and visible craft. Channels below are ordered
by expected return per hour spent.

### Tier 1 — highest leverage, do first

1. **Hacker News "Show HN".** One-shot, but 10k+ engineers read in a
   single morning if it lands on the front page. Submit *after* the
   Specodex rebrand cutover (DNS on `specodex.com`, see
   [REBRAND.md](REBRAND.md)) so the URL is stable for the comment thread.
   Title formula: `Show HN: Specodex – cross-vendor spec database for
   motors, drives, gearheads (datasheet-mined)`. Lead the post with the
   one-paragraph elevator. Have a maintainer (@JimothyJohn) ready to
   answer comments live for the first 4 hours — that's where the actual
   trust gets built. Single attempt; if it flops, wait 90 days before
   resubmitting with substantially new material (a major feature, a
   benchmark blog post, etc.).

2. **r/PLC, r/AskEngineers, r/Mechatronics, r/robotics, r/AutomationEng.**
   Five subreddits, ~600k combined subscribers, all engineer-heavy. Don't
   blast — post one well-written "I built this because I got tired of
   X" thread per sub, spaced over 2-3 weeks. Reddit detects coordinated
   posting and shadowbans; pace matters more than reach. Engage every
   comment in the first 48 hours. Lead with a screenshot of the filter
   chips (TM-01) — it reads as "real software" instantly.

3. **Eng-Tips and ControlBooth forums.** Older, smaller, but extremely
   high-trust audiences. Eng-Tips has a "Motors & Generators Engineering"
   forum with ~50 daily active engineers who actually answer each other's
   datasheet questions. Soft-introduce by *answering questions with
   Specodex links* before posting any standalone announcement. The bar
   for promo posts is very high; the bar for "here's a useful link in a
   genuine answer" is normal.

4. **GitHub repo as a marketing asset.** The README already exists
   (`README.md`), but it's developer-onboarding focused. Add a top-of-
   README screenshot of the catalog UI and a one-line "use the live
   app at datasheets.advin.io / specodex.com — no install needed".
   `awesome-*` lists worth submitting to: `awesome-industrial`, `awesome-
   robotics`, `awesome-mechatronics`, `awesome-engineering-resources`.
   These are slow-burn but each merge ≈ 50-200 referral clicks/month
   forever.

### Tier 2 — sustained content, second

5. **Engineering blog on the GitHub Pages site (`docs/`).** Already
   serving `index.html` — extend with a `/blog/` directory. Three posts
   that earn their keep:
   - **"How we extract specs from a 600-page Mitsubishi catalog without
     blowing $40 on Gemini calls."** Technical, names names, shows the
     `page_finder` heuristic. Engineering audiences love seeing the
     trick. Cross-post to HN as a follow-up if the Show HN went well.
   - **"Yaskawa Σ-7 vs Mitsubishi MR-J5 vs Allen-Bradley Kinetix — by
     the numbers."** Pure data, no opinions. Ranks by rated power /
     continuous torque / footprint. Doubles as SEO bait (see
     [SEO.md](SEO.md)) for the comparison long-tail.
   - **"Building a 3-axis motion stage from scratch: motor + gearhead +
     drive + cylinder."** Walkthrough using Specodex's filter UI. Ties
     into [INTEGRATION.md](INTEGRATION.md) once the chain-review modal
     ships.

6. **YouTube collaborations, no own channel yet.** The audience has
   established channels they trust:
   - Tim Wilborne (`twcontrols`, ~50k subs) — Rockwell-heavy, but
     occasionally features tools.
   - Tim Hyland (`thelearningpit`) — Allen-Bradley educational.
   - RealPars — paid courses, has affiliate-style relationships.
   - Smart Manufacturing Show, automation podcasts.
   Pitch a 5-minute "I'll search for a motor that meets your criteria
   live on the show" segment. Free for them (no ad slot needed), useful
   for their audience, demoable.

7. **LinkedIn — long-form posts, not the algorithm.** LinkedIn's organic
   reach for industrial engineers is genuinely good if the post reads
   like an engineer wrote it (specific, concrete, no buzzwords). Cadence:
   one post every 10-14 days. Topic shape that works:
   "I just did X using these tools, here's the screenshot, here's the
   one weird thing I learned." Avoid "thought leadership" tone — it
   reads as fake to this audience.

### Tier 3 — slower-burn, third

8. **Trade press / e-newsletters.** Most have a "new tools" or "engineer's
   workbench" beat editor who'll publish a 200-word writeup if pitched
   well:
   - *Design World* (designworldonline.com) — has a Motion Control beat.
   - *Control Engineering* (controleng.com) — ICS / PLC heavy.
   - *Machine Design* (machinedesign.com) — broader mechanical.
   - *Motion Control & Sensors* — niche newsletter, very relevant.
   - *Automation World* — process + discrete.
   Approach: short pitch email to the section editor with two sentences
   on what + why, and one image. Don't send a press release. They get
   forty per week and ignore them.

9. **Conferences — booth or no booth.**
   - **Automate** (Detroit, biennial) — the U.S. mechatronics conference.
     A booth is ~$5-15k; not worth it pre-revenue. Free attend + walk +
     hand out something physical (sticker with the URL works) is the
     pre-revenue play.
   - **IMTS** (Chicago, biennial, manufacturing-tech). Same logic.
   - **Pack Expo** (Las Vegas / Chicago, packaging machinery). Same.
   - **NI Week / VIEW** (NI's user conf). Decent crowd; small.
   Decision rule: no paid booth until either (a) Stripe revenue covers
   it 2× or (b) we have a partner who's splitting cost.

10. **Cold outbound to system integrators.** Use the CSIA member
    directory (~600 certified integrators, public list). Personalized
    one-line emails with a screenshot, addressed to the engineering
    lead. Conversion is low (1-2%) but each conversion is a 5-engineer
    team who'll all use it. Only worth doing once we have a second
    engineer; otherwise the response volume eats time.

### Don't bother

- **Google / LinkedIn paid ads.** The CPM for "industrial engineer" is
  brutal and the audience tunes out. Re-evaluate at $10k MRR.
- **Generic SaaS review sites** (G2, Capterra). The audience doesn't
  use them for this kind of tool.
- **Influencer marketing.** Not a thing in this niche. Engineers cite
  forum threads and YouTube tutorials, not "thought leaders".

## Conversion ladder

Free public site → engineer use → either of:

(A) **Bulk / API tier** (paid, via Stripe). Engineers who want
programmatic access, CSV export of the full filtered set, or BOM-import
into their internal tools. Pricing thought: $X/month for individual,
$Y/month for team-of-N. (Numbers TBD; Stripe metered billing is
already plumbed in `stripe/`.)

(B) **Sponsored ingestion** (paid, by manufacturers — *not yet, and only
with neutrality preserved*). Manufacturers who want guaranteed coverage
of their catalog get billed for ingestion, but their products *do not*
rank higher, get no badging, get no preferential placement. The product
remains neutral or it has nothing. **Hold off on this until the user
base is large enough that it matters to them; otherwise it's just
selling data entry.**

(C) **Custom-type ingestion as a service** (paid, by integrators).
A consulting firm has a niche product type (say, hydraulic
proportional valves) they want indexed for their internal use plus
the public site. We run `./Quickstart schemagen` against their PDF
pile and bill for the engineering time. Low-volume, but margin is
high.

## What we measure

| Metric | Target by month-3 of active marketing | Source |
|---|---|---|
| Unique sessions / week to `specodex.com` | 1,000 | CloudFront access logs / GA |
| Returning sessions ratio | > 25% | GA |
| GitHub stars | 250 | repo |
| Search impressions / week (Google) | 5,000 | Google Search Console |
| External backlinks (real, non-spam) | 30 | ahrefs free / Search Console |
| Newsletter / email opt-ins | 100 | TBD — needs a `/subscribe` |
| Stripe conversions (when paid surface ships) | first 5 | Stripe dashboard |

Marketing telemetry isn't built into the data-quality observatory
(`./Quickstart godmode`) today — that report is scoped to catalog
quality. If we want a marketing panel later, it's its own surface;
GA / Plausible + Search Console exports cover the audience side
without a custom dashboard.

## Phasing

| Phase | Window | Gate to next |
|---|---|---|
| **0 — Pre-flight.** Specodex rebrand cutover ([REBRAND.md](REBRAND.md)) complete; SEO foundation ([SEO.md](SEO.md)) phase 1 shipped (sitemap, structured data, prerender). | Until `specodex.com` resolves and Google has indexed > 50 product pages. | Both gates green. |
| **1 — Soft launch.** Show HN. r/PLC + r/AskEngineers thread. Top-of-README screenshot. `awesome-*` PRs. | 30 days. | Sustained > 200 sessions/week; > 50 GitHub stars; no critical UX bugs surfaced. |
| **2 — Content.** Three engineering-blog posts. Two YouTube collab pitches. Three trade-press pitches. | 60 days. | At least one trade-press placement *or* one YouTube mention *or* a second HN appearance. |
| **3 — Outbound.** CSIA cold outreach, LinkedIn cadence, conference attend (no booth). Begin paid surface (Stripe) once free funnel is producing > 1,000 sessions/week. | Indefinite. | Stripe MRR > $0. |
| **4 — Scale.** Re-evaluate paid ads, conference booths, and partnership conversations. Only with revenue justifying spend. | TBD. | — |

## Risks and what to watch

- **Looking like a vendor's affiliate site.** If users perceive Specodex
  as ranking ABB above Siemens (or vice versa), the trust collapses
  permanently. Code-level: the search ordering must be deterministic
  and stable across vendors. Marketing-level: never accept a
  manufacturer-paid placement that affects ranking. Re-read this rule
  before any sponsorship conversation.
- **Datasheet copyright pushback.** Manufacturers occasionally object to
  spec aggregation on copyright grounds. Specs themselves are facts and
  not copyrightable; verbatim datasheet *text* and *images* are. Specodex
  links to the original datasheet rather than re-hosting — preserve that
  hard line. If a takedown notice arrives, comply on the specific item,
  document, and continue. Don't capitulate site-wide.
- **Quality regressions visible to early users.** A high-profile post on
  HN/Reddit with a broken comparison is worse than no post. Run
  `./Quickstart bench --live` and `./Quickstart smoke` against prod
  immediately before any high-traffic announcement.
- **Email collection without a privacy story.** If we add a `/subscribe`,
  it needs a one-paragraph privacy statement. Engineers care about this.

## Triggers

| Trigger (files / topics in your current task) | Surface |
|---|---|
| User mentions "marketing", "launch", "Show HN", "Reddit post", "conference", "outbound", "press", "newsletter", "growth" | this doc |
| `app/frontend/src/components/Welcome.tsx` copy edits, hero tagline changes | this doc + [REBRAND.md](REBRAND.md) |
| Stripe billing surface activation (`stripe/`, paywall, pricing page) | this doc |
| Any conversation about manufacturer outreach, vendor partnerships, or sponsored placements | this doc — re-read the neutrality rule first |
| `docs/blog/` creation, GitHub Pages content additions beyond the landing page | this doc + [SEO.md](SEO.md) |
| User asks "how do I get this in front of {engineers, integrators, OEMs, mechatronics, robotics teams}" | this doc |
