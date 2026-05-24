/**
 * Tests for filter and sort logic
 */

import { describe, it, expect } from 'vitest';
import {
  applyFilters,
  sortProducts,
  FilterCriterion,
  SortConfig,
  deriveAttributesFromRecords,
  mergeAttributesByKey,
  AttributeMetadata,
} from './filters';
import {
  COLUMN_ORDER,
  orderColumnAttributes,
  computeVisibleColumnAttributes,
} from './columnOrder';
import { Product } from './models';

describe('Filter Logic', () => {
  const mockProducts: Product[] = [
    {
      product_id: '1',
      product_type: 'motor',
      manufacturer: 'ACME',
      part_number: 'AC-100',
      product_name: 'AC-100',
      rated_power: { value: 100, unit: 'W' },
    },
    {
      product_id: '2',
      product_type: 'motor',
      manufacturer: 'ACME',
      part_number: 'AC-200',
      product_name: 'AC-200',
      rated_power: { value: 200, unit: 'W' },
    },
    {
      product_id: '3',
      product_type: 'motor',
      manufacturer: 'Beta Corp',
      part_number: 'BC-150',
      product_name: 'BC-150',
      rated_power: { value: 150, unit: 'W' },
    },
  ];

  describe('applyFilters', () => {
    it('should return all products when no filters applied', () => {
      const result = applyFilters(mockProducts, []);
      expect(result).toEqual(mockProducts);
    });

    it('should filter by exact string match (include mode)', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'manufacturer',
          mode: 'include',
          value: 'ACME',
          displayName: 'Manufacturer',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(2);
      expect(result.every(p => p.manufacturer === 'ACME')).toBe(true);
    });

    it('should filter by partial string match (case insensitive)', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'manufacturer',
          mode: 'include',
          value: 'acme',
          displayName: 'Manufacturer',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(2);
    });

    it('should exclude products (exclude mode)', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'manufacturer',
          mode: 'exclude',
          value: 'ACME',
          displayName: 'Manufacturer',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(1);
      expect(result[0].manufacturer).toBe('Beta Corp');
    });

    it('should filter by numeric value with equals operator', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'rated_power',
          mode: 'include',
          value: 100,
          operator: '=',
          displayName: 'Rated Power',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(1);
      expect(result[0].product_id).toBe('1');
    });

    it('should filter by numeric value with greater than operator', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'rated_power',
          mode: 'include',
          value: 150,
          operator: '>',
          displayName: 'Rated Power',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(1);
      expect(result[0].product_id).toBe('2');
    });

    it('should filter by numeric value with less than operator', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'rated_power',
          mode: 'include',
          value: 150,
          operator: '<',
          displayName: 'Rated Power',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(1);
      expect(result[0].product_id).toBe('1');
    });

    it('should handle multiple filters (AND logic)', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'manufacturer',
          mode: 'include',
          value: 'ACME',
          displayName: 'Manufacturer',
        },
        {
          attribute: 'rated_power',
          mode: 'include',
          value: 150,
          operator: '>',
          displayName: 'Rated Power',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(1);
      expect(result[0].product_id).toBe('2');
    });

    it('should ignore neutral mode filters', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'manufacturer',
          mode: 'neutral',
          value: 'ACME',
          displayName: 'Manufacturer',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toEqual(mockProducts);
    });

    it('should handle missing attributes gracefully', () => {
      const filters: FilterCriterion[] = [
        {
          attribute: 'nonexistent',
          mode: 'include',
          value: 'test',
          displayName: 'Nonexistent',
        },
      ];
      const result = applyFilters(mockProducts, filters);
      expect(result).toHaveLength(0);
    });
  });
});

describe('Sort Logic', () => {
  const mockProducts: Product[] = [
    {
      product_id: 'abc10',
      product_type: 'motor',
      manufacturer: 'ACME',
      part_number: 'AC-100',
      product_name: 'AC-100',
      rated_power: { value: 100, unit: 'W' },
    },
    {
      product_id: 'abc2',
      product_type: 'motor',
      manufacturer: 'ACME',
      part_number: 'AC-200',
      product_name: 'AC-200',
      rated_power: { value: 200, unit: 'W' },
    },
    {
      product_id: 'abc3',
      product_type: 'motor',
      manufacturer: 'Beta Corp',
      part_number: 'BC-150',
      product_name: 'BC-150',
      rated_power: { value: 150, unit: 'W' },
    },
  ];

  describe('sortProducts', () => {
    it('should return unsorted products when sort is null', () => {
      const result = sortProducts(mockProducts, null);
      expect(result).toEqual(mockProducts);
    });

    it('should sort by string alphabetically (ascending)', () => {
      const sort: SortConfig = {
        attribute: 'manufacturer',
        direction: 'asc',
        displayName: 'Manufacturer',
      };
      const result = sortProducts(mockProducts, sort);
      expect(result[0].manufacturer).toBe('ACME');
      expect(result[2].manufacturer).toBe('Beta Corp');
    });

    it('should sort by string alphabetically (descending)', () => {
      const sort: SortConfig = {
        attribute: 'manufacturer',
        direction: 'desc',
        displayName: 'Manufacturer',
      };
      const result = sortProducts(mockProducts, sort);
      expect(result[0].manufacturer).toBe('Beta Corp');
      expect(result[2].manufacturer).toBe('ACME');
    });

    it('should sort by numeric value (ascending)', () => {
      const sort: SortConfig = {
        attribute: 'rated_power',
        direction: 'asc',
        displayName: 'Rated Power',
      };
      const result = sortProducts(mockProducts, sort);
      expect(result[0].product_id).toBe('abc10');
      expect(result[1].product_id).toBe('abc3');
      expect(result[2].product_id).toBe('abc2');
    });

    it('should sort by numeric value (descending)', () => {
      const sort: SortConfig = {
        attribute: 'rated_power',
        direction: 'desc',
        displayName: 'Rated Power',
      };
      const result = sortProducts(mockProducts, sort);
      expect(result[0].product_id).toBe('abc2');
      expect(result[1].product_id).toBe('abc3');
      expect(result[2].product_id).toBe('abc10');
    });

    it('should use natural alphanumeric sorting (abc2 < abc10)', () => {
      const sort: SortConfig = {
        attribute: 'product_id',
        direction: 'asc',
        displayName: 'Product ID',
      };
      const result = sortProducts(mockProducts, sort);
      expect(result[0].product_id).toBe('abc2');
      expect(result[1].product_id).toBe('abc3');
      expect(result[2].product_id).toBe('abc10');
    });

    it('should handle multi-level sorting', () => {
      const productsWithDuplicates: Product[] = [
        {
          product_id: '1',
          product_type: 'motor',
          manufacturer: 'ACME',
          part_number: 'AC-200',
          product_name: 'AC-200',
          rated_power: { value: 100, unit: 'W' },
        },
        {
          product_id: '2',
          product_type: 'motor',
          manufacturer: 'ACME',
          part_number: 'AC-100',
          product_name: 'AC-100',
          rated_power: { value: 100, unit: 'W' },
        },
        {
          product_id: '3',
          product_type: 'motor',
          manufacturer: 'Beta Corp',
          part_number: 'BC-150',
          product_name: 'BC-150',
          rated_power: { value: 200, unit: 'W' },
        },
      ];

      const sorts: SortConfig[] = [
        {
          attribute: 'rated_power',
          direction: 'asc',
          displayName: 'Rated Power',
        },
        {
          attribute: 'part_number',
          direction: 'asc',
          displayName: 'Part Number',
        },
      ];

      const result = sortProducts(productsWithDuplicates, sorts);
      
      // First sorted by rated_power (both 100W come first)
      // Then sorted by part_number (AC-100 before AC-200)
      expect(result[0].part_number).toBe('AC-100');
      expect(result[1].part_number).toBe('AC-200');
      expect(result[2].part_number).toBe('BC-150');
    });

    it('should handle null values (push to end)', () => {
      const productsWithNull: Product[] = [
        {
          product_id: '1',
          product_type: 'motor',
          manufacturer: 'ACME',
          part_number: 'AC-100',
          product_name: 'AC-100',
        },
        {
          product_id: '2',
          product_type: 'motor',
          manufacturer: 'Beta Corp',
          part_number: 'BC-150',
          product_name: 'BC-150',
          rated_power: { value: 150, unit: 'W' },
        },
      ];

      const sort: SortConfig = {
        attribute: 'rated_power',
        direction: 'asc',
        displayName: 'Rated Power',
      };

      const result = sortProducts(productsWithNull, sort);
      expect(result[0].product_id).toBe('2'); // Has value
      expect(result[1].product_id).toBe('1'); // Null value goes last
    });

    it('should not mutate original array', () => {
      const original = [...mockProducts];
      const sort: SortConfig = {
        attribute: 'manufacturer',
        direction: 'desc',
        displayName: 'Manufacturer',
      };
      
      sortProducts(mockProducts, sort);
      expect(mockProducts).toEqual(original);
    });
  });
});

describe('deriveAttributesFromRecords', () => {
  const contactorRecords = [
    {
      product_id: 'abc',
      product_type: 'contactor',
      manufacturer: 'Mitsubishi',
      part_number: 'S-T10',
      product_name: 'S-T10',
      series: 'MS-T',
      frame_size: 'T10',
      rated_insulation_voltage: { value: 690, unit: 'V' },
      operating_temp: { min: -5, max: 40, unit: '°C' },
      coil_voltage_designations: ['AC100V', 'AC200V'],
      number_of_poles: 3,
      iec_rail_mounting: true,
      datasheet_url: 'https://example.com/x.pdf',
      pages: [1, 2, 3],
    },
  ];

  it('derives a ValueUnit key as type=object with unit', () => {
    const attrs = deriveAttributesFromRecords(contactorRecords, 'contactor');
    const voltage = attrs.find(a => a.key === 'rated_insulation_voltage');
    expect(voltage).toBeDefined();
    expect(voltage!.type).toBe('object');
    expect(voltage!.nested).toBe(true);
    expect(voltage!.unit).toBe('V');
    expect(voltage!.displayName).toBe('Rated Insulation Voltage');
  });

  it('derives a MinMaxUnit key as type=range with unit', () => {
    const attrs = deriveAttributesFromRecords(contactorRecords, 'contactor');
    const temp = attrs.find(a => a.key === 'operating_temp');
    expect(temp).toBeDefined();
    expect(temp!.type).toBe('range');
    expect(temp!.nested).toBe(true);
    expect(temp!.unit).toBe('°C');
  });

  it('derives primitives to their correct type', () => {
    const attrs = deriveAttributesFromRecords(contactorRecords, 'contactor');
    expect(attrs.find(a => a.key === 'part_number')?.type).toBe('string');
    expect(attrs.find(a => a.key === 'number_of_poles')?.type).toBe('number');
    expect(attrs.find(a => a.key === 'iec_rail_mounting')?.type).toBe('boolean');
    expect(attrs.find(a => a.key === 'coil_voltage_designations')?.type).toBe('array');
  });

  it('excludes identity and bookkeeping keys', () => {
    const attrs = deriveAttributesFromRecords(contactorRecords, 'contactor');
    const keys = attrs.map(a => a.key);
    expect(keys).not.toContain('PK');
    expect(keys).not.toContain('SK');
    expect(keys).not.toContain('product_id');
    expect(keys).not.toContain('product_type');
    expect(keys).not.toContain('datasheet_url');
    expect(keys).not.toContain('pages');
  });

  it('tags derived attributes with the provided productType', () => {
    const attrs = deriveAttributesFromRecords(contactorRecords, 'contactor');
    for (const attr of attrs) {
      expect(attr.applicableTypes).toEqual(['contactor']);
    }
  });

  it('falls through null/undefined values to later records with real values', () => {
    const records = [
      { product_type: 'contactor', frame_size: null },
      { product_type: 'contactor', frame_size: 'T50' },
    ];
    const attrs = deriveAttributesFromRecords(records, 'contactor');
    expect(attrs.find(a => a.key === 'frame_size')?.type).toBe('string');
  });

  it('returns empty for null productType or empty records', () => {
    expect(deriveAttributesFromRecords([], 'contactor')).toEqual([]);
    expect(deriveAttributesFromRecords(contactorRecords, null)).toEqual([]);
    expect(deriveAttributesFromRecords(contactorRecords, 'all')).toEqual([]);
  });
});

describe('mergeAttributesByKey', () => {
  const staticAttr: AttributeMetadata = {
    key: 'rated_voltage',
    displayName: 'Rated Voltage',
    type: 'range',
    applicableTypes: ['motor'],
    nested: true,
    unit: 'V',
  };
  const derivedSameKey: AttributeMetadata = {
    key: 'rated_voltage',
    displayName: 'Rated Voltage',
    type: 'object',
    applicableTypes: ['motor'],
    nested: true,
    unit: 'V',
  };
  const derivedNewKey: AttributeMetadata = {
    key: 'weirdfield',
    displayName: 'Weirdfield',
    type: 'string',
    applicableTypes: ['motor'],
  };

  it('prefers primary on key collision so static metadata wins', () => {
    const merged = mergeAttributesByKey([staticAttr], [derivedSameKey]);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toBe(staticAttr);
  });

  it('appends secondary entries whose keys are missing from primary', () => {
    const merged = mergeAttributesByKey([staticAttr], [derivedSameKey, derivedNewKey]);
    expect(merged).toHaveLength(2);
    expect(merged[1]).toBe(derivedNewKey);
  });

  it('returns just the derived list when primary is empty', () => {
    const merged = mergeAttributesByKey([], [derivedSameKey, derivedNewKey]);
    expect(merged).toEqual([derivedSameKey, derivedNewKey]);
  });
});

describe('orderColumnAttributes', () => {
  const make = (key: string, displayName?: string): AttributeMetadata => ({
    key,
    displayName: displayName ?? key,
    type: 'string',
    applicableTypes: ['motor'],
  });

  it('falls back to alphabetical when COLUMN_ORDER for the type is empty', () => {
    // Mutate gearhead's order to empty for this test (restore after) — every
    // real type now seeds at least 'manufacturer' as its lead column, so
    // there is no naturally-empty type to use here.
    const original = COLUMN_ORDER.gearhead ?? [];
    COLUMN_ORDER.gearhead = [];
    try {
      const attrs = [make('z_field', 'Z Field'), make('a_field', 'A Field'), make('m_field', 'M Field')];
      const ordered = orderColumnAttributes(attrs, 'gearhead');
      expect(ordered.map(a => a.key)).toEqual(['a_field', 'm_field', 'z_field']);
    } finally {
      COLUMN_ORDER.gearhead = original;
    }
  });

  it('puts authored-order keys first in declared order, then unlisted alphabetical', () => {
    // Temporarily seed motor order via mutation — the export is a const
    // object reference, so we restore at end.
    const original = COLUMN_ORDER.motor ?? [];
    COLUMN_ORDER.motor = ['rated_power', 'rated_torque'];
    try {
      const attrs = [
        make('weight', 'Weight'),
        make('rated_torque', 'Rated Torque'),
        make('manufacturer', 'Manufacturer'),
        make('rated_power', 'Rated Power'),
      ];
      const ordered = orderColumnAttributes(attrs, 'motor');
      expect(ordered.map(a => a.key)).toEqual([
        'rated_power',
        'rated_torque',
        'manufacturer',
        'weight',
      ]);
    } finally {
      COLUMN_ORDER.motor = original;
    }
  });

  it('returns alphabetical for productType=null and productType=all', () => {
    const attrs = [make('b'), make('a')];
    expect(orderColumnAttributes(attrs, null).map(a => a.key)).toEqual(['a', 'b']);
    expect(orderColumnAttributes(attrs, 'all').map(a => a.key)).toEqual(['a', 'b']);
  });

  it('does not mutate the input array', () => {
    const attrs = [make('b'), make('a')];
    const snapshot = attrs.map(a => a.key);
    orderColumnAttributes(attrs, 'motor');
    expect(attrs.map(a => a.key)).toEqual(snapshot);
  });
});

describe('computeVisibleColumnAttributes', () => {
  // Helpers — terser construction for the tests below.
  const nested = (key: string, displayName?: string): AttributeMetadata => ({
    key,
    displayName: displayName ?? key,
    type: 'object',
    applicableTypes: ['motor'],
    nested: true,
  });
  const expert = (
    key: string,
    defaultVisible: boolean,
    displayName?: string,
  ): AttributeMetadata => ({
    key,
    displayName: displayName ?? key,
    type: 'string',
    applicableTypes: ['motor'],
    defaultVisible,
  });
  const bare = (key: string, displayName?: string): AttributeMetadata => ({
    key,
    displayName: displayName ?? key,
    type: 'string',
    applicableTypes: ['motor'],
  });

  it('shows nested attrs by default, hides bare attrs by default', () => {
    const attrs = [nested('rated_power'), bare('frame_size')];
    const out = computeVisibleColumnAttributes(attrs, [], [], 10);
    expect(out.map(a => a.key)).toEqual(['rated_power']);
  });

  it('respects defaultVisible=true (expert override on a bare type)', () => {
    const attrs = [bare('frame_size'), expert('manufacturer', true)];
    const out = computeVisibleColumnAttributes(attrs, [], [], 10);
    expect(out.map(a => a.key)).toEqual(['manufacturer']);
  });

  it('respects defaultVisible=false (expert override hides a nested attr)', () => {
    const attrs = [
      nested('rated_power'),
      { ...nested('peak_current'), defaultVisible: false },
    ];
    const out = computeVisibleColumnAttributes(attrs, [], [], 10);
    expect(out.map(a => a.key)).toEqual(['rated_power']);
  });

  it('user-hidden keys are removed from the visible set', () => {
    const attrs = [nested('rated_power'), nested('rated_torque')];
    const out = computeVisibleColumnAttributes(attrs, ['rated_power'], [], 10);
    expect(out.map(a => a.key)).toEqual(['rated_torque']);
  });

  it('user-restored keys override the bare-type-hidden default', () => {
    const attrs = [nested('rated_power'), bare('frame_size')];
    const out = computeVisibleColumnAttributes(attrs, [], ['frame_size'], 10);
    expect(out.map(a => a.key).sort()).toEqual(['frame_size', 'rated_power']);
  });

  it('caps the default set at maxVisible', () => {
    const attrs = Array.from({ length: 12 }, (_, i) => nested(`spec_${i}`));
    const out = computeVisibleColumnAttributes(attrs, [], [], 10);
    expect(out).toHaveLength(10);
  });

  // ───────────────────────────────────────────────────────────────────────
  // REGRESSION — 2026-05-23: "Add specs isn't adding new columns".
  //
  // The pre-fix code did `shown.slice(0, MAX_VISIBLE_COLUMNS)`. When the
  // default-visible set already filled the cap, restoring a bare-type
  // column appended it past the cap and the slice dropped it silently.
  // The handler ran, state updated, table didn't change — user saw a
  // dead button.
  //
  // Contract: a user-restored key ALWAYS lands in the visible set, even
  // when doing so pushes the total past `maxVisible`. The cap is a
  // soft default; explicit user choice overrides it.
  // ───────────────────────────────────────────────────────────────────────
  it('user-restored attr always renders even when default set already fills the cap', () => {
    // 10 nested attrs would-be-shown by default (fills the cap of 10).
    const defaults = Array.from({ length: 10 }, (_, i) => nested(`spec_${i}`));
    const restored = bare('frame_size');
    const attrs = [...defaults, restored];
    const out = computeVisibleColumnAttributes(attrs, [], ['frame_size'], 10);
    expect(out.map(a => a.key)).toContain('frame_size');
    // Restore is explicit; default slot count drops by one to keep
    // user-explicit count + default count == cap when possible.
    expect(out).toHaveLength(10);
  });

  it('multiple restores all survive — explicit-add count can exceed the cap', () => {
    const defaults = Array.from({ length: 10 }, (_, i) => nested(`spec_${i}`));
    const attrs = [
      ...defaults,
      bare('frame_size'),
      bare('cooling'),
      bare('mount_pattern'),
    ];
    const out = computeVisibleColumnAttributes(
      attrs,
      [],
      ['frame_size', 'cooling', 'mount_pattern'],
      10,
    );
    expect(out.map(a => a.key)).toEqual(
      expect.arrayContaining(['frame_size', 'cooling', 'mount_pattern']),
    );
    // 3 explicit + 7 defaults = 10 (cap). Explicit takes priority.
    expect(out).toHaveLength(10);
  });

  it('explicit restore takes priority over default-visible fill when cap is tight', () => {
    // 10 nested defaults + 1 restored bare; cap is 8.
    // Expect the bare attr in the output and exactly 7 of the 10 nested
    // (the first 7 in input order, since input is already ordered).
    const defaults = Array.from({ length: 10 }, (_, i) => nested(`spec_${i}`));
    const attrs = [...defaults, bare('frame_size')];
    const out = computeVisibleColumnAttributes(attrs, [], ['frame_size'], 8);
    expect(out).toHaveLength(8);
    expect(out.map(a => a.key)).toContain('frame_size');
    // Defaults that survived are the first 7 in input order.
    expect(out.filter(a => a.key !== 'frame_size').map(a => a.key)).toEqual([
      'spec_0',
      'spec_1',
      'spec_2',
      'spec_3',
      'spec_4',
      'spec_5',
      'spec_6',
    ]);
  });

  it('user-hidden wins over user-restored for the same key (defensive)', () => {
    const attrs = [bare('frame_size')];
    const out = computeVisibleColumnAttributes(
      attrs,
      ['frame_size'],
      ['frame_size'],
      10,
    );
    expect(out).toHaveLength(0);
  });

  it('preserves input ordering in the result', () => {
    // Caller is expected to pre-sort via orderColumnAttributes. The
    // helper must not reshuffle: output keys land in their input
    // positions among the surviving set.
    const attrs = [
      nested('alpha'),
      bare('charlie'), // hidden by default
      nested('bravo'),
      bare('delta'), // restored
    ];
    const out = computeVisibleColumnAttributes(attrs, [], ['delta'], 10);
    expect(out.map(a => a.key)).toEqual(['alpha', 'bravo', 'delta']);
  });

  it('returns empty when all attrs are user-hidden', () => {
    const attrs = [nested('rated_power'), nested('rated_torque')];
    const out = computeVisibleColumnAttributes(
      attrs,
      ['rated_power', 'rated_torque'],
      [],
      10,
    );
    expect(out).toHaveLength(0);
  });
});
