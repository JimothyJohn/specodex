import { describe, it, expect } from 'vitest';
import { applyGearCascade, outputTorqueTargets, snapGearUp } from './gearCascade';
import { applyFilters, FilterCriterion } from '../types/filters';
import type { Product } from '../types/models';
import { numericFromValue } from './filterValues';

const motor = (id: string, torque: number, speed: number) =>
  ({
    product_id: id,
    product_type: 'motor',
    manufacturer: 'X',
    part_number: id,
    rated_torque: { value: torque, unit: 'Nm' },
    peak_torque: { value: torque * 3, unit: 'Nm' },
    rated_speed: { value: speed, unit: 'rpm' },
    rated_power: { value: 1000, unit: 'W' },
  }) as unknown as Product;

const rows = [motor('a', 868, 1000), motor('b', 340, 3000), motor('c', 26681, 1790), motor('d', 0.1, 3000), motor('e', 31000, 500)];

const gte = (attribute: string, value: number): FilterCriterion => ({
  attribute,
  mode: 'include',
  operator: '>=',
  value,
  displayName: attribute,
});

const num = (p: Product, key: string) => numericFromValue((p as any)[key]);

describe('applyGearCascade', () => {
  it('never rewrites rated_torque / peak_torque / rated_speed', () => {
    const { products } = applyGearCascade(rows, [gte('output_torque', 30000)], 'motor');
    products.forEach((p, i) => {
      expect((p as any).rated_torque).toEqual((rows[i] as any).rated_torque);
      expect((p as any).peak_torque).toEqual((rows[i] as any).peak_torque);
      expect((p as any).rated_speed).toEqual((rows[i] as any).rated_speed);
    });
  });

  it('a rated_torque filter is plain shaft torque: no gearing, no geared matches', () => {
    const f = [gte('rated_torque', 30000)];
    const { products, active, gearMap } = applyGearCascade(rows, f, 'motor');
    expect(active).toBe(false);
    expect(gearMap.size).toBe(0);
    // Only the motor whose own shaft torque clears 30 kNm matches.
    expect(applyFilters(products, f).map(p => p.product_id)).toEqual(['e']);
  });

  it('every row an output_torque filter returns shows output torque >= the threshold', () => {
    const f = [gte('output_torque', 30000)];
    const { products, gearMap } = applyGearCascade(rows, f, 'motor');
    const hits = applyFilters(products, f);
    expect(hits.map(p => p.product_id).sort()).toEqual(['a', 'b', 'c', 'e']);
    for (const p of hits) {
      expect(num(p, 'output_torque')!).toBeGreaterThanOrEqual(30000);
      const ratio = gearMap.get(p.product_id)!;
      expect(num(p, 'output_torque')).toBeCloseTo(num(p, 'rated_torque')! * ratio, -1);
      expect(num(p, 'output_speed')).toBeCloseTo(num(p, 'rated_speed')! / ratio, 0);
    }
    // 868 Nm needs 34.6:1 → snaps up to 36:1; 26681 Nm needs 1.12:1 → 4:1;
    // 31000 Nm already clears it direct-drive.
    expect(gearMap.get('a')).toBe(36);
    expect(gearMap.get('c')).toBe(4);
    expect(gearMap.get('e')).toBe(1);
    // 0.1 Nm can't reach 30 kNm even at the top ratio, so it stays out.
    expect(gearMap.get('d')).toBe(100);
  });

  it('output columns equal the rated values when no output target is set', () => {
    const { products } = applyGearCascade(rows, [], 'motor');
    for (const p of products) {
      expect(num(p, 'output_torque')).toBe(num(p, 'rated_torque'));
      expect(num(p, 'output_speed')).toBe(num(p, 'rated_speed'));
    }
  });

  it('passes non-motor rows through untouched', () => {
    const { products, active } = applyGearCascade(rows, [gte('output_torque', 30000)], 'gearhead');
    expect(active).toBe(false);
    expect(products).toBe(rows);
  });
});

describe('outputTorqueTargets', () => {
  it('only reads >= / > filters on output_torque with a positive value', () => {
    expect(
      outputTorqueTargets([
        gte('rated_torque', 10),
        gte('output_torque', 0),
        { ...gte('output_torque', 50), operator: '<=' },
        gte('output_torque', 50),
      ]),
    ).toEqual([50]);
  });
});

describe('snapGearUp', () => {
  it('rounds up to the next discrete ratio and caps at the top one', () => {
    expect(snapGearUp(1)).toBe(1);
    expect(snapGearUp(1.01)).toBe(4);
    expect(snapGearUp(34.6)).toBe(36);
    expect(snapGearUp(1e6)).toBe(100);
  });
});
