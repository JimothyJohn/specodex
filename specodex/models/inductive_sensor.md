# InductiveSensor Model — Design Notes & Sources

Companion to `inductive_sensor.py`. Documents the schema for inductive
proximity sensors — the first sensing (rather than motion or switching)
product type in the catalog.

## Scope

Non-contact sensors that detect a metal target by the damping of an
oscillating field at the sensing face, per IEC 60947-5-2. Switching
variants (DC 2/3/4-wire, AC and AC/DC 2-wire, NAMUR) and
distance-proportional analog variants, in threaded-barrel, smooth-barrel,
rectangular, ring and slot housings.

**Out of scope:**
- Capacitive, magnetic (reed / Hall / cylinder), photoelectric and
  ultrasonic proximity sensors — different physics, different headline
  specs. Several source catalogs mix them in; the ingest prompt skips
  them.
- Inductive *measurement* systems (LVDTs, inductive linear encoders,
  eddy-current displacement probes with separate amplifiers).
- Safety-rated inductive switches' functional-safety figures (PL, SIL,
  MTTFd) — see Known gaps.
- Accessories: cordsets, mounting brackets, nuts.

## Reference sources

Schemagen ran on seven sources from seven vendors; the result was then
revised by hand against a wider read of thirteen vendors' documents.
"Official" means fetched from the vendor's own domain; every other file
is a distributor-hosted copy of a vendor-authored PDF and may be an
older edition.

| Source | Pages | Vendor | Origin | Relevance |
|---|---|---|---|---|
| `sick-imb18-08bpovc0k.pdf` | 6 | SICK | official | Single-part sheet. Sn *and* Sa, reproducibility, tightening torque, enclosure rating as a list (IP68, IP69K). |
| `baumer-ifrm-18-short.pdf` | 1 | Baumer | distributor | One sheet, eight variants. "make / break function", quasi-shielded mounting, `+Vs` range, `Vd`. |
| `keyence-ez.pdf` | 4 | Keyence | reseller | "Detecting distance", "Shielded", "Response frequency", output folded into one "Control output" cell. |
| `datalogic-is-12.pdf` | 16 | Datalogic | distributor | AC 2-wire and NAMUR variants, "Nominal switching distance", residual current. |
| `omron-e2e-next-dc2wire.pdf` | 24 | Omron | official | DC 2-wire: min/max load current, leakage current, "Setting distance", "Differential travel". |
| `pepperl-fuchs-nbb20-u2-uu.pdf` | 4 | Pepperl+Fuchs | official | Rectangular AC/DC 2-wire: rated vs operating voltage, off-state current, separate AC and DC switching frequency, terminal compartment. |
| `balluff-bes-standard-portfolio.pdf` | 2 | Balluff | official | Selection flyer: housing size × length, range, connection, materials. |

Also read while revising (not fed to schemagen): Balluff Global and
Global-special catalogs, Baumer inductive catalog (definitions p8),
SICK IMA / IMB / IMF family overviews and full proximity catalogue
(glossary p287), Keyence EV, Datalogic inductive guide, ifm selection
brochure, Turck B1008 barrel and rectangular, Telemecanique OsiSense XS
(definitions pp. 13–14), Contrinex 500/600 extract, wenglor I12H004,
Autonics PR.

Vendors attempted but not obtained: **Banner Engineering** (no inductive
literature surfaced — every hit was photoelectric), **Leuze** (403).

A first schemagen pass over seven larger documents failed three times on
`ProposedModel` validation (`output_function` proposed as a literal with
no values); the pass above used smaller sources. Schemagen also exposed
that `page_finder` barely recognised sensor pages — see "Page finder"
below.

## Design decisions

### Nominal and assured distance are two fields

Every vendor leads with the nominal sensing distance Sn
(`sensing_distance`). Many print a second, smaller number — and it is
not the same quantity across vendors:

- SICK, Balluff, Baumer, Telemecanique: assured distance Sa =
  0 … 0.81 × Sn (the IEC definition).
- Autonics: "Setting distance (Sa) = Sn × 70 %".
- Omron: "Setting distance", about 75–80 % of Sn.

So `assured_sensing_distance` stores the *printed* upper bound and is
never derived. The pilot ingest showed why that needs enforcing rather
than asking: on the Keyence EV sheet, which prints no assured distance,
Gemini filled the field with a computed value on 19 of 24 rows despite
an explicit instruction not to. The ingest driver now nulls any numeric
value that is not printed on the row's source page.

### `mounting` is a three-state enum

The schemagen proposal had five values with synonyms side by side
(`flush`, `shielded`, `non_flush`, `unshielded`, `quasi_shielded`), which
would have split one population across two filter values. Vendors use
flush / shielded / embeddable for the same thing; the model keeps three
states — `flush`, `non_flush`, `quasi_flush` — and the field description
carries the synonym map for the extractor. Quasi-flush is a real third
state (SICK and Baumer both footnote the required protrusion), not a
boolean.

### Output is three orthogonal fields

Schemagen proposed one `output_circuit_type` literal mixing polarity,
function and wiring (`NPN_NO`, `DC_3_wire`, `NAMUR_2_wire`) — a value
could only say one of the three things. Split into:

- `output_type` — the output stage: `pnp`, `npn`, `pnp_npn`,
  `push_pull`, `two_wire`, `namur`, `analog_voltage`, `analog_current`.
- `output_function` — `no`, `nc`, `complementary` (NO + NC antivalent),
  `programmable`.
- `wiring` — `dc_2_wire` … `ac_dc_2_wire`.

`io_link` is its own boolean because it crosses all three (Omron
supports it on PNP-NO only).

### Three currents, kept apart

- `max_load_current` — what the output can switch.
- `min_load_current` — 2-wire sensors need a minimum load to stay
  powered ("3 to 100 mA").
- `no_load_current` (supply current of a 3-wire sensor) vs
  `leakage_current` (off-state current through the load). Vendors name
  both "current consumption" / "residual current" loosely, and Keyence's
  2-wire sheet prints one figure for both because for a 2-wire device
  they are the same thing. The descriptions route 2-wire parts to
  `leakage_current`.

All are `Current`, so printed mA normalises to A like every other
current in the catalog.

### `switching_frequency` absorbs "response frequency"

Keyence, Omron and Autonics publish "response frequency" and footnote
the test method (standard target, gap of twice the target width, half
the sensing distance). The other vendors publish "switching frequency"
without stating a method on the pages read. Stored in one field because
users compare them as one number, but they may not be strictly
like-for-like across those two vendor groups. Unit is pinned to Hz in
the description: the `Frequency` family accepts kHz without converting,
and a mixed column would mis-sort.

### `housing_size` is a normalised string

`M18 x 1`, `M18×1`, `M18` and Keyence's bare `M18` all mean the same
barrel; smooth barrels are `Ø6.5`; rectangular bodies are a face size.
One string, normalised by instruction (`M18`, `Ø6.5`, `40x40`), is what
users filter on. `type` carries the form factor separately so a
rectangular 40x40 and a threaded M30 don't need string parsing to tell
apart.

### `ip_rating` plus `protection_ratings`

Repo convention is the shared `IpRating` int. Sensors routinely claim
several ratings at once, including lettered ones the int cannot hold
(SICK "IP68, IP69K"; Omron "IP67, IP69K, IP67G"). `protection_ratings`
keeps the printed list; `ip_rating` is backfilled from it by a model
validator (highest plain two-digit rating) because extraction fills the
list and skips the scalar.

Note "protection class" means the IP rating at Baumer but the electrical
class (II / III) at SICK, wenglor and P+F — the `ip_rating` description
says so.

### No `certifications` field

Present in the schemagen proposal and in `Contactor`. Removed here after
the pilot: approval marks are logos, not text, and Gemini filled the
field from the description's examples — on one Keyence row, with 120
invented per-country RoHS marks.

### No shock / vibration fields

Schemagen proposed both as free strings because formats differ wildly
("100 g / 2 ms / 500 cycles" vs "10 to 55 Hz, 1.5 mm double amplitude").
A string that cannot be filtered or sorted, on a spec that is
near-identical across the category, costs every row a quality-score slot
for nothing.

## Page finder

`SPEC_KEYWORDS` had one sensor group, so a sensor page could match at
most one or two of the three groups required: 1 of 36 pages of the
Balluff Global brochure and 0 of 4 of the Keyence EZ sheet were
detected. Two groups were added (binary sensor output; proximity
installation) plus three sensing-distance spellings.
`tests/unit/test_page_finder_proximity.py` pins it.

## Extraction traps

Found by checking the pilot rows against the source pages:

- **Footnote superscripts glue onto part numbers.** Keyence prints
  `EV-130M` followed by a superscript `1.`; text extraction and Gemini
  both read `EV-130M1`. Detectable by span size (3.5 pt vs 6 pt body).
- **`0` and `O` are confused** in part numbers (`IMB18-08BPOVC0K` came
  back as `…COK`). Part numbers must be checked against the page text.
- **Label / value page spreads.** The Balluff main catalog and the
  Contrinex extract print row labels on the left page and values only on
  the right. Per-page extraction cannot work on these; they were not
  ingested.
- **One dense page can exceed the output budget.** The Autonics PR
  instruction sheet lists every DC 3-wire part on a single page and
  truncates mid-JSON.

## Known gaps

- **Reduction (correction) factors** per target metal — SICK, wenglor,
  Keyence publish them, and "factor 1" sensors are a real selection
  criterion. Not modelled.
- **Functional-safety and reliability figures** (MTTFd, mission time).
- **Timing** — start-up delay, response / recovery times in ms
  (Telemecanique, P+F, Datalogic).
- **Standard target size**, **temperature drift**, **shock / vibration**.
- **Analog output detail** — only the output kind is captured, not the
  measuring range, linearity or slope.
- **Electrical protection class** (II / III).

## Fields

See `inductive_sensor.py`. Current set:

- Identity: `type`, `series`
- Housing: `housing_size`, `housing_length`, `housing_material`,
  `sensing_face_material`
- Sensing: `sensing_distance`, `assured_sensing_distance`, `mounting`,
  `switching_frequency`, `hysteresis`, `repeatability`
- Output: `output_type`, `output_function`, `wiring`, `io_link`
- Electrical: `supply_voltage`, `max_load_current`, `min_load_current`,
  `no_load_current`, `leakage_current`, `voltage_drop`,
  `short_circuit_protection`, `reverse_polarity_protection`
- Connection: `connection`, `connector_pins`, `cable_length`
- Environmental / mechanical: `operating_temp`, `ip_rating`,
  `protection_ratings`, `tightening_torque`
