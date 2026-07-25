/**
 * VendorDrawer — honest empty state + cited fact rendering
 * (todo/COMMERCIAL.md Phases 3+4 shell).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import VendorDrawer from './VendorDrawer';

const registry = {
  'allen-bradley': {
    facts: [
      {
        label: 'Standard warranty',
        value: '12 months from installation',
        sources: [
          {
            url: 'https://vendor.example.com/warranty',
            kind: 'vendor_doc',
            retrieved_at: '2026-07-01T00:00:00Z',
            excerpt: 'Products are warranted for 12 months from installation.',
          },
        ],
      },
      {
        label: 'Support hours',
        value: '24/7 phone support (NA region)',
        sources: [
          {
            url: 'https://vendor.example.com/support',
            kind: 'vendor_doc',
            retrieved_at: '2026-07-01T00:00:00Z',
            excerpt: null,
          },
        ],
      },
    ],
  },
};

describe('VendorDrawer', () => {
  it('renders nothing when no manufacturer is selected', () => {
    render(<VendorDrawer manufacturer={null} onClose={() => {}} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('titles the drawer with the manufacturer', () => {
    render(<VendorDrawer manufacturer="Siemens" onClose={() => {}} registry={{}} />);
    expect(screen.getByRole('heading').textContent).toBe('Siemens');
  });

  it('shows the honest empty state for a vendor without registry facts', () => {
    render(<VendorDrawer manufacturer="Siemens" onClose={() => {}} registry={{}} />);
    expect(
      screen.getByText(
        /No published commercial facts on file for Siemens\. Facts appear here only with a verifiable public citation\./,
      ),
    ).toBeTruthy();
  });

  it('tolerates the default (empty) bundled registry', () => {
    render(<VendorDrawer manufacturer="Omron" onClose={() => {}} />);
    expect(screen.getByText(/No published commercial facts on file for Omron/)).toBeTruthy();
  });

  it('renders each fact row with label, value, and a sources trigger', () => {
    render(
      <VendorDrawer manufacturer="Allen-Bradley" onClose={() => {}} registry={registry} />,
    );
    expect(screen.getByText('Standard warranty')).toBeTruthy();
    expect(screen.getByText('12 months from installation')).toBeTruthy();
    expect(screen.getByText('Support hours')).toBeTruthy();
    expect(screen.getAllByText('1 source')).toHaveLength(2);
  });

  it('opens the SourcePopover with the citation for a fact', () => {
    render(
      <VendorDrawer manufacturer="Allen-Bradley" onClose={() => {}} registry={registry} />,
    );
    fireEvent.click(screen.getAllByText('1 source')[0]);
    expect(screen.getByText('vendor.example.com')).toBeTruthy();
    expect(
      screen.getByText(/Products are warranted for 12 months from installation\./),
    ).toBeTruthy();
  });

  it('closes on backdrop click and on Escape', () => {
    const onClose = vi.fn();
    const { container } = render(
      <VendorDrawer manufacturer="Siemens" onClose={onClose} registry={{}} />,
    );
    void container;
    fireEvent.click(document.querySelector('.vendor-drawer-backdrop')!);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
