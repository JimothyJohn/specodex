/**
 * Pins relationsUrl.contract.json to buildRelationsUrl(). The FastAPI
 * contract test replays the JSON file against the real router, so this
 * test is what makes that replay mean "the URL the UI actually builds".
 */

import { describe, it, expect } from 'vitest';
import { buildRelationsUrl, type RelationKind } from './relationsUrl';
import contract from './relationsUrl.contract.json';

describe('buildRelationsUrl', () => {
  it('covers every RelationKind in the contract file', () => {
    const kinds: RelationKind[] = [
      'motors-for-actuator',
      'drives-for-motor',
      'gearheads-for-motor',
    ];
    expect(new Set(contract.cases.map(c => c.relation))).toEqual(new Set(kinds));
  });

  it.each(contract.cases)('$relation matches the contract URL', c => {
    expect(buildRelationsUrl('', c.relation as RelationKind, c.source)).toBe(c.url);
  });

  it('prefixes apiBase', () => {
    const c = contract.cases[0];
    expect(buildRelationsUrl('https://example.test', c.relation as RelationKind, c.source)).toBe(
      `https://example.test${c.url}`,
    );
  });
});
