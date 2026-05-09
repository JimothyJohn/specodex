/**
 * Focused tests for the tier-A "Spec wrong? Tell us." link in
 * ProductDetailModal. The full modal has a deep dependency tree
 * (AppContext, AuthContext, ProjectsContext, CompatChecker,
 * AddToProjectMenu); the tests below mock the app-level context and
 * only assert the new feedback affordance — the rest of the modal is
 * exercised by the existing integration tests for those features.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ProductDetailModal from './ProductDetailModal';
import type { Product } from '../types/models';

vi.mock('../context/AppContext', () => ({
  useApp: () => ({
    unitSystem: 'metric',
    build: {},
    addToBuild: vi.fn(),
    removeFromBuild: vi.fn(),
  }),
}));

// CompatChecker pulls products + auth context — stub it out so this test
// stays focused on the feedback link.
vi.mock('./CompatChecker', () => ({
  default: () => null,
}));

vi.mock('./AddToProjectMenu', () => ({
  default: () => null,
}));

const PRODUCT = {
  product_type: 'motor',
  product_id: 'm-1',
  product_name: 'HG-KR43',
  manufacturer: 'Mitsubishi',
  part_number: 'HG-KR43',
} as unknown as Product;

const POS = { x: 100, y: 100 };

describe('ProductDetailModal — Spec wrong? link', () => {
  it('does not render the link when onSpecFeedback is omitted', () => {
    render(<ProductDetailModal product={PRODUCT} clickPosition={POS} onClose={() => {}} />);
    expect(screen.queryByText(/Spec wrong/i)).toBeNull();
  });

  it('renders the link when onSpecFeedback is provided', () => {
    render(
      <ProductDetailModal
        product={PRODUCT}
        clickPosition={POS}
        onClose={() => {}}
        onSpecFeedback={() => {}}
      />,
    );
    expect(screen.getByText(/Spec wrong\? Tell us/i)).toBeTruthy();
  });

  it('calls onSpecFeedback with the product, then onClose', () => {
    const order: string[] = [];
    const onSpecFeedback = vi.fn((p) => {
      order.push(`feedback:${p.part_number}`);
    });
    const onClose = vi.fn(() => {
      order.push('close');
    });

    render(
      <ProductDetailModal
        product={PRODUCT}
        clickPosition={POS}
        onClose={onClose}
        onSpecFeedback={onSpecFeedback}
      />,
    );

    fireEvent.click(screen.getByText(/Spec wrong\? Tell us/i));

    expect(onSpecFeedback).toHaveBeenCalledTimes(1);
    expect(onSpecFeedback).toHaveBeenCalledWith(PRODUCT);
    expect(onClose).toHaveBeenCalledTimes(1);
    // Order matters: the parent needs to capture the product before the
    // modal closes (and selectedProduct is reset to null).
    expect(order).toEqual([`feedback:${PRODUCT.part_number}`, 'close']);
  });
});
