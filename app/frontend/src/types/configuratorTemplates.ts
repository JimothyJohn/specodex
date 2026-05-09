/**
 * Procedural part-number configurator templates.
 *
 * MVP storage: hand-curated frontend fixtures keyed on
 * `(manufacturer, series)`. See `todo/CATAGORIES.md` Part 2.
 *
 * Each template describes how to assemble a vendor's part number from
 * a small set of user-pickable segments (travel, lead, motor mount,
 * etc.). The synthesised string is **client-side ephemeral** — it
 * doesn't get written back to DynamoDB, doesn't trigger a backend
 * call, and is not validated against the vendor's catalog system.
 *
 * **The synthesised SKU may not exist in any vendor's inventory.**
 * The configurator's footer in the UI says exactly this — it's a
 * starting point for a quote, not a verified order number.
 *
 * **Bidirectional.** Each template carries a `parse(partNumber)`
 * regex twin so the configurator can ALSO go backwards: feed it an
 * existing record's part number, get the segment choices that
 * produced it. This is what powers "click a record → configurator
 * pre-populates" in `ActuatorPage`.
 */

export type SegmentKind = 'enum' | 'range' | 'literal';

export interface EnumOption {
  value: string;       // raw token that goes into the part number
  label: string;       // human-readable name shown in the dropdown
}

export interface ConfiguratorSegment {
  /** Internal id; matches the placeholder name in `template`. */
  name: string;
  /** Label shown in the UI. */
  display_name: string;
  /** What kind of input control to render. */
  kind: SegmentKind;
  /** For `enum`: the choices. */
  options?: EnumOption[];
  /** For `range`: numeric bounds + step. */
  min?: number;
  max?: number;
  step?: number;
  /** Unit shown next to the range slider. */
  unit?: string;
  /**
   * sprintf-style format spec (Python-flavoured, since templates were
   * authored that way). Supported tokens at MVP:
   *   {value}        — string passthrough
   *   {value:Nd}     — zero-padded integer (e.g. `{value:03d}` → "072")
   *   {value:.Nf}    — fixed-precision float
   * Unrecognised specs fall back to passthrough.
   */
  encode: string;
  required: boolean;
  /** Optional help text shown under the control. */
  help?: string;
  /**
   * For range/literal segments: how to coerce a parsed string back to
   * the choice value. `'int'` parses as integer, `'float'` as float,
   * `'string'` keeps as-is. Defaults to inferring from `kind`.
   */
  parseAs?: 'int' | 'float' | 'string';
}

/**
 * Specs derived from a configuration choice — the "performance"
 * half of the user's "transforming part numbers, performance, and
 * relations between devices" framing.
 *
 * All fields are optional and best-effort; templates that can't
 * derive a particular spec just leave it undefined. The frontend
 * surfaces the derived block alongside the synthesised PN so users
 * see what their choice means physically.
 */
export interface DerivedSpecs {
  /** Lead pitch normalised to mm/rev. */
  lead_mm?: number;
  /** Theoretical max linear speed at a stated motor RPM (mm/s). */
  max_speed_mm_s?: number;
  /** The motor RPM assumption used to compute max_speed_mm_s. */
  assumed_motor_rpm?: number;
  /**
   * Suggested motor frame size (e.g. "NEMA 23", "IEC 71"). Lets
   * the cross-device-relations layer query the motor catalog.
   */
  suggested_motor_frame?: string;
  /**
   * Free-text caveat when a derivation is approximate (e.g. lead
   * inferred from BNL vs BNM convention).
   */
  caveat?: string;
}

export interface ConfiguratorTemplate {
  /** Vendor (matches `manufacturer` field on records). */
  manufacturer: string;
  /** Family (matches `series` field on records). */
  series: string;
  /**
   * Part-number assembly recipe. `{name}` placeholders match
   * `segments[name].name`. Anything outside `{...}` is preserved
   * verbatim, including separators and trailing modifier slots
   * we don't model (e.g. accessory bundle codes).
   */
  template: string;
  /**
   * Companion regex for parsing an existing part number back into a
   * `ChoiceMap`. **Required if you want clicking a record to
   * pre-populate the configurator.** Named groups must match
   * `segments[*].name`. Trailing un-modelled bits should be in a
   * named group like `(?<trailing>.*)?` so reverse parsing doesn't
   * fail on accessory codes; the trailing group is preserved but
   * not exposed as a segment.
   */
  parseRegex?: RegExp;
  segments: ConfiguratorSegment[];
  /**
   * Free-text vendor quirks: where the encoding came from in the
   * catalog, known footguns, deprecated codes, etc.
   */
  notes?: string;
  /**
   * Catalog source URL or page reference, so the next person can
   * cross-check a segment that looks wrong.
   */
  source?: string;
  /**
   * Series-key aliases this template should also match. Real records
   * may store `series` as `'200 Series'`, `'200-Series'`, `'200'`, or
   * `'Series 200'`; we list every observed spelling here so
   * `findTemplate` doesn't miss them.
   */
  aliases?: string[];
  /**
   * Performance derivation: given the user's segment choices, return
   * inferred physical specs (lead in mm, theoretical max speed at a
   * canonical motor RPM, etc.). Optional — templates without enough
   * encoded information to derive a lead just omit this.
   */
  derive?: (choices: ChoiceMap) => DerivedSpecs;
}

/**
 * Lookup key: `<manufacturer>::<series>`. Lowercased, whitespace-
 * collapsed for case-insensitive match against record fields.
 */
export type TemplateKey = string;

const k = (manufacturer: string, series: string): TemplateKey =>
  `${manufacturer.trim().toLowerCase()}::${series.trim().toLowerCase()}`;

// ---------------------------------------------------------------------------
// Templates. Each one is hand-authored from the vendor's "ordering
// information" page AND cross-checked against real records in dev DB
// where applicable. The dev-DB audit (2026-05-08) showed all 46
// existing linear_actuator records are Tolomatic, so Tolomatic templates
// are first-class here. Lintech and Toyo records aren't in DB yet but
// are observed via the schema fit-check pass; templates are based on
// the catalog ordering pages and the extracted records' part numbers.
// ---------------------------------------------------------------------------

/**
 * Tolomatic TRS — heavy-industrial rodless screw-driven slide.
 *
 * Observed format: `TRS<frame>-BNM<lead>` (e.g. `TRS165-BNM10`).
 * 12 records in dev DB (all variants of `TRS165` and `TRS235`).
 *
 * BNM = Ball-screw, Normal-lead, Metric. The trailing 2 digits are
 * the lead pitch in mm (5, 10, 25 are the catalog options).
 */
const TOLOMATIC_TRS: ConfiguratorTemplate = {
  manufacturer: 'Tolomatic',
  series: 'TRS',
  template: 'TRS{frame}-{drive}{lead}',
  parseRegex: /^TRS(?<frame>\d+)-(?<drive>BNM|BNL|BSM|BSL)(?<lead>\d+)(?<trailing>.*)?$/,
  segments: [
    {
      name: 'frame',
      display_name: 'Frame size',
      kind: 'enum',
      options: [
        { value: '165', label: 'TRS165 (165 mm)' },
        { value: '235', label: 'TRS235 (235 mm)' },
        { value: '305', label: 'TRS305 (305 mm)' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'drive',
      display_name: 'Drive screw',
      kind: 'enum',
      options: [
        { value: 'BNM', label: 'Ball, Normal-lead, Metric' },
        { value: 'BNL', label: 'Ball, Normal-lead, English' },
        { value: 'BSM', label: 'Ball, Short-lead, Metric' },
        { value: 'BSL', label: 'Ball, Short-lead, English' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'lead',
      display_name: 'Lead pitch',
      kind: 'enum',
      options: [
        { value: '05', label: '5 mm / turn' },
        { value: '10', label: '10 mm / turn' },
        { value: '25', label: '25 mm / turn' },
      ],
      encode: '{value}',
      required: true,
    },
  ],
  notes:
    '12 records of this family in dev DB — most-populated linear_actuator ' +
    'family. Drive code BNM/BNL/BSM/BSL combines bearing type + ' +
    'lead-class + units in one token; not all catalog combinations are ' +
    'enumerated above.',
  source: 'Tolomatic TRS catalog (records observed in dev DB, 2026-05-08)',
  derive: (c) => {
    // Drive code's last char encodes units: M = metric (mm), L = English (inches).
    // Lead value: BNM10 = 10 mm/rev; BNL05 = 0.5 in/rev = 12.7 mm/rev.
    const drive = String(c.drive ?? '');
    const leadRaw = parseInt(String(c.lead ?? ''), 10);
    if (Number.isNaN(leadRaw)) return {};
    const isEnglish = drive.endsWith('L');
    const lead_mm = isEnglish
      ? (leadRaw / 10) * 25.4 // 05 → 0.5 in → 12.7 mm
      : leadRaw;              // 10 → 10 mm
    const assumed_rpm = 3000;
    return {
      lead_mm,
      assumed_motor_rpm: assumed_rpm,
      max_speed_mm_s: (lead_mm * assumed_rpm) / 60,
      suggested_motor_frame: 'NEMA 23 / NEMA 34',
      caveat: isEnglish
        ? 'BNL/BSL drives encode lead in tenths-of-an-inch.'
        : undefined,
    };
  },
};

/**
 * Tolomatic BCS — compact rodless ball-screw slide.
 *
 * Observed: `BCS15-BNL05`, `BCS20-BNL02`. Dev DB has 6+5+3=14 records
 * across BCS10, BCS15, BCS20.
 *
 * Note: a single template handles all three frame sizes via the
 * `frame` enum; we don't ship one template per frame.
 */
const TOLOMATIC_BCS: ConfiguratorTemplate = {
  manufacturer: 'Tolomatic',
  series: 'BCS',
  template: 'BCS{frame}-{drive}{lead}',
  parseRegex: /^BCS(?<frame>\d+)-(?<drive>BNL|BNM|BSL|BSM|SN)(?<lead>\d+)(?<trailing>.*)?$/,
  segments: [
    {
      name: 'frame',
      display_name: 'Frame size',
      kind: 'enum',
      options: [
        { value: '10', label: 'BCS10' },
        { value: '15', label: 'BCS15' },
        { value: '20', label: 'BCS20' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'drive',
      display_name: 'Drive screw',
      kind: 'enum',
      options: [
        { value: 'BNL', label: 'Ball, Normal-lead' },
        { value: 'BNM', label: 'Ball, Normal-lead, Metric' },
        { value: 'BSL', label: 'Ball, Short-lead' },
        { value: 'BSM', label: 'Ball, Short-lead, Metric' },
        { value: 'SN', label: 'Standard nut (lead screw)' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'lead',
      display_name: 'Lead pitch',
      kind: 'enum',
      options: [
        { value: '02', label: '0.2 in/turn' },
        { value: '05', label: '0.5 in/turn' },
        { value: '08', label: '0.8 in/turn' },
        { value: '10', label: '1.0 in/turn' },
      ],
      encode: '{value}',
      required: true,
    },
  ],
  notes:
    '14 records across three frame sizes (BCS10/15/20) in dev DB. ' +
    'Family also appears with fingerprint BCS<d>-SN<d> (lead-screw ' +
    'variant of BCS10).',
  source: 'Tolomatic BCS catalog (records observed in dev DB, 2026-05-08)',
  derive: (c) => {
    const drive = String(c.drive ?? '');
    const leadRaw = parseInt(String(c.lead ?? ''), 10);
    if (Number.isNaN(leadRaw)) return {};
    // BCS catalog uses the 'L' suffix (BNL/BSL) for English encoding.
    // 02 → 0.2 in/rev, 05 → 0.5 in/rev, 08 → 0.8 in/rev, 10 → 1.0 in/rev.
    const isEnglish = drive.endsWith('L');
    const isLeadScrew = drive === 'SN';
    const lead_mm = isEnglish
      ? (leadRaw / 10) * 25.4
      : leadRaw;
    const assumed_rpm = isLeadScrew ? 1800 : 3000;
    return {
      lead_mm,
      assumed_motor_rpm: assumed_rpm,
      max_speed_mm_s: (lead_mm * assumed_rpm) / 60,
      suggested_motor_frame: 'NEMA 17 / NEMA 23',
      caveat: isLeadScrew
        ? 'Lead-screw variant; assumed motor RPM is conservative (1800).'
        : undefined,
    };
  },
};

/**
 * Tolomatic ERD — rodless electric cylinder (sister product type).
 *
 * Observed format: `ERD<frame>-BNM<lead>-<travel-mm>`. 27 records in
 * dev DB. Travel encoded as a literal mm value at the end (e.g.
 * `304.8` = 12 inches).
 *
 * Stored as `electric_cylinder` records in the DB schema, but it's
 * still an actuator and the configurator should support it. The
 * Actuator page shows both subtypes in the same view.
 */
const TOLOMATIC_ERD: ConfiguratorTemplate = {
  manufacturer: 'Tolomatic',
  series: 'ERD',
  template: 'ERD{frame}-{drive}{lead}-{travel}',
  parseRegex: /^ERD(?<frame>\d+)-(?<drive>BNM|BNL)(?<lead>\d+)-(?<travel>[\d.]+)(?<trailing>.*)?$/,
  segments: [
    {
      name: 'frame',
      display_name: 'Frame size',
      kind: 'enum',
      options: [
        { value: '15', label: 'ERD15' },
        { value: '20', label: 'ERD20' },
        { value: '25', label: 'ERD25' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'drive',
      display_name: 'Drive screw',
      kind: 'enum',
      options: [
        { value: 'BNM', label: 'Ball, Normal-lead, Metric' },
        { value: 'BNL', label: 'Ball, Normal-lead, English' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'lead',
      display_name: 'Lead pitch',
      kind: 'enum',
      options: [
        { value: '05', label: '5 mm / 0.5 in' },
        { value: '10', label: '10 mm / 1.0 in' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'travel',
      display_name: 'Stroke',
      kind: 'range',
      min: 50,
      max: 1500,
      step: 50,
      unit: 'mm',
      encode: '{value}',
      parseAs: 'float',
      required: true,
      help: 'Catalog ships in fractional-inch increments; the encoding is mm equivalent (e.g. 304.8 = 12 in).',
    },
  ],
  notes:
    '27 records of this family in dev DB. Travel is encoded as a ' +
    'literal mm value (often a fractional-inch conversion, e.g. ' +
    '"304.8" for 12 inches). Step=50 in the slider is rough — the ' +
    'real catalog has more granular travel options.',
  source: 'Tolomatic ERD catalog (records observed in dev DB, 2026-05-08)',
  derive: (c) => {
    const drive = String(c.drive ?? '');
    const leadRaw = parseInt(String(c.lead ?? ''), 10);
    if (Number.isNaN(leadRaw)) return {};
    const isEnglish = drive.endsWith('L');
    const lead_mm = isEnglish
      ? (leadRaw / 10) * 25.4
      : leadRaw;
    const assumed_rpm = 3000;
    return {
      lead_mm,
      assumed_motor_rpm: assumed_rpm,
      max_speed_mm_s: (lead_mm * assumed_rpm) / 60,
      suggested_motor_frame: 'NEMA 23 / NEMA 34',
    };
  },
};

/**
 * Lintech 200 — rodless ball-screw / lead-screw slide.
 *
 * Real catalog format observed via schema fit-check (2026-05-08):
 * `200<frame><travel>-<accessory>` (e.g. `200607-WC0`).
 *
 * - `200` — series prefix
 * - First digit after `200`: frame variant (e.g. 6 in `200607`)
 * - Next two digits: travel in inches (e.g. `07` in `200607` = 7 inches)
 * - Trailing `-WC<n>` or `-WCO<...>`: accessory bundle (preserved
 *   verbatim, not enumerated).
 *
 * Replaces the originally-shipped `200-{drive}-{travel}-{lead}-{mount}`
 * template, which was an honest-best-guess that didn't match real
 * Lintech part numbers.
 */
const LINTECH_200: ConfiguratorTemplate = {
  manufacturer: 'Lintech',
  series: '200 Series',
  aliases: ['200', '200-series', 'series 200'],
  template: '200{frame}{travel:02d}-WC{accessory}',
  parseRegex: /^200(?<frame>\d)(?<travel>\d{2})-WC(?<accessory>\w*)$/,
  segments: [
    {
      name: 'frame',
      display_name: 'Frame variant',
      kind: 'enum',
      options: [
        { value: '4', label: '2004 (frame 4)' },
        { value: '5', label: '2005 (frame 5)' },
        { value: '6', label: '2006 (frame 6)' },
        { value: '7', label: '2007 (frame 7)' },
        { value: '8', label: '2008 (frame 8)' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'travel',
      display_name: 'Travel length',
      kind: 'range',
      min: 1,
      max: 96,
      step: 1,
      unit: 'in',
      encode: '{value:02d}',
      parseAs: 'int',
      required: true,
      help: 'Catalog ships in 6-inch increments; slider step relaxed to 1 inch.',
    },
    {
      name: 'accessory',
      display_name: 'Accessory bundle',
      kind: 'literal',
      encode: '{value}',
      parseAs: 'string',
      required: false,
      help: 'WC suffix codes (e.g. WC0, WC1A) are vendor-internal accessory bundles. Free text.',
    },
  ],
  notes:
    'Real-world format derived from catalog ordering page + ' +
    '28 variants extracted via schema fit-check 2026-05-08. The original ' +
    'hand-authored template (200-{drive}-{travel}-{lead}-{mount}) was a ' +
    'guess and did not match any real Lintech part number; replaced.',
  source: 'https://www.lintechmotion.com/pdffiles/Lintech_200_Whole_Section_2020-09.pdf',
  derive: (c) => {
    // Lintech 200 family: lead is encoded in the accessory bundle, not
    // a first-class segment. Without a vendor table we can't infer
    // lead_mm; derivation surfaces only the travel for now.
    const travelIn = parseInt(String(c.travel ?? ''), 10);
    if (Number.isNaN(travelIn)) return {};
    return {
      caveat:
        'Lintech 200 encodes lead pitch in the accessory bundle (WC<n>), ' +
        'not a separate segment. Lead-derived speed needs a vendor table to compute.',
      suggested_motor_frame: 'NEMA 23',
    };
  },
};

/**
 * Toyo Y-series — Japanese rodless screw-driven actuator.
 *
 * Format: `Y<frame>-<subtype>` (e.g. `Y43-L2`). Five variants
 * extracted via the schema fit-check pass; not in DB yet.
 */
const TOYO_Y: ConfiguratorTemplate = {
  manufacturer: 'Toyo',
  series: 'Y43',
  aliases: ['y', 'y-series', 'y series'],
  template: 'Y{frame}-{subtype}',
  parseRegex: /^Y(?<frame>\d+)-(?<subtype>\w+)$/,
  segments: [
    {
      name: 'frame',
      display_name: 'Frame size',
      kind: 'enum',
      options: [
        { value: '43', label: 'Y43' },
        { value: '53', label: 'Y53' },
        { value: '70', label: 'Y70' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'subtype',
      display_name: 'Subtype',
      kind: 'enum',
      options: [
        { value: 'L1', label: 'L1 (low-load short stroke)' },
        { value: 'L2', label: 'L2 (low-load mid stroke)' },
        { value: 'M1', label: 'M1 (mid-load short stroke)' },
        { value: 'M2', label: 'M2 (mid-load mid stroke)' },
      ],
      encode: '{value}',
      required: true,
    },
  ],
  notes:
    'Toyo encodes load class and stroke band into a single subtype ' +
    'token (L1/L2/M1/M2). Lead pitch and stroke length live on a ' +
    'separate dimensional table the configurator does not yet model.',
  source: 'https://www.toyorobot.com/File/Fsrv_NoAuthority_Download/f24100116381920',
};

/**
 * Parker HD — screw-driven positioner. Documentation-only.
 *
 * Parker's catalog PDFs are Akamai-blocked from automated fetches
 * (verified twice: original schemagen pass and the 2026-05-08 fit-
 * check). This template is hand-authored from the public ordering
 * page; **no DB-backed validation**.
 *
 * Kept here so the Actuator page demos a multi-vendor configurator,
 * but the synthesised SKUs are unverifiable until somebody supplies
 * a real Parker catalog.
 */
const PARKER_HD: ConfiguratorTemplate = {
  manufacturer: 'Parker',
  series: 'HD',
  template: '{frame}-{travel}-{drive}-{mount}-{feedback}',
  parseRegex: /^(?<frame>HD\d+)-(?<travel>\d+)-(?<drive>BS\d+)-(?<mount>IL|PL|P\d)-(?<feedback>NF|IN|EN)(?<trailing>.*)?$/,
  segments: [
    {
      name: 'frame',
      display_name: 'Frame size',
      kind: 'enum',
      options: [
        { value: 'HD08', label: 'HD08' },
        { value: 'HD12', label: 'HD12' },
        { value: 'HD15', label: 'HD15' },
        { value: 'HD20', label: 'HD20' },
        { value: 'HD25', label: 'HD25' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'travel',
      display_name: 'Stroke',
      kind: 'range',
      min: 100,
      max: 2000,
      step: 100,
      unit: 'mm',
      encode: '{value:04d}',
      parseAs: 'int',
      required: true,
    },
    {
      name: 'drive',
      display_name: 'Drive screw',
      kind: 'enum',
      options: [
        { value: 'BS05', label: 'Ball screw, 5 mm lead' },
        { value: 'BS10', label: 'Ball screw, 10 mm lead' },
        { value: 'BS25', label: 'Ball screw, 25 mm lead' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'mount',
      display_name: 'Motor mount',
      kind: 'enum',
      options: [
        { value: 'IL', label: 'In-line coupling' },
        { value: 'PL', label: 'Parallel reverse' },
        { value: 'P1', label: 'Parallel 1:1' },
      ],
      encode: '{value}',
      required: true,
    },
    {
      name: 'feedback',
      display_name: 'Feedback',
      kind: 'enum',
      options: [
        { value: 'NF', label: 'None (motorless)' },
        { value: 'IN', label: 'Inductive home/limit switches' },
        { value: 'EN', label: 'Encoder + limits' },
      ],
      encode: '{value}',
      required: true,
    },
  ],
  notes:
    'Parker catalogs are Akamai-blocked from automated fetches; no DB-' +
    'backed validation for this template. Hand-authored from the ' +
    'public ordering page. Real catalog adds optional cable carrier, ' +
    'brake, and connector codes after this stem.',
  source: 'Parker HD-Series catalog (URL Akamai-blocked, 2026-05-08)',
};

const TEMPLATES: ConfiguratorTemplate[] = [
  TOLOMATIC_TRS,
  TOLOMATIC_BCS,
  TOLOMATIC_ERD,
  LINTECH_200,
  TOYO_Y,
  PARKER_HD,
];

const TEMPLATE_INDEX: Map<TemplateKey, ConfiguratorTemplate> = (() => {
  const m = new Map<TemplateKey, ConfiguratorTemplate>();
  for (const t of TEMPLATES) {
    m.set(k(t.manufacturer, t.series), t);
    for (const alias of t.aliases ?? []) {
      m.set(k(t.manufacturer, alias), t);
    }
  }
  return m;
})();

/**
 * Look up a template for the given record. Returns `null` if no
 * template is registered for that `(manufacturer, series)` pair —
 * the configurator drawer should then stay collapsed for that record.
 *
 * Matches against the canonical key AND any registered aliases — real
 * records have `series` stored as `'200 Series'`, `'200-Series'`,
 * `'200'`, etc., and the lookup must be tolerant.
 */
export function findTemplate(
  manufacturer: string | null | undefined,
  series: string | null | undefined,
): ConfiguratorTemplate | null {
  if (!manufacturer || !series) return null;
  return TEMPLATE_INDEX.get(k(manufacturer, series)) ?? null;
}

/** All templates, useful for the "browse configurators" demo view. */
export function allTemplates(): ConfiguratorTemplate[] {
  return TEMPLATES.slice();
}

// ---------------------------------------------------------------------------
// Synthesis — turn (template, choices) into a part number.
// ---------------------------------------------------------------------------

export type ChoiceMap = Record<string, string | number>;

export interface SynthesisResult {
  partNumber: string | null;
  errors: string[];
}

/**
 * Render a single segment's chosen value through its `encode` spec.
 * Supports `{value}`, `{value:Nd}` zero-pad, `{value:.Nf}` fixed.
 */
function applyEncode(spec: string, raw: string | number): string {
  if (spec === '{value}') return String(raw);

  const zPad = spec.match(/^\{value:(\d+)d\}$/);
  if (zPad) {
    const width = parseInt(zPad[1], 10);
    const n = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
    if (Number.isNaN(n)) return String(raw);
    return String(Math.trunc(n)).padStart(width, '0');
  }

  const fixed = spec.match(/^\{value:\.(\d+)f\}$/);
  if (fixed) {
    const prec = parseInt(fixed[1], 10);
    const n = typeof raw === 'number' ? raw : parseFloat(String(raw));
    if (Number.isNaN(n)) return String(raw);
    return n.toFixed(prec);
  }

  return String(raw);
}

export function synthesise(
  template: ConfiguratorTemplate,
  choices: ChoiceMap,
): SynthesisResult {
  const errors: string[] = [];
  const encodedSegments: Record<string, string> = {};

  for (const seg of template.segments) {
    const raw = choices[seg.name];
    if (raw === undefined || raw === null || raw === '') {
      if (seg.required) {
        errors.push(`Missing ${seg.display_name}`);
        // Required segment unset: keep the placeholder visible so the
        // caller can see what to fill in. (We early-exit with errors
        // below, so this only surfaces if the caller ignores errors.)
        encodedSegments[seg.name] = `{${seg.name}}`;
      } else {
        // Optional segment unset: render as empty string so the
        // template's surrounding literals stay clean (e.g. Lintech's
        // "-WC{accessory}" becomes "-WC", not "-WC{accessory}").
        encodedSegments[seg.name] = '';
      }
      continue;
    }
    if (seg.kind === 'enum') {
      const ok = (seg.options ?? []).some((o) => o.value === String(raw));
      if (!ok) {
        errors.push(
          `${seg.display_name}: "${raw}" is not a recognised option.`,
        );
        continue;
      }
    } else if (seg.kind === 'range') {
      const n = typeof raw === 'number' ? raw : parseFloat(String(raw));
      if (Number.isNaN(n)) {
        errors.push(`${seg.display_name}: "${raw}" is not a number.`);
        continue;
      }
      if (seg.min !== undefined && n < seg.min) {
        errors.push(`${seg.display_name}: ${n} is below minimum ${seg.min}.`);
        continue;
      }
      if (seg.max !== undefined && n > seg.max) {
        errors.push(`${seg.display_name}: ${n} is above maximum ${seg.max}.`);
        continue;
      }
    }
    encodedSegments[seg.name] = applyEncode(seg.encode, raw);
  }

  if (errors.length > 0) {
    return { partNumber: null, errors };
  }

  const partNumber = template.template
    // First strip the format-spec annotations from the template
    // string itself (e.g. `{travel:02d}` → `{travel}`) so the regex
    // below resolves cleanly.
    .replace(/\{(\w+)(?::[^}]+)?\}/g, (_match, name: string) =>
      encodedSegments[name] ?? `{${name}}`,
    );
  return { partNumber, errors: [] };
}

// ---------------------------------------------------------------------------
// Reverse parser — turn a part number into a `ChoiceMap` so the
// configurator drawer can pre-populate from an existing record. The
// inverse of `synthesise`.
// ---------------------------------------------------------------------------

export interface ParseResult {
  /** Choices the part number resolves to; null if the regex didn't match. */
  choices: ChoiceMap | null;
  /** The trailing un-modelled portion (e.g. accessory codes), if any. */
  trailing: string | null;
  /**
   * Per-segment validation result. A choice may be present but fail
   * validation (e.g. parsed travel is out-of-range for the slider).
   * The configurator should show the segment value and surface the
   * warning rather than silently dropping it.
   */
  warnings: string[];
}

function coerceParsed(
  raw: string,
  segment: ConfiguratorSegment,
): string | number {
  const target =
    segment.parseAs ??
    (segment.kind === 'range' ? 'float' : 'string');
  if (target === 'int') {
    const n = parseInt(raw, 10);
    return Number.isNaN(n) ? raw : n;
  }
  if (target === 'float') {
    const n = parseFloat(raw);
    return Number.isNaN(n) ? raw : n;
  }
  return raw;
}

/**
 * Parse a part number against a template, returning the `ChoiceMap`
 * that would synthesise it. Returns `{choices: null}` if the regex
 * doesn't match.
 *
 * The parsed result is **provisional** — values may be out-of-range
 * for the segment's slider bounds (vendors revise their catalogs;
 * old part numbers may exceed today's catalogued options). Surface
 * warnings via `warnings[]` instead of dropping the parse.
 */
export function parsePartNumber(
  template: ConfiguratorTemplate,
  partNumber: string,
): ParseResult {
  if (!template.parseRegex) {
    return {
      choices: null,
      trailing: null,
      warnings: ['No parseRegex on template — cannot reverse-parse.'],
    };
  }
  const match = template.parseRegex.exec(partNumber.trim());
  if (!match || !match.groups) {
    return { choices: null, trailing: null, warnings: [] };
  }
  const groups = match.groups;
  const segmentByName = new Map(template.segments.map((s) => [s.name, s]));
  const choices: ChoiceMap = {};
  const warnings: string[] = [];

  for (const [name, raw] of Object.entries(groups)) {
    if (name === 'trailing' || raw === undefined) continue;
    const seg = segmentByName.get(name);
    if (!seg) {
      // Group in regex doesn't correspond to a segment — treat as
      // structural and skip silently.
      continue;
    }
    const coerced = coerceParsed(raw, seg);
    choices[name] = coerced;

    // Warn (don't fail) on bounds / enum mismatches.
    if (seg.kind === 'enum') {
      const known = (seg.options ?? []).some((o) => o.value === String(coerced));
      if (!known) {
        warnings.push(
          `${seg.display_name}: parsed "${coerced}" is not in the catalogued options.`,
        );
      }
    } else if (seg.kind === 'range') {
      const n = typeof coerced === 'number' ? coerced : parseFloat(String(coerced));
      if (!Number.isNaN(n)) {
        if (seg.min !== undefined && n < seg.min) {
          warnings.push(
            `${seg.display_name}: parsed ${n} is below the segment's minimum ${seg.min}.`,
          );
        }
        if (seg.max !== undefined && n > seg.max) {
          warnings.push(
            `${seg.display_name}: parsed ${n} is above the segment's maximum ${seg.max}.`,
          );
        }
      }
    }
  }

  return {
    choices,
    trailing: groups.trailing ?? null,
    warnings,
  };
}

/**
 * High-level convenience: given a record's `(manufacturer, series,
 * part_number)`, find the template and parse the PN. Returns null
 * if no template matches the record's family.
 */
export function parseRecord(
  manufacturer: string | null | undefined,
  series: string | null | undefined,
  partNumber: string | null | undefined,
): { template: ConfiguratorTemplate; result: ParseResult } | null {
  const tpl = findTemplate(manufacturer, series);
  if (!tpl || !partNumber) return null;
  return { template: tpl, result: parsePartNumber(tpl, partNumber) };
}
