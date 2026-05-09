/**
 * ActuatorPage — Linear Motion supercategory landing.
 *
 * Demonstrates the supercategory layer (`linear_motion` groups
 * `linear_actuator` + `electric_cylinder`) and the procedural part-
 * number configurator. See `todo/CATAGORIES.md` for the full design.
 *
 * Bidirectional integration:
 *   - **Synthesise**: pick a template + segments → get a part number.
 *   - **Parse**: click an existing record → configurator pre-fills
 *     to the choices that produced its part number.
 *   - **Derive**: each template knows how to map its choices to
 *     physical specs (lead pitch in mm, theoretical max speed at a
 *     canonical motor RPM) — surfaced live as the user configures.
 *
 * Self-contained: doesn't share state with `ProductList` (so picking a
 * subtype here doesn't change what `/` shows). Loads its own product
 * data via `apiClient.listProducts(subtype)`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiClient } from '../api/client';
import type { Product } from '../types/models';
import type { LinearActuator, ElectricCylinder, Motor, ValueUnit } from '../types/generated';
import { SUPERCATEGORIES } from '../types/categories';
import {
  allTemplates,
  findTemplate,
  parsePartNumber,
  synthesise,
  type ChoiceMap,
  type ConfiguratorSegment,
  type ConfiguratorTemplate,
  type DerivedSpecs,
} from '../types/configuratorTemplates';
import './ActuatorPage.css';

type Subtype = 'linear_actuator' | 'electric_cylinder';

const SUBTYPE_LABELS: Record<Subtype, { name: string; blurb: string }> = {
  linear_actuator: {
    name: 'Linear Actuator',
    blurb: 'Rodless: payload rides a carriage along a guided rail.',
  },
  electric_cylinder: {
    name: 'Electric Cylinder',
    blurb: 'Rod-style: motor pushes a rod from end-cap; payload external.',
  },
};

function formatValueUnit(vu: ValueUnit | null | undefined): string {
  if (!vu || vu.value === null || vu.value === undefined) return '—';
  const value = typeof vu.value === 'number' ? vu.value : parseFloat(String(vu.value));
  if (Number.isNaN(value)) return '—';
  const formatted = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return vu.unit ? `${formatted} ${vu.unit}` : formatted;
}

interface ActuatorRow {
  product_id: string;
  manufacturer: string;
  product_name: string;
  series: string | null | undefined;
  part_number: string | null | undefined;
  stroke: ValueUnit | null | undefined;
  max_push_force: ValueUnit | null | undefined;
  drive: string | null | undefined;
  raw: Product;
}

function toRow(p: Product): ActuatorRow | null {
  if (p.product_type === 'linear_actuator') {
    const la = p as LinearActuator & { product_id: string };
    return {
      product_id: la.product_id,
      manufacturer: la.manufacturer,
      product_name: la.product_name,
      series: la.series,
      part_number: la.part_number,
      stroke: la.stroke,
      max_push_force: la.max_push_force,
      drive: la.actuation_mechanism ?? null,
      raw: p,
    };
  }
  if (p.product_type === 'electric_cylinder') {
    const ec = p as ElectricCylinder & { product_id: string };
    return {
      product_id: ec.product_id,
      manufacturer: ec.manufacturer,
      product_name: ec.product_name,
      series: ec.series,
      part_number: ec.part_number,
      stroke: ec.stroke,
      max_push_force: ec.max_push_force,
      drive: ec.motor_type ?? null,
      raw: p,
    };
  }
  return null;
}

interface SegmentControlProps {
  segment: ConfiguratorSegment;
  value: string | number | undefined;
  onChange: (next: string | number) => void;
}

function SegmentControl({ segment, value, onChange }: SegmentControlProps) {
  if (segment.kind === 'enum') {
    return (
      <div>
        <label className="actuator-page__seg-label" htmlFor={`seg-${segment.name}`}>
          {segment.display_name}
          {segment.required ? ' *' : ''}
        </label>
        <select
          id={`seg-${segment.name}`}
          className="actuator-page__seg-select"
          value={value === undefined ? '' : String(value)}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">— select —</option>
          {(segment.options ?? []).map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {segment.help && <div className="actuator-page__seg-help">{segment.help}</div>}
      </div>
    );
  }
  if (segment.kind === 'range') {
    const min = segment.min ?? 0;
    const max = segment.max ?? 100;
    const step = segment.step ?? 1;
    const current = typeof value === 'number'
      ? value
      : value !== undefined && value !== ''
        ? parseFloat(String(value))
        : min;
    return (
      <div>
        <label className="actuator-page__seg-label" htmlFor={`seg-${segment.name}`}>
          {segment.display_name}
          {segment.required ? ' *' : ''}
        </label>
        <div className="actuator-page__seg-range-row">
          <input
            id={`seg-${segment.name}`}
            type="range"
            min={min}
            max={max}
            step={step}
            value={Number.isNaN(current) ? min : current}
            onChange={(e) => onChange(Number(e.target.value))}
          />
          <span className="actuator-page__seg-value">
            {Number.isNaN(current) ? '—' : current}
            {segment.unit ? ` ${segment.unit}` : ''}
          </span>
        </div>
        {segment.help && <div className="actuator-page__seg-help">{segment.help}</div>}
      </div>
    );
  }
  return (
    <div>
      <label className="actuator-page__seg-label" htmlFor={`seg-${segment.name}`}>
        {segment.display_name}
        {segment.required ? ' *' : ''}
      </label>
      <input
        id={`seg-${segment.name}`}
        className="actuator-page__seg-input"
        type="text"
        value={value === undefined ? '' : String(value)}
        onChange={(e) => onChange(e.target.value)}
      />
      {segment.help && <div className="actuator-page__seg-help">{segment.help}</div>}
    </div>
  );
}

export interface MotorCandidate {
  product_id: string;
  manufacturer: string;
  product_name: string;
  part_number: string | null | undefined;
  frame_size: string | null | undefined;
  rated_torque: ValueUnit | null | undefined;
}

/**
 * Filter motor records by best-effort substring match against the
 * configurator's `suggested_motor_frame` ("NEMA 23 / NEMA 34" → motors
 * whose frame_size contains "23" or "34"). Returns up to `limit`
 * candidates sorted by manufacturer + name.
 *
 * Fragile by design: ~half the motor records in DB have no
 * `frame_size`, and vendors use heterogeneous spellings ("23",
 * "Size 23", "NEMA 23"). The MVP intent is to surface a starting
 * point, not to be an authoritative compatibility check.
 */
/**
 * Exported for unit tests — pins the word-boundary matching that
 * keeps "NEMA 23" from matching "Ø230" or "234".
 */
export function filterMotorsByFrame(
  motors: MotorCandidate[],
  suggestedFrame: string,
  limit = 5,
): MotorCandidate[] {
  // Extract digit-runs from the suggestion ("NEMA 23 / NEMA 34" → ["23", "34"]).
  const tokens = Array.from(suggestedFrame.matchAll(/\d+/g)).map((m) => m[0]);
  if (tokens.length === 0) return [];
  // Word-boundary regexes per token. `\b23\b` matches "NEMA 23",
  // "Size 23", "23"; rejects "Ø230", "234". This kicks out false
  // positives but is still tolerant of vendors who don't write
  // "NEMA" or who pad with whitespace/punctuation.
  const patterns = tokens.map((t) => new RegExp(`\\b${t}\\b`));
  const matches = motors.filter((m) => {
    const f = (m.frame_size ?? '').toLowerCase();
    if (!f) return false;
    return patterns.some((re) => re.test(f));
  });
  matches.sort((a, b) =>
    `${a.manufacturer} ${a.product_name}`.localeCompare(
      `${b.manufacturer} ${b.product_name}`,
    ),
  );
  return matches.slice(0, limit);
}

interface MotorSuggestionsProps {
  derived: DerivedSpecs;
}

function MotorSuggestions({ derived }: MotorSuggestionsProps) {
  const suggestedFrame = derived.suggested_motor_frame ?? '';
  const [motors, setMotors] = useState<MotorCandidate[] | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Lazy-load motors only when a template suggests a frame. Cache in
  // state — switching between templates that suggest the same frame
  // shouldn't re-fetch.
  useEffect(() => {
    if (!suggestedFrame || motors !== null) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const data = await apiClient.listProducts('motor');
        if (cancelled) return;
        const candidates: MotorCandidate[] = [];
        for (const p of data) {
          if (p.product_type !== 'motor') continue;
          // Cast through unknown — Persisted<Motor> has tighter
          // optional types than the raw Motor pulled from generated.ts.
          const m = p as unknown as Motor & { product_id: string };
          candidates.push({
            product_id: m.product_id,
            manufacturer: m.manufacturer ?? '',
            product_name: m.product_name,
            part_number: m.part_number,
            frame_size: m.frame_size,
            rated_torque: m.rated_torque,
          });
        }
        setMotors(candidates);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Motor lookup failed.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [suggestedFrame, motors]);

  if (!suggestedFrame) return null;

  const candidates = motors ? filterMotorsByFrame(motors, suggestedFrame) : [];

  return (
    <div className="actuator-page__motor-suggestions">
      <div className="actuator-page__derived-label">
        Compatible motors ({suggestedFrame})
      </div>
      {loading && (
        <div className="actuator-page__derived-caveat">Looking up motors…</div>
      )}
      {error && (
        <div
          className="actuator-page__derived-caveat"
          style={{ color: 'var(--danger)' }}
        >
          {error}
        </div>
      )}
      {!loading && !error && candidates.length === 0 && motors !== null && (
        <div className="actuator-page__derived-caveat">
          No catalogued motors match this frame size.
        </div>
      )}
      {candidates.length > 0 && (
        <ul className="actuator-page__motor-list">
          {candidates.map((m) => (
            <li key={m.product_id} className="actuator-page__motor-item">
              <span className="actuator-page__motor-meta">
                {m.manufacturer}
              </span>
              <span className="actuator-page__motor-name">
                {m.product_name}
                {m.part_number ? ` · ${m.part_number}` : ''}
              </span>
              <span className="actuator-page__motor-spec">
                {m.frame_size ? `frame ${m.frame_size}` : 'no frame'}
                {m.rated_torque?.value !== undefined &&
                m.rated_torque?.value !== null
                  ? ` · ${m.rated_torque.value} ${m.rated_torque.unit ?? ''}`
                  : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="actuator-page__derived-caveat">
        Best-effort match against `frame_size` substring. Vendors spell frames
        inconsistently — verify shaft diameter and mounting pattern before
        ordering.
      </div>
    </div>
  );
}

interface DerivedSpecsRowProps {
  derived: DerivedSpecs;
}

function DerivedSpecsRow({ derived }: DerivedSpecsRowProps) {
  const items: Array<[string, string]> = [];
  if (derived.lead_mm !== undefined) {
    items.push(['Lead pitch', `${derived.lead_mm.toFixed(1)} mm/rev`]);
  }
  if (
    derived.max_speed_mm_s !== undefined &&
    derived.assumed_motor_rpm !== undefined
  ) {
    items.push([
      `Max speed @ ${derived.assumed_motor_rpm} RPM`,
      `${Math.round(derived.max_speed_mm_s)} mm/s`,
    ]);
  }
  if (derived.suggested_motor_frame) {
    items.push(['Compatible motor', derived.suggested_motor_frame]);
  }
  if (items.length === 0 && !derived.caveat) return null;
  return (
    <div className="actuator-page__derived">
      <div className="actuator-page__derived-label">Derived specs</div>
      {items.length > 0 && (
        <dl className="actuator-page__derived-grid">
          {items.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {derived.caveat && (
        <div className="actuator-page__derived-caveat">{derived.caveat}</div>
      )}
    </div>
  );
}

interface ConfiguratorPanelProps {
  templates: ConfiguratorTemplate[];
  activeKey: string;
  onActiveKeyChange: (key: string) => void;
  choices: ChoiceMap;
  onChoicesChange: (next: ChoiceMap) => void;
  /** Banner shown above the configurator when populated from a record. */
  prefilledFrom?: { manufacturer: string; partNumber: string } | null;
  /**
   * Warnings from `parsePartNumber` (e.g. value out-of-range for the
   * segment's slider bounds). Displayed under the panel title.
   */
  parseWarnings?: string[];
}

function ConfiguratorPanel({
  templates,
  activeKey,
  onActiveKeyChange,
  choices,
  onChoicesChange,
  prefilledFrom,
  parseWarnings,
}: ConfiguratorPanelProps) {
  const [copied, setCopied] = useState<boolean>(false);

  const active = useMemo(
    () => templates.find((t) => `${t.manufacturer}::${t.series}` === activeKey) ?? null,
    [templates, activeKey],
  );

  const result = useMemo(
    () => (active ? synthesise(active, choices) : null),
    [active, choices],
  );

  const derived = useMemo(
    () =>
      active && active.derive ? active.derive(choices) : ({} as DerivedSpecs),
    [active, choices],
  );

  const handleCopy = useCallback(async () => {
    if (!result?.partNumber) return;
    try {
      await navigator.clipboard.writeText(result.partNumber);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // navigator.clipboard fails on insecure contexts; user can still
      // highlight + Ctrl-C from the rendered string.
    }
  }, [result]);

  if (templates.length === 0) {
    return (
      <section className="actuator-page__panel">
        <h2 className="actuator-page__panel-title">Part-Number Configurator</h2>
        <p className="actuator-page__panel-body">
          No configurator templates are registered for this subtype yet.
        </p>
      </section>
    );
  }

  return (
    <section className="actuator-page__panel" id="configurator">
      <h2 className="actuator-page__panel-title">Part-Number Configurator</h2>
      <p className="actuator-page__panel-body">
        Pick a vendor family, fill the form-fit-function fields, copy
        the synthesised part number into your quote request. Or click
        a record below — the configurator will reverse-parse it.
      </p>
      {prefilledFrom && (
        <div className="actuator-page__prefill-banner">
          Pre-filled from <code>{prefilledFrom.partNumber}</code> ({prefilledFrom.manufacturer})
        </div>
      )}
      {parseWarnings && parseWarnings.length > 0 && (
        <ul className="actuator-page__synth-errors">
          {parseWarnings.map((w) => (
            <li key={w}>· {w}</li>
          ))}
        </ul>
      )}
      <div className="actuator-page__template-picker" role="tablist">
        {templates.map((t) => {
          const key = `${t.manufacturer}::${t.series}`;
          const isActive = key === activeKey;
          return (
            <button
              key={key}
              role="tab"
              aria-selected={isActive}
              className={
                'actuator-page__template-chip' +
                (isActive ? ' actuator-page__template-chip--active' : '')
              }
              onClick={() => onActiveKeyChange(key)}
            >
              {t.manufacturer} {t.series}
            </button>
          );
        })}
      </div>
      {active && (
        <>
          <div className="actuator-page__configurator-grid">
            {active.segments.map((seg) => (
              <SegmentControl
                key={seg.name}
                segment={seg}
                value={choices[seg.name]}
                onChange={(next) =>
                  onChoicesChange({ ...choices, [seg.name]: next })
                }
              />
            ))}
          </div>
          <div className="actuator-page__synth-row">
            <span className="actuator-page__synth-label">Part #</span>
            <span
              className={
                'actuator-page__synth-value' +
                (result?.partNumber
                  ? ''
                  : ' actuator-page__synth-value--placeholder')
              }
            >
              {result?.partNumber ?? 'configure to complete'}
            </span>
            <button
              type="button"
              className="actuator-page__copy-btn"
              onClick={handleCopy}
              disabled={!result?.partNumber}
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          {result && result.errors.length > 0 && (
            <ul className="actuator-page__synth-errors">
              {result.errors.map((e) => (
                <li key={e}>· {e}</li>
              ))}
            </ul>
          )}
          <DerivedSpecsRow derived={derived} />
          <MotorSuggestions derived={derived} />
          <p className="actuator-page__disclaimer">
            Synthesised from your selections. Verify against the vendor
            before ordering — trailing accessory codes (cable carrier,
            limit switches, brake) are intentionally not modelled.
            {active.source && active.source.startsWith('http') && (
              <>
                {' '}
                Source:{' '}
                <a
                  className="actuator-page__source-link"
                  href={active.source}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  catalog page
                </a>
                .
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}

export default function ActuatorPage() {
  const supercat = SUPERCATEGORIES.linear_motion;
  const [subtype, setSubtype] = useState<Subtype>('linear_actuator');
  const [data, setData] = useState<{
    linear_actuator: ActuatorRow[];
    electric_cylinder: ActuatorRow[];
  }>({ linear_actuator: [], electric_cylinder: [] });
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Configurator state lifted up so record clicks can pre-fill it.
  const templates = useMemo(() => allTemplates(), []);
  const [activeKey, setActiveKey] = useState<string>(
    templates.length > 0
      ? `${templates[0].manufacturer}::${templates[0].series}`
      : '',
  );
  const [choices, setChoices] = useState<ChoiceMap>({});
  const [prefilledFrom, setPrefilledFrom] = useState<
    { manufacturer: string; partNumber: string } | null
  >(null);
  const [parseWarnings, setParseWarnings] = useState<string[]>([]);
  const configuratorRef = useRef<HTMLElement | null>(null);

  // Reset choices + prefill banner when user manually switches template chips.
  // Distinguish from programmatic switches (record-click) by clearing the
  // banner only on user-initiated changes.
  const handleActiveKeyChange = useCallback((next: string) => {
    setActiveKey(next);
    setChoices({});
    setPrefilledFrom(null);
    setParseWarnings([]);
  }, []);

  const handleChoicesChange = useCallback((next: ChoiceMap) => {
    setChoices(next);
  }, []);

  // Click a record → reverse-parse → pre-fill configurator.
  const handleRecordClick = useCallback(
    (row: ActuatorRow) => {
      const tpl = findTemplate(row.manufacturer, row.series);
      if (!tpl || !row.part_number) return;
      const key = `${tpl.manufacturer}::${tpl.series}`;
      const parsed = parsePartNumber(tpl, row.part_number);
      if (!parsed.choices) {
        // Switch template anyway so the user sees the segments, but
        // don't fabricate choices.
        setActiveKey(key);
        setChoices({});
        setPrefilledFrom({
          manufacturer: row.manufacturer,
          partNumber: row.part_number,
        });
        setParseWarnings([
          `Part number "${row.part_number}" did not match the ${tpl.manufacturer} ${tpl.series} regex.`,
        ]);
      } else {
        setActiveKey(key);
        setChoices(parsed.choices);
        setPrefilledFrom({
          manufacturer: row.manufacturer,
          partNumber: row.part_number,
        });
        setParseWarnings(parsed.warnings);
      }
      // Scroll the configurator into view — it's above the records
      // table by default; on long pages the user may have scrolled
      // past it.
      configuratorRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      });
    },
    [],
  );

  // Fetch both subtypes' records on mount.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [la, ec] = await Promise.all([
          apiClient.listProducts('linear_actuator'),
          apiClient.listProducts('electric_cylinder'),
        ]);
        if (cancelled) return;
        setData({
          linear_actuator: la.map(toRow).filter((r): r is ActuatorRow => r !== null),
          electric_cylinder: ec.map(toRow).filter((r): r is ActuatorRow => r !== null),
        });
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load actuators.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = data[subtype];

  return (
    <div className="actuator-page">
      <header className="actuator-page__hero">
        <div className="actuator-page__eyebrow">Supercategory</div>
        <h1 className="actuator-page__title">{supercat.display_name}</h1>
        <p className="actuator-page__subhead">{supercat.description}</p>
        <p className="actuator-page__subhead actuator-page__subhead--question">
          {supercat.selection_question}
        </p>
      </header>

      <div className="actuator-page__tabs" role="tablist">
        {(Object.keys(SUBTYPE_LABELS) as Subtype[]).map((st) => (
          <button
            key={st}
            role="tab"
            aria-selected={subtype === st}
            className={
              'actuator-page__tab' +
              (subtype === st ? ' actuator-page__tab--active' : '')
            }
            onClick={() => setSubtype(st)}
          >
            {SUBTYPE_LABELS[st].name}
            <span className="actuator-page__tab-count">{data[st].length}</span>
          </button>
        ))}
      </div>

      <p className="actuator-page__panel-body" style={{ marginBottom: '1rem' }}>
        {SUBTYPE_LABELS[subtype].blurb}
      </p>

      <div ref={configuratorRef as React.RefObject<HTMLDivElement>}>
        <ConfiguratorPanel
          templates={templates}
          activeKey={activeKey}
          onActiveKeyChange={handleActiveKeyChange}
          choices={choices}
          onChoicesChange={handleChoicesChange}
          prefilledFrom={prefilledFrom}
          parseWarnings={parseWarnings}
        />
      </div>

      <section className="actuator-page__panel">
        <h2 className="actuator-page__panel-title">
          {SUBTYPE_LABELS[subtype].name} records
          <span className="actuator-page__panel-subtitle">
            {' '}
            — click a row to reverse-parse into the configurator
          </span>
        </h2>
        {loading && <div className="actuator-page__loading">Loading…</div>}
        {error && (
          <div className="actuator-page__empty" style={{ color: 'var(--danger)' }}>
            {error}
          </div>
        )}
        {!loading && !error && rows.length === 0 && (
          <div className="actuator-page__empty">
            No {SUBTYPE_LABELS[subtype].name.toLowerCase()} records yet.
            Ingest a vendor catalog to populate this view.
          </div>
        )}
        {!loading && !error && rows.length > 0 && (
          <table className="actuator-page__product-table">
            <thead>
              <tr>
                <th>Manufacturer</th>
                <th>Family</th>
                <th>Part #</th>
                <th>Stroke</th>
                <th>Max push force</th>
                <th>Drive</th>
                <th>Configurator</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const hasTemplate = !!findTemplate(r.manufacturer, r.series);
                return (
                  <tr
                    key={r.product_id}
                    className={
                      hasTemplate ? 'actuator-page__row--clickable' : ''
                    }
                    onClick={hasTemplate ? () => handleRecordClick(r) : undefined}
                  >
                    <td>{r.manufacturer}</td>
                    <td>{r.series ?? r.product_name}</td>
                    <td>{r.part_number ?? '—'}</td>
                    <td>{formatValueUnit(r.stroke)}</td>
                    <td>{formatValueUnit(r.max_push_force)}</td>
                    <td>{r.drive ?? '—'}</td>
                    <td>
                      {hasTemplate ? (
                        <span className="actuator-page__has-template">parse →</span>
                      ) : (
                        <span className="actuator-page__no-template">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
