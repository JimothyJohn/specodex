/**
 * ProductDetailModal — covers the per-spec "?" complaint button added in
 * the spec-complaint-button feature. The full modal interaction surface
 * (close-on-outside, build add/remove, compat checker) is exercised
 * elsewhere; these tests pin the new field-complaint flow only.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReactNode } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { AppProvider } from '../context/AppContext';
import { AuthProvider } from '../context/AuthContext';
import { ProjectsProvider } from '../context/ProjectsContext';
import ProductDetailModal from './ProductDetailModal';
import type { Product } from '../types/models';

vi.mock('../api/client', () => ({
  apiClient: {
    setAuthToken: vi.fn(),
    listProducts: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getSummary: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    getCategories: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    checkCompat: vi.fn(() => Promise.reject(new Error('apiClient not available in unit test'))),
    authLogin: vi.fn(),
    authRegister: vi.fn(),
    authConfirm: vi.fn(),
    authResendCode: vi.fn(),
    authForgotPassword: vi.fn(),
    authResetPassword: vi.fn(),
    authRefresh: vi.fn(),
    authLogout: vi.fn().mockResolvedValue(undefined),
    listProjects: vi.fn().mockResolvedValue({ data: [] }),
  },
}));

const wrapper = ({ children }: { children: ReactNode }) => (
  <AuthProvider>
    <ProjectsProvider>
      <AppProvider>{children}</AppProvider>
    </ProjectsProvider>
  </AuthProvider>
);

function makeMotor(): Product {
  return {
    product_id: 'mot-1',
    product_type: 'motor',
    manufacturer: 'Acme',
    part_number: 'X-1',
    rated_torque: { value: 5, unit: 'N·m' },
  } as Product;
}

describe('ProductDetailModal — per-spec complaint button', () => {
  let lastHref: string;

  beforeEach(() => {
    lastHref = '';
    Object.defineProperty(window, 'location', {
      writable: true,
      value: {
        ...window.location,
        get href() {
          return lastHref || '';
        },
        set href(v: string) {
          lastHref = v;
        },
        pathname: '/',
        search: '',
      },
    });
  });

  it('renders a complaint button next to a regular spec row', () => {
    render(
      <ProductDetailModal
        product={makeMotor()}
        onClose={() => {}}
        clickPosition={{ x: 0, y: 0 }}
      />,
      { wrapper },
    );
    const btn = screen.getByLabelText('Report inaccurate value for Rated Torque');
    expect(btn.textContent).toBe('?');
  });

  it('opens the FeedbackModal pre-selected to "A spec is wrong" on click', () => {
    render(
      <ProductDetailModal
        product={makeMotor()}
        onClose={() => {}}
        clickPosition={{ x: 0, y: 0 }}
      />,
      { wrapper },
    );
    fireEvent.click(screen.getByLabelText('Report inaccurate value for Rated Torque'));
    // Heading swaps to the field-specific title.
    expect(screen.getByText('Report inaccurate value')).toBeTruthy();
    const wrongRadio = screen.getByLabelText('A spec is wrong') as HTMLInputElement;
    expect(wrongRadio.checked).toBe(true);
  });

  it('threads the field name + value + unit into the mailto body', () => {
    render(
      <ProductDetailModal
        product={makeMotor()}
        onClose={() => {}}
        clickPosition={{ x: 0, y: 0 }}
      />,
      { wrapper },
    );
    fireEvent.click(screen.getByLabelText('Report inaccurate value for Rated Torque'));
    fireEvent.click(screen.getByText('Compose email'));
    const decoded = decodeURIComponent(lastHref);
    expect(decoded).toContain('Field: Rated Torque (rated_torque)');
    expect(decoded).toContain('Reported value: 5 N·m');
    expect(decoded).toContain('Product: Acme / X-1');
  });

  it('does NOT close the parent modal when the complaint button is clicked', () => {
    const onClose = vi.fn();
    render(
      <ProductDetailModal
        product={makeMotor()}
        onClose={onClose}
        clickPosition={{ x: 0, y: 0 }}
      />,
      { wrapper },
    );
    fireEvent.mouseDown(
      screen.getByLabelText('Report inaccurate value for Rated Torque'),
    );
    fireEvent.click(
      screen.getByLabelText('Report inaccurate value for Rated Torque'),
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
