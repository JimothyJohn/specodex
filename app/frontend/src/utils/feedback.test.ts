import { describe, it, expect } from 'vitest';
import { buildFeedbackMailto, FEEDBACK_TO } from './feedback';
import type { FilterCriterion } from '../types/filters';

function decodeBody(href: string): string {
  const m = href.match(/[?&]body=([^&]+)/);
  if (!m) throw new Error(`No body param in ${href}`);
  return decodeURIComponent(m[1]);
}

function decodeSubject(href: string): string {
  const m = href.match(/[?&]subject=([^&]+)/);
  if (!m) throw new Error(`No subject param in ${href}`);
  return decodeURIComponent(m[1]);
}

describe('buildFeedbackMailto', () => {
  it('addresses to nick@advin.io with mailto: scheme', () => {
    const href = buildFeedbackMailto({ category: 'general', message: 'hi' });
    expect(href.startsWith(`mailto:${FEEDBACK_TO}?`)).toBe(true);
  });

  it('includes the category label in the subject', () => {
    const href = buildFeedbackMailto({
      category: 'missing_product',
      message: '',
    });
    expect(decodeSubject(href)).toBe('[Specodex] A product is missing');
  });

  it('appends product type to the subject when provided', () => {
    const href = buildFeedbackMailto({
      category: 'no_match',
      message: '',
      context: { productType: 'motor' },
    });
    expect(decodeSubject(href)).toContain('(motor)');
  });

  it('falls back to a placeholder when message is blank', () => {
    const href = buildFeedbackMailto({ category: 'general', message: '   ' });
    expect(decodeBody(href)).toContain('(No additional message)');
  });

  it('serialises filters one per line with displayName', () => {
    const filters: FilterCriterion[] = [
      {
        attribute: 'rated_voltage.min',
        mode: 'include',
        operator: '>=',
        value: 200,
        displayName: 'Rated Voltage',
      },
      {
        attribute: 'manufacturer',
        mode: 'exclude',
        operator: '=',
        value: 'Acme',
        displayName: 'Manufacturer',
      },
    ];
    const body = decodeBody(
      buildFeedbackMailto({
        category: 'no_match',
        message: 'no results',
        context: { filters },
      }),
    );
    expect(body).toContain('Active filters:');
    expect(body).toContain('- Rated Voltage >= 200');
    expect(body).toContain('- NOT Manufacturer = Acme');
  });

  it('drops neutral filters and empty values', () => {
    const filters: FilterCriterion[] = [
      {
        attribute: 'unused',
        mode: 'neutral',
        value: 'whatever',
        displayName: 'Unused',
      },
      {
        attribute: 'empty',
        mode: 'include',
        value: '',
        displayName: 'Empty',
      },
    ];
    const body = decodeBody(
      buildFeedbackMailto({
        category: 'general',
        message: 'x',
        context: { filters },
      }),
    );
    expect(body).not.toContain('Active filters:');
    expect(body).not.toContain('Unused');
    expect(body).not.toContain('Empty');
  });

  it('joins array filter values with commas', () => {
    const filters: FilterCriterion[] = [
      {
        attribute: 'manufacturer',
        mode: 'include',
        value: ['Acme', 'Beta'],
        displayName: 'Manufacturer',
      },
    ];
    const body = decodeBody(
      buildFeedbackMailto({
        category: 'general',
        message: 'x',
        context: { filters },
      }),
    );
    expect(body).toContain('Manufacturer = Acme, Beta');
  });

  it('includes route, product type, and product reference', () => {
    const body = decodeBody(
      buildFeedbackMailto({
        category: 'wrong_info',
        message: 'spec is wrong',
        context: {
          route: '/?type=drive',
          productType: 'drive',
          product: { manufacturer: 'Mitsubishi', part_number: 'MR-J5-100A' },
        },
      }),
    );
    expect(body).toContain('Route: /?type=drive');
    expect(body).toContain('Product type: drive');
    expect(body).toContain('Product: Mitsubishi / MR-J5-100A');
  });
});
