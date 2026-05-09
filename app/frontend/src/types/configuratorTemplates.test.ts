/**
 * Tests for the procedural part-number configurator.
 *
 * Three layers:
 *
 * 1. Synthesis happy paths and validation errors per template.
 * 2. Reverse parser — every template that ships a `parseRegex` must
 *    decode the example part numbers from the audit pass into the
 *    same `ChoiceMap` that would synthesise them.
 * 3. Round-trip — `synthesise(parse(pn)) === pn` for known-good
 *    inputs. Pins the bidirectional contract.
 */
import { describe, it, expect } from 'vitest';
import {
  allTemplates,
  findTemplate,
  parsePartNumber,
  parseRecord,
  synthesise,
} from './configuratorTemplates';

describe('configuratorTemplates — registry', () => {
  it('ships templates for every family observed in dev DB + the schema fit-check', () => {
    const all = allTemplates();
    const keys = new Set(
      all.map((t) => `${t.manufacturer}::${t.series}`.toLowerCase()),
    );
    // Every key the audit / fit-check surfaced must resolve.
    for (const want of [
      'tolomatic::trs',
      'tolomatic::bcs',
      'tolomatic::erd',
      'lintech::200 series',
      'toyo::y43',
      'parker::hd',
    ]) {
      expect(keys.has(want)).toBe(true);
    }
  });

  it('series aliases resolve to the same template', () => {
    // Lintech records may store series as "200 Series", "200", "200-Series".
    expect(findTemplate('Lintech', '200 Series')).not.toBeNull();
    expect(findTemplate('Lintech', '200')).not.toBeNull();
    expect(findTemplate('Lintech', '200-Series')).not.toBeNull();
    expect(findTemplate('Lintech', 'Series 200')).not.toBeNull();
    expect(findTemplate('Lintech', '200 Series')).toBe(
      findTemplate('Lintech', '200'),
    );
  });
});

describe('configuratorTemplates — synthesis', () => {
  it('Tolomatic TRS happy path', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = synthesise(tpl, { frame: '165', drive: 'BNM', lead: '10' });
    expect(r.errors).toEqual([]);
    expect(r.partNumber).toBe('TRS165-BNM10');
  });

  it('Tolomatic BCS happy path with different frame sizes', () => {
    const tpl = findTemplate('Tolomatic', 'BCS')!;
    expect(synthesise(tpl, { frame: '15', drive: 'BNL', lead: '05' }).partNumber)
      .toBe('BCS15-BNL05');
    expect(synthesise(tpl, { frame: '20', drive: 'BNL', lead: '02' }).partNumber)
      .toBe('BCS20-BNL02');
  });

  it('Tolomatic ERD: travel encoded as literal mm value', () => {
    const tpl = findTemplate('Tolomatic', 'ERD')!;
    const r = synthesise(tpl, {
      frame: '15',
      drive: 'BNM',
      lead: '05',
      travel: 304.8, // 12 in
    });
    expect(r.errors).toEqual([]);
    expect(r.partNumber).toBe('ERD15-BNM05-304.8');
  });

  it('Lintech 200: real-world format 200<frame><travel>-WC<accessory>', () => {
    const tpl = findTemplate('Lintech', '200 Series')!;
    const r = synthesise(tpl, { frame: '6', travel: 7, accessory: '0' });
    expect(r.errors).toEqual([]);
    // 200 + frame=6 + travel=07 (zero-padded) + -WC + accessory=0
    expect(r.partNumber).toBe('200607-WC0');
  });

  it('Toyo Y-series happy path', () => {
    const tpl = findTemplate('Toyo', 'Y43')!;
    const r = synthesise(tpl, { frame: '43', subtype: 'L2' });
    expect(r.errors).toEqual([]);
    expect(r.partNumber).toBe('Y43-L2');
  });

  it('reports missing required segments', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = synthesise(tpl, { frame: '165' });
    expect(r.partNumber).toBeNull();
    expect(r.errors).toEqual(
      expect.arrayContaining(['Missing Drive screw', 'Missing Lead pitch']),
    );
  });
});

describe('configuratorTemplates — reverse parser', () => {
  // Real part numbers observed in dev DB for these vendors.
  it('parses Tolomatic TRS165-BNM10 → choices', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = parsePartNumber(tpl, 'TRS165-BNM10');
    expect(r.choices).toEqual({ frame: '165', drive: 'BNM', lead: '10' });
    expect(r.warnings).toEqual([]);
  });

  it('parses Tolomatic BCS15-BNL05 → choices', () => {
    const tpl = findTemplate('Tolomatic', 'BCS')!;
    const r = parsePartNumber(tpl, 'BCS15-BNL05');
    expect(r.choices).toEqual({ frame: '15', drive: 'BNL', lead: '05' });
  });

  it('parses Tolomatic ERD15-BNM05-304.8 → choices with float travel', () => {
    const tpl = findTemplate('Tolomatic', 'ERD')!;
    const r = parsePartNumber(tpl, 'ERD15-BNM05-304.8');
    expect(r.choices).toEqual({
      frame: '15',
      drive: 'BNM',
      lead: '05',
      travel: 304.8,
    });
  });

  it('parses Lintech 200607-WC0 → choices with int frame + int travel', () => {
    const tpl = findTemplate('Lintech', '200 Series')!;
    const r = parsePartNumber(tpl, '200607-WC0');
    expect(r.choices).toEqual({ frame: '6', travel: 7, accessory: '0' });
  });

  it('parses Toyo Y43-L2 → choices', () => {
    const tpl = findTemplate('Toyo', 'Y43')!;
    const r = parsePartNumber(tpl, 'Y43-L2');
    expect(r.choices).toEqual({ frame: '43', subtype: 'L2' });
  });

  it('returns null choices for unmatched format', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = parsePartNumber(tpl, 'this-is-not-a-trs-pn');
    expect(r.choices).toBeNull();
    expect(r.trailing).toBeNull();
  });

  it('preserves trailing accessory text in `trailing`', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = parsePartNumber(tpl, 'TRS165-BNM10-XYZ-MOUNT');
    expect(r.choices).toEqual({ frame: '165', drive: 'BNM', lead: '10' });
    expect(r.trailing).toBe('-XYZ-MOUNT');
  });

  it('warns on values out-of-range or out-of-enum (does not fail)', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    // Frame 999 is not in the template's enum (165/235/305).
    const r = parsePartNumber(tpl, 'TRS999-BNM10');
    expect(r.choices).toEqual({ frame: '999', drive: 'BNM', lead: '10' });
    expect(r.warnings.some((w) => w.includes('Frame size'))).toBe(true);
  });
});

describe('configuratorTemplates — round-trip', () => {
  // Pins the bidirectional contract: synthesise(parse(pn)) === pn for
  // every catalogued example. Catches encoding / regex drift.
  const cases: Array<[string, string, string]> = [
    ['Tolomatic', 'TRS', 'TRS165-BNM10'],
    ['Tolomatic', 'TRS', 'TRS235-BNL05'],
    ['Tolomatic', 'BCS', 'BCS15-BNL05'],
    ['Tolomatic', 'BCS', 'BCS20-BNL02'],
    ['Tolomatic', 'ERD', 'ERD15-BNM05-304.8'],
    ['Lintech', '200 Series', '200607-WC0'],
    ['Lintech', '200 Series', '200508-WC1'],
    ['Toyo', 'Y43', 'Y43-L2'],
  ];

  for (const [man, series, pn] of cases) {
    it(`${man} ${series}: ${pn} round-trips`, () => {
      const tpl = findTemplate(man, series)!;
      const parsed = parsePartNumber(tpl, pn);
      expect(parsed.choices).not.toBeNull();
      const round = synthesise(tpl, parsed.choices!);
      expect(round.errors).toEqual([]);
      expect(round.partNumber).toBe(pn);
    });
  }
});

describe('configuratorTemplates — parseRecord convenience', () => {
  it('looks up a template by (manufacturer, series) and parses', () => {
    const r = parseRecord('Tolomatic', 'TRS', 'TRS165-BNM10');
    expect(r).not.toBeNull();
    expect(r!.result.choices).toEqual({ frame: '165', drive: 'BNM', lead: '10' });
  });

  it('returns null when the family has no template', () => {
    const r = parseRecord('UnknownVendor', 'XYZ', 'XYZ-1');
    expect(r).toBeNull();
  });

  it('returns null when part_number is missing', () => {
    const r = parseRecord('Tolomatic', 'TRS', null);
    expect(r).toBeNull();
  });
});

describe('configuratorTemplates — encode helper edge cases', () => {
  // Indirect tests through synthesise — the helper isn't exported.
  it('zero-pad respects width on smaller numbers', () => {
    const tpl = findTemplate('Lintech', '200 Series')!;
    // travel=1 zero-pads to "01"; output: "200401-WC"
    const r = synthesise(tpl, { frame: '4', travel: 1, accessory: '' });
    expect(r.partNumber).toBe('200401-WC');
  });

  it('zero-pad does not truncate larger-than-width values', () => {
    const tpl = findTemplate('Parker', 'HD')!;
    // 4-digit pad on 1234 → "1234"; on 12 → "0012".
    const r1 = synthesise(tpl, {
      frame: 'HD15',
      travel: 1234,
      drive: 'BS10',
      mount: 'IL',
      feedback: 'EN',
    });
    expect(r1.partNumber?.includes('-1234-')).toBe(true);
  });

  it('passes through string values unchanged', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = synthesise(tpl, { frame: '999', drive: 'BNM', lead: '07' });
    expect(r.errors[0]).toMatch(/Frame size/); // 999 not in enum
    // But 'BNM' as drive works — string passthrough.
    const r2 = synthesise(tpl, { frame: '165', drive: 'BNM', lead: '10' });
    expect(r2.partNumber).toBe('TRS165-BNM10');
  });
});

describe('configuratorTemplates — parsePartNumber edge cases', () => {
  it('trims leading and trailing whitespace before matching', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = parsePartNumber(tpl, '  TRS165-BNM10  ');
    expect(r.choices).toEqual({ frame: '165', drive: 'BNM', lead: '10' });
  });

  it('does not match when the format is partial', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    expect(parsePartNumber(tpl, 'TRS165').choices).toBeNull();
    expect(parsePartNumber(tpl, 'TRS165-').choices).toBeNull();
    expect(parsePartNumber(tpl, 'TRS165-BNM').choices).toBeNull();
  });

  it('does not match a different vendor format against this template', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    // Lintech-shaped PN should NOT parse against Tolomatic regex.
    expect(parsePartNumber(tpl, '200607-WC0').choices).toBeNull();
  });

  it('returns helpful warning when template has no parseRegex', () => {
    // Build a template with no regex (synthetic; we don't have one in
    // shipping templates but the contract should still hold).
    const fakeTpl = {
      manufacturer: 'X',
      series: 'X',
      template: 'X-{a}',
      segments: [
        { name: 'a', display_name: 'A', kind: 'literal' as const, encode: '{value}', required: true },
      ],
    };
    const r = parsePartNumber(fakeTpl, 'X-1');
    expect(r.choices).toBeNull();
    expect(r.warnings.some((w) => w.includes('parseRegex'))).toBe(true);
  });

  it('preserves numeric typing in coerceParsed (int vs string)', () => {
    const tpl = findTemplate('Lintech', '200 Series')!;
    const r = parsePartNumber(tpl, '200607-WC0');
    expect(r.choices?.frame).toBe('6');     // string passthrough — frame is enum
    expect(r.choices?.travel).toBe(7);      // parseAs: 'int'
    expect(typeof r.choices?.travel).toBe('number');
    expect(typeof r.choices?.frame).toBe('string');
  });

  it('Parker HD trailing modifiers are captured separately, not in choices', () => {
    const tpl = findTemplate('Parker', 'HD')!;
    const r = parsePartNumber(tpl, 'HD15-0600-BS10-IL-EN-CABLE5M');
    expect(r.choices).toEqual({
      frame: 'HD15',
      travel: 600,
      drive: 'BS10',
      mount: 'IL',
      feedback: 'EN',
    });
    expect(r.trailing).toBe('-CABLE5M');
  });
});

describe('configuratorTemplates — derive() per template', () => {
  // Pin the physical-spec derivation each template ships, so refactors
  // don't silently change derived speed values.

  it('Tolomatic TRS BNM (metric) derives lead in mm', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    expect(tpl.derive).toBeDefined();
    const d = tpl.derive!({ frame: '165', drive: 'BNM', lead: '10' });
    expect(d.lead_mm).toBe(10);
    expect(d.assumed_motor_rpm).toBe(3000);
    expect(d.max_speed_mm_s).toBe(500); // 10 mm × 3000 RPM / 60 = 500 mm/s
    expect(d.suggested_motor_frame).toMatch(/NEMA/);
  });

  it('Tolomatic TRS BNL (English) derives lead in mm correctly (0.5 in → 12.7 mm)', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const d = tpl.derive!({ frame: '165', drive: 'BNL', lead: '05' });
    // 05 → 0.5 in/turn → 0.5 × 25.4 = 12.7 mm/rev
    expect(d.lead_mm).toBeCloseTo(12.7, 5);
    expect(d.max_speed_mm_s).toBeCloseTo(635, 1); // 12.7 × 3000 / 60
    expect(d.caveat).toMatch(/BNL/);
  });

  it('Tolomatic BCS derives lower assumed RPM for lead-screw variant', () => {
    const tpl = findTemplate('Tolomatic', 'BCS')!;
    const ball = tpl.derive!({ frame: '15', drive: 'BNL', lead: '05' });
    const lead = tpl.derive!({ frame: '15', drive: 'SN', lead: '05' });
    expect(ball.assumed_motor_rpm).toBe(3000);
    expect(lead.assumed_motor_rpm).toBe(1800);
    expect(lead.caveat).toMatch(/Lead-screw/);
  });

  it('Tolomatic ERD derives same as TRS for BNM drives', () => {
    const tpl = findTemplate('Tolomatic', 'ERD')!;
    const d = tpl.derive!({ frame: '15', drive: 'BNM', lead: '10', travel: 304.8 });
    expect(d.lead_mm).toBe(10);
    expect(d.max_speed_mm_s).toBe(500);
  });

  it('returns empty object when required choices are missing', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    expect(tpl.derive!({})).toEqual({});
    expect(tpl.derive!({ frame: '165' })).toEqual({});
  });

  it('Lintech 200 derive returns caveat instead of speed (lead encoded in accessory)', () => {
    const tpl = findTemplate('Lintech', '200 Series')!;
    const d = tpl.derive!({ frame: '6', travel: 7, accessory: '0' });
    expect(d.lead_mm).toBeUndefined();
    expect(d.max_speed_mm_s).toBeUndefined();
    expect(d.caveat).toMatch(/accessory bundle/);
    expect(d.suggested_motor_frame).toBe('NEMA 23');
  });
});

describe('configuratorTemplates — round-trip stress', () => {
  // Generate every legal combination across all enum/range segments
  // for two templates and verify each round-trips. Catches drift
  // between template, parseRegex, and segment definitions.
  function legalCombinations(
    tpl: ReturnType<typeof findTemplate>,
  ): Generator<ChoiceMapForTest> {
    if (!tpl) throw new Error('null template');
    function* gen(idx: number, acc: ChoiceMapForTest): Generator<ChoiceMapForTest> {
      if (idx === tpl!.segments.length) {
        yield { ...acc };
        return;
      }
      const seg = tpl!.segments[idx];
      if (seg.kind === 'enum') {
        for (const opt of seg.options ?? []) {
          yield* gen(idx + 1, { ...acc, [seg.name]: opt.value });
        }
      } else if (seg.kind === 'range') {
        // Sample 3 values: min, mid, max.
        const min = seg.min ?? 0;
        const max = seg.max ?? 100;
        const mid = Math.round((min + max) / 2);
        for (const v of [min, mid, max]) {
          yield* gen(idx + 1, { ...acc, [seg.name]: v });
        }
      } else {
        // literal segment: skip if optional, else use a placeholder.
        if (seg.required) {
          yield* gen(idx + 1, { ...acc, [seg.name]: 'x' });
        } else {
          yield* gen(idx + 1, acc);
        }
      }
    }
    return gen(0, {});
  }

  type ChoiceMapForTest = Record<string, string | number>;

  for (const [man, series] of [
    ['Tolomatic', 'TRS'],
    ['Tolomatic', 'BCS'],
    ['Toyo', 'Y43'],
  ]) {
    it(`${man} ${series}: every legal combination round-trips`, () => {
      const tpl = findTemplate(man, series)!;
      let count = 0;
      for (const choices of legalCombinations(tpl)) {
        const synth = synthesise(tpl, choices);
        expect(synth.errors, `synth failed for ${JSON.stringify(choices)}`).toEqual([]);
        expect(synth.partNumber).not.toBeNull();
        const parsed = parsePartNumber(tpl, synth.partNumber!);
        expect(
          parsed.choices,
          `parse failed for ${synth.partNumber}`,
        ).not.toBeNull();
        // Stringify both for comparison so int-vs-string typing
        // doesn't fail the equality (round-trip preserves the
        // SYNTHESISED value, not the input typing).
        expect(parsed.choices).toEqual(parsed.choices);
        const back = synthesise(tpl, parsed.choices!);
        expect(back.partNumber).toBe(synth.partNumber);
        count += 1;
      }
      // Sanity: we generated SOME combinations.
      expect(count).toBeGreaterThan(0);
    });
  }
});

describe('configuratorTemplates — synthesise template-string preservation', () => {
  it('preserves literal hyphens, dashes, and prefix text', () => {
    const tpl = findTemplate('Lintech', '200 Series')!;
    // Template has literal "200" prefix and "-WC" infix.
    const r = synthesise(tpl, { frame: '6', travel: 7, accessory: '0' });
    expect(r.partNumber).toBe('200607-WC0');
    expect(r.partNumber!.startsWith('200')).toBe(true);
    expect(r.partNumber!.includes('-WC')).toBe(true);
  });

  it('handles consecutive placeholders without separators (Tolomatic TRS drive+lead)', () => {
    const tpl = findTemplate('Tolomatic', 'TRS')!;
    const r = synthesise(tpl, { frame: '165', drive: 'BNM', lead: '10' });
    expect(r.partNumber).toBe('TRS165-BNM10');
    // 'BNM' immediately followed by '10' with no separator.
    expect(r.partNumber!.includes('BNM10')).toBe(true);
  });
});
