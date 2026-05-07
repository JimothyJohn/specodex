/**
 * ChainReviewModal — INTEGRATION next slice item 1.
 *
 * Tests the helper, the gating ("at least one adjacent pair filled"), and
 * the rendering shape of the report list. apiClient.checkCompat is mocked
 * at module level so the test controls exactly what the modal stacks.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReactNode } from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AppProvider } from '../context/AppContext';
import ChainReviewModal, { adjacentFilledPairs } from './ChainReviewModal';
import type { Product } from '../types/models';
import type { CompatibilityReport } from '../types/compat';

const mockCheckCompat = vi.fn<
  (a: { id: string; type: string }, b: { id: string; type: string }) => Promise<CompatibilityReport>
>();

vi.mock('../api/client', () => ({
  apiClient: {
    listProducts: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getSummary: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getCategories: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    checkCompat: (a: { id: string; type: string }, b: { id: string; type: string }) => mockCheckCompat(a, b),
  },
}));

function product(product_type: string, opts: { id: string; manufacturer?: string; part_number?: string } = { id: 'x' }): Product {
  return {
    product_id: opts.id,
    product_type,
    manufacturer: opts.manufacturer ?? 'TestCo',
    part_number: opts.part_number,
  } as Product;
}

function okReport(from: 'drive' | 'motor', to: 'motor' | 'gearhead'): CompatibilityReport {
  return {
    from_type: from,
    to_type: to,
    status: 'ok',
    results: [
      {
        from_port: `${from}.power_output`,
        to_port: `${to}.power_input`,
        status: 'ok',
        checks: [{ field: 'voltage', status: 'ok', detail: 'matches' }],
      },
    ],
  } as CompatibilityReport;
}

const wrapper = ({ children }: { children: ReactNode }) => <AppProvider>{children}</AppProvider>;

beforeEach(() => {
  window.localStorage.clear();
  mockCheckCompat.mockReset();
});

describe('adjacentFilledPairs', () => {
  it('returns no pairs when fewer than two slots are filled', () => {
    expect(adjacentFilledPairs({})).toEqual([]);
    expect(adjacentFilledPairs({ motor: product('motor', { id: 'm1' }) })).toEqual([]);
  });

  it('returns the drive→motor pair when both are filled', () => {
    const drive = product('drive', { id: 'd1' });
    const motor = product('motor', { id: 'm1' });
    expect(adjacentFilledPairs({ drive, motor })).toEqual([
      { from: 'drive', to: 'motor', a: drive, b: motor },
    ]);
  });

  it('returns BUILD_SLOTS-ordered adjacent pairs when all three are filled', () => {
    const drive = product('drive', { id: 'd1' });
    const motor = product('motor', { id: 'm1' });
    const gearhead = product('gearhead', { id: 'g1' });
    const pairs = adjacentFilledPairs({ drive, motor, gearhead });
    expect(pairs).toHaveLength(2);
    expect(pairs[0].from).toBe('drive');
    expect(pairs[0].to).toBe('motor');
    expect(pairs[1].from).toBe('motor');
    expect(pairs[1].to).toBe('gearhead');
  });

  it('skips a non-adjacent gap (drive + gearhead, no motor)', () => {
    const drive = product('drive', { id: 'd1' });
    const gearhead = product('gearhead', { id: 'g1' });
    expect(adjacentFilledPairs({ drive, gearhead })).toEqual([]);
  });
});

describe('ChainReviewModal', () => {
  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <ChainReviewModal isOpen={false} onClose={vi.fn()} />,
      { wrapper },
    );
    expect(container.querySelector('.chain-review-overlay')).toBeNull();
  });

  it('shows a "no adjacent pair" hint when the build is empty', () => {
    render(<ChainReviewModal isOpen={true} onClose={vi.fn()} />, { wrapper });
    expect(screen.getByText(/Add at least two adjacent products/i)).toBeInTheDocument();
    expect(mockCheckCompat).not.toHaveBeenCalled();
  });

  it('fires one checkCompat call per adjacent filled pair on open', async () => {
    window.localStorage.setItem('specodex.build', JSON.stringify({
      drive: product('drive', { id: 'd1', manufacturer: 'ABB' }),
      motor: product('motor', { id: 'm1', manufacturer: 'NEMA' }),
      gearhead: product('gearhead', { id: 'g1', manufacturer: 'SEW' }),
    }));
    mockCheckCompat
      .mockResolvedValueOnce(okReport('drive', 'motor'))
      .mockResolvedValueOnce(okReport('motor', 'gearhead'));

    render(<ChainReviewModal isOpen={true} onClose={vi.fn()} />, { wrapper });

    await waitFor(() => {
      expect(mockCheckCompat).toHaveBeenCalledTimes(2);
    });
    expect(mockCheckCompat).toHaveBeenNthCalledWith(
      1,
      { id: 'd1', type: 'drive' },
      { id: 'm1', type: 'motor' },
    );
    expect(mockCheckCompat).toHaveBeenNthCalledWith(
      2,
      { id: 'm1', type: 'motor' },
      { id: 'g1', type: 'gearhead' },
    );
  });

  it('renders the two pair sections with their compat reports', async () => {
    window.localStorage.setItem('specodex.build', JSON.stringify({
      drive: product('drive', { id: 'd1', manufacturer: 'ABB', part_number: 'D-1' }),
      motor: product('motor', { id: 'm1', manufacturer: 'NEMA', part_number: 'M-1' }),
      gearhead: product('gearhead', { id: 'g1', manufacturer: 'SEW', part_number: 'G-1' }),
    }));
    mockCheckCompat
      .mockResolvedValueOnce(okReport('drive', 'motor'))
      .mockResolvedValueOnce(okReport('motor', 'gearhead'));

    render(<ChainReviewModal isOpen={true} onClose={vi.fn()} />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText('Drive → Motor')).toBeInTheDocument();
      expect(screen.getByText('Motor → Gearhead')).toBeInTheDocument();
    });
    // Product labels rendered for each pair.
    expect(screen.getByText('ABB — D-1')).toBeInTheDocument();
    // NEMA appears twice (once per adjacent pair); just assert it's present.
    expect(screen.getAllByText('NEMA — M-1').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('SEW — G-1')).toBeInTheDocument();
  });

  it('renders an error message when checkCompat rejects', async () => {
    window.localStorage.setItem('specodex.build', JSON.stringify({
      drive: product('drive', { id: 'd1' }),
      motor: product('motor', { id: 'm1' }),
    }));
    mockCheckCompat.mockRejectedValueOnce(new Error('backend down'));

    render(<ChainReviewModal isOpen={true} onClose={vi.fn()} />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText('backend down')).toBeInTheDocument();
    });
  });

  it('Close button calls onClose', () => {
    window.localStorage.setItem('specodex.build', JSON.stringify({
      drive: product('drive', { id: 'd1' }),
      motor: product('motor', { id: 'm1' }),
    }));
    mockCheckCompat.mockResolvedValue(okReport('drive', 'motor'));

    const onClose = vi.fn();
    render(<ChainReviewModal isOpen={true} onClose={onClose} />, { wrapper });

    fireEvent.click(screen.getByRole('button', { name: /close chain review/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape key calls onClose', () => {
    mockCheckCompat.mockResolvedValue(okReport('drive', 'motor'));
    const onClose = vi.fn();
    render(<ChainReviewModal isOpen={true} onClose={onClose} />, { wrapper });

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
