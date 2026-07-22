/**
 * ConfidenceChip tier rendering (todo/COMMERCIAL.md Phase 3).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ConfidenceChip from './ConfidenceChip';
import type { Confidence } from '../../utils/commercial';

const tiers: Array<[Confidence, string]> = [
  ['listed', 'LISTED'],
  ['high', 'HIGH'],
  ['medium', 'MED'],
  ['low', 'LOW'],
  ['inferred', 'EST'],
];

describe('ConfidenceChip', () => {
  it.each(tiers)('renders %s with its tier text and modifier class', (tier, text) => {
    render(<ConfidenceChip confidence={tier} />);
    const chip = screen.getByRole('button');
    expect(chip.textContent).toBe(text);
    expect(chip.className).toContain(`confidence-chip--${tier}`);
  });

  it('every tier gets a distinct modifier class', () => {
    const classes = new Set(
      tiers.map(([tier]) => {
        const { unmount } = render(<ConfidenceChip confidence={tier} />);
        const cls = screen.getByRole('button').className;
        unmount();
        return cls;
      }),
    );
    expect(classes.size).toBe(tiers.length);
  });

  it('inferred is visually distinct from listed (never masquerades)', () => {
    render(<ConfidenceChip confidence="inferred" />);
    const inferred = screen.getByRole('button');
    expect(inferred.className).toContain('confidence-chip--inferred');
    expect(inferred.className).not.toContain('confidence-chip--listed');
    expect(inferred.textContent).not.toBe('LISTED');
  });

  it('forwards clicks and reflects popover state via aria-expanded', () => {
    const onClick = vi.fn();
    render(<ConfidenceChip confidence="listed" onClick={onClick} active />);
    const chip = screen.getByRole('button');
    fireEvent.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(chip.getAttribute('aria-expanded')).toBe('true');
  });

  it('surfaces the detail wording to assistive tech', () => {
    render(<ConfidenceChip confidence="listed" detail="listed — catalog extraction" />);
    const chip = screen.getByRole('button');
    expect(chip.getAttribute('aria-label')).toContain('listed — catalog extraction');
    // Visible text stays the compact tier so it fits in a cell.
    expect(chip.textContent).toBe('LISTED');
  });
});
