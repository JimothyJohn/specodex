/**
 * Tests for RelationsPanel — fetch wiring + states (loading / error / empty / loaded).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import RelationsPanel from './RelationsPanel';

const sampleActuator = {
  product_id: 'la-23',
  product_type: 'linear_actuator',
  product_name: 'BCS',
  manufacturer: 'Tolomatic',
};

const sampleMotor = {
  product_id: 'm-23',
  product_type: 'motor',
  product_name: 'NX series',
  manufacturer: 'Oriental',
  part_number: 'NX620AC-PS25',
};

function mockFetchResponse(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: async () => body,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('RelationsPanel', () => {
  it('renders the loading state on first paint', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));

    render(
      <RelationsPanel sourceProduct={sampleActuator} relation="motors-for-actuator" />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Loading');
    expect(screen.getByText('Compatible motors')).toBeInTheDocument();
  });

  it('renders the empty state when API returns no candidates', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchResponse({ success: true, data: [], count: 0 }),
    );

    render(
      <RelationsPanel sourceProduct={sampleActuator} relation="motors-for-actuator" />,
    );

    await waitFor(() => {
      expect(screen.getByText('No compatible products found.')).toBeInTheDocument();
    });
  });

  it('renders candidates and forwards onCandidateClick', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchResponse({ success: true, data: [sampleMotor], count: 1 }),
    );
    const onClick = vi.fn();

    render(
      <RelationsPanel
        sourceProduct={sampleActuator}
        relation="motors-for-actuator"
        onCandidateClick={onClick}
      />,
    );

    const row = await screen.findByText('NX series');
    expect(row).toBeInTheDocument();
    expect(screen.getByText('Oriental')).toBeInTheDocument();
    expect(screen.getByText('NX620AC-PS25')).toBeInTheDocument();

    await userEvent.click(row);
    expect(onClick).toHaveBeenCalledWith(sampleMotor);
  });

  it('renders the error state on HTTP failure', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetchResponse({ success: false, error: 'Actuator not found' }, false),
    );

    render(
      <RelationsPanel sourceProduct={sampleActuator} relation="motors-for-actuator" />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Actuator not found');
    });
  });

  it('builds the right URL for motors-for-actuator (includes type)', async () => {
    const fetchMock = mockFetchResponse({ success: true, data: [], count: 0 });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <RelationsPanel sourceProduct={sampleActuator} relation="motors-for-actuator" />,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('/api/v1/relations/motors-for-actuator');
    expect(url).toContain('id=la-23');
    expect(url).toContain('type=linear_actuator');
  });

  it('builds the right URL for drives-for-motor (no type param)', async () => {
    const fetchMock = mockFetchResponse({ success: true, data: [], count: 0 });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <RelationsPanel sourceProduct={sampleMotor} relation="drives-for-motor" />,
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('/api/v1/relations/drives-for-motor');
    expect(url).toContain('id=m-23');
    expect(url).not.toContain('type=');
  });
});
