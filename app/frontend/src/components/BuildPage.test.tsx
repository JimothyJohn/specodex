/**
 * Tests for BuildPage scaffold — page renders, slots lock/unlock,
 * relations chain wires through. Doesn't exercise the full
 * requirements form (deferred to Phase 1 PR-2).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import BuildPage from './BuildPage';

const sampleActuator = {
  product_id: 'la-1',
  product_type: 'linear_actuator',
  product_name: 'BCS',
  manufacturer: 'Tolomatic',
  part_number: 'BCS15-BNL05',
};

const sampleMotor = {
  product_id: 'm-1',
  product_type: 'motor',
  product_name: 'NX series',
  manufacturer: 'Oriental',
  part_number: 'NX620AC',
};

function fetchMockSequence(handlers: Array<(url: string) => unknown>) {
  let i = 0;
  return vi.fn(async (input: RequestInfo | URL) => {
    const handler = handlers[i++] || handlers[handlers.length - 1];
    const body = handler(String(input));
    return {
      ok: true,
      status: 200,
      json: async () => body,
    };
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('BuildPage scaffold', () => {
  it('renders header + locked downstream slots before any pick', async () => {
    vi.stubGlobal(
      'fetch',
      fetchMockSequence([() => ({ success: true, data: [], count: 0 })]),
    );

    render(<BuildPage />);

    expect(screen.getByText('Build a motion system')).toBeInTheDocument();
    expect(await screen.findByText(/No linear actuators in the catalog yet/)).toBeInTheDocument();

    // Motor / Drive / Gearhead are locked when no actuator picked.
    const lockedNotes = screen.getAllByText(/— locked/);
    expect(lockedNotes.length).toBeGreaterThanOrEqual(3);
  });

  it('lists actuator candidates from /api/v1/search', async () => {
    vi.stubGlobal(
      'fetch',
      fetchMockSequence([() => ({ success: true, data: [sampleActuator], count: 1 })]),
    );

    render(<BuildPage />);

    expect(await screen.findByText('BCS')).toBeInTheDocument();
    expect(screen.getByText('Tolomatic')).toBeInTheDocument();
  });

  it('unlocks Motor slot when an actuator is picked, and surfaces compatible motors', async () => {
    vi.stubGlobal(
      'fetch',
      fetchMockSequence([
        // Initial /api/v1/search?type=linear_actuator
        () => ({ success: true, data: [sampleActuator], count: 1 }),
        // RelationsPanel call once Motor slot unlocks
        () => ({ success: true, data: [sampleMotor], count: 1 }),
      ]),
    );

    render(<BuildPage />);

    const actuatorRow = await screen.findByText('BCS');
    await userEvent.click(actuatorRow);

    // The picked-state caption should appear in the Actuator slot header.
    await waitFor(() => {
      expect(screen.getByText(/Tolomatic BCS/)).toBeInTheDocument();
    });

    // Motor slot's RelationsPanel should fetch and surface the motor.
    expect(await screen.findByText('NX series')).toBeInTheDocument();
  });

  it('clears downstream picks when an upstream pick is changed', async () => {
    const otherActuator = { ...sampleActuator, product_id: 'la-2', product_name: 'SLS' };
    vi.stubGlobal(
      'fetch',
      fetchMockSequence([
        () => ({ success: true, data: [sampleActuator, otherActuator], count: 2 }),
        // First Motor query (for BCS)
        () => ({ success: true, data: [sampleMotor], count: 1 }),
        // Re-fetch when actuator changes — empty for SLS
        () => ({ success: true, data: [], count: 0 }),
      ]),
    );

    render(<BuildPage />);

    await userEvent.click(await screen.findByText('BCS'));
    await screen.findByText('NX series');

    // Switch to SLS — motor slot should re-fetch and downstream cleared.
    await userEvent.click(screen.getByText('SLS'));

    await waitFor(() => {
      expect(screen.queryByText('NX series')).not.toBeInTheDocument();
    });
  });

  it('reset button clears all picks', async () => {
    vi.stubGlobal(
      'fetch',
      fetchMockSequence([
        () => ({ success: true, data: [sampleActuator], count: 1 }),
        () => ({ success: true, data: [], count: 0 }),
      ]),
    );

    render(<BuildPage />);

    await userEvent.click(await screen.findByText('BCS'));

    // Picked caption present
    await screen.findByText(/Tolomatic BCS/);

    await userEvent.click(screen.getByText('Reset picks'));

    await waitFor(() => {
      expect(screen.queryByText(/Tolomatic BCS/)).not.toBeInTheDocument();
    });
  });
});
