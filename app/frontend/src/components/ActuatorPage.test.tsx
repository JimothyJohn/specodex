/**
 * Unit tests for ActuatorPage helpers AND a component-level test
 * covering the bidirectional configurator flow.
 *
 * The component test boots ActuatorPage with a mocked apiClient,
 * exercises the click-record → configurator-prefill → derived-specs
 * → motor-suggestion path end-to-end. It's the cheapest smoke test
 * that catches regressions in the lifted state, parsing, and lazy
 * motor fetch interactions without needing Playwright.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../test/utils';
import ActuatorPage, { filterMotorsByFrame, type MotorCandidate } from './ActuatorPage';

// Mock apiClient — every test gets fresh mocks. AuthProvider isn't in
// the test wrapper so anything that needs auth state has to mock it
// out; ActuatorPage doesn't.
vi.mock('../api/client', () => ({
  apiClient: {
    listProducts: vi.fn(),
  },
}));

import { apiClient } from '../api/client';

const m = (overrides: Partial<MotorCandidate>): MotorCandidate => ({
  product_id: Math.random().toString(36).slice(2),
  manufacturer: 'Acme',
  product_name: 'Test motor',
  part_number: 'X-1',
  frame_size: null,
  rated_torque: null,
  ...overrides,
});

describe('filterMotorsByFrame', () => {
  it('matches frame 23 for "NEMA 23 / NEMA 34" suggestion', () => {
    const candidates = [
      m({ manufacturer: 'A', product_name: 'a1', frame_size: '23' }),
      m({ manufacturer: 'B', product_name: 'b1', frame_size: 'NEMA 23' }),
      m({ manufacturer: 'C', product_name: 'c1', frame_size: 'Size 23' }),
      m({ manufacturer: 'D', product_name: 'd1', frame_size: '34' }),
    ];
    const result = filterMotorsByFrame(candidates, 'NEMA 23 / NEMA 34');
    expect(result).toHaveLength(4);
  });

  it('rejects "Ø230" when matching NEMA 23 (false-positive guard)', () => {
    const candidates = [
      m({ manufacturer: 'Mitsubishi', product_name: 'MR-J4', frame_size: 'Ø230' }),
      m({ manufacturer: 'Faulhaber', product_name: '22L', frame_size: '230 mm' }),
    ];
    const result = filterMotorsByFrame(candidates, 'NEMA 23');
    expect(result).toHaveLength(0);
  });

  it('rejects motors with no frame_size', () => {
    const candidates = [
      m({ frame_size: null }),
      m({ frame_size: '' }),
      m({ frame_size: undefined }),
      m({ frame_size: 'NEMA 23' }),
    ];
    const result = filterMotorsByFrame(candidates, 'NEMA 23');
    expect(result).toHaveLength(1);
  });

  it('returns empty when suggested frame has no digit-runs', () => {
    const candidates = [m({ frame_size: 'NEMA 23' })];
    const result = filterMotorsByFrame(candidates, 'no digits here');
    expect(result).toHaveLength(0);
  });

  it('caps result count by limit param', () => {
    const candidates = Array.from({ length: 20 }, (_, i) =>
      m({ manufacturer: `M${i}`, product_name: `m${i}`, frame_size: 'NEMA 23' }),
    );
    expect(filterMotorsByFrame(candidates, 'NEMA 23', 3)).toHaveLength(3);
  });

  it('sorts results by manufacturer + product name', () => {
    const candidates = [
      m({ manufacturer: 'Z', product_name: 'z', frame_size: '23' }),
      m({ manufacturer: 'A', product_name: 'a', frame_size: '23' }),
      m({ manufacturer: 'M', product_name: 'm', frame_size: '23' }),
    ];
    const result = filterMotorsByFrame(candidates, 'NEMA 23');
    expect(result.map((r) => r.manufacturer)).toEqual(['A', 'M', 'Z']);
  });

  it('case-insensitive on the frame_size value', () => {
    const candidates = [
      m({ manufacturer: 'A', product_name: 'a', frame_size: 'IEC 71' }),
    ];
    expect(filterMotorsByFrame(candidates, 'IEC 71')).toHaveLength(1);
    expect(filterMotorsByFrame(candidates, 'iec 71')).toHaveLength(1);
  });
});

describe('ActuatorPage — component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function makeLinearActuator(overrides: Record<string, unknown> = {}) {
    return {
      product_id: 'la-1',
      product_type: 'linear_actuator' as const,
      product_name: 'TRS 165 Rodless',
      manufacturer: 'Tolomatic',
      series: 'TRS',
      part_number: 'TRS165-BNM10',
      stroke: { value: 600, unit: 'mm' },
      max_push_force: { value: 8400, unit: 'N' },
      actuation_mechanism: 'ball_screw',
      ...overrides,
    };
  }

  function makeMotor(overrides: Record<string, unknown> = {}) {
    return {
      product_id: 'm-1',
      product_type: 'motor' as const,
      product_name: 'Test Motor',
      manufacturer: 'Acme',
      part_number: 'M-1',
      frame_size: 'NEMA 23',
      rated_torque: { value: 1.2, unit: 'Nm' },
      ...overrides,
    };
  }

  it('renders the Linear Motion supercategory hero on mount', async () => {
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<ActuatorPage />);
    expect(await screen.findByText('Linear Motion')).toBeTruthy();
    // Selection question subhead.
    expect(screen.getByText(/How do I move this load this far/i)).toBeTruthy();
  });

  it('shows live record counts in the subtype tabs after fetch', async () => {
    const la = [makeLinearActuator(), makeLinearActuator({ product_id: 'la-2', part_number: 'TRS235-BNL05' })];
    const ec: unknown[] = [];
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) => {
      if (type === 'linear_actuator') return Promise.resolve(la);
      if (type === 'electric_cylinder') return Promise.resolve(ec);
      return Promise.resolve([]);
    });
    render(<ActuatorPage />);
    // Both `subtype tabs` and `configurator template chips` use role=tab,
    // so disambiguate by the count text we expect on the subtype tab.
    await waitFor(() => {
      const tab = screen.getByRole('tab', { name: /Linear Actuator\s*2$/i });
      expect(tab).toBeTruthy();
    });
  });

  it('renders all six configurator template chips', async () => {
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    render(<ActuatorPage />);
    await screen.findByText('Linear Motion');
    for (const name of [
      'Tolomatic TRS',
      'Tolomatic BCS',
      'Tolomatic ERD',
      'Lintech 200 Series',
      'Toyo Y43',
      'Parker HD',
    ]) {
      expect(screen.getByRole('tab', { name })).toBeTruthy();
    }
  });

  it('clicking a Tolomatic record reverse-parses into the configurator', async () => {
    const la = [makeLinearActuator()];
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) =>
      Promise.resolve(type === 'linear_actuator' ? la : []),
    );
    render(<ActuatorPage />);
    // Wait for the table row to render.
    const row = await screen.findByText('TRS165-BNM10');
    fireEvent.click(row);
    // Pre-fill banner mentions the part number.
    await waitFor(() => {
      expect(screen.getByText('TRS165-BNM10', { selector: 'code' })).toBeTruthy();
    });
    // The Tolomatic TRS chip is now the active tab.
    const trsChip = screen.getByRole('tab', { name: 'Tolomatic TRS' });
    expect(trsChip.getAttribute('aria-selected')).toBe('true');
    // Synthesise round-trip — the rendered part number matches.
    const synth = document.querySelector('.actuator-page__synth-value')!;
    expect(synth.textContent).toBe('TRS165-BNM10');
  });

  it('manually switching templates clears the prefill banner', async () => {
    const la = [makeLinearActuator()];
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) =>
      Promise.resolve(type === 'linear_actuator' ? la : []),
    );
    render(<ActuatorPage />);
    fireEvent.click(await screen.findByText('TRS165-BNM10'));
    await waitFor(() => screen.getByText(/Pre-filled from/));
    fireEvent.click(screen.getByRole('tab', { name: 'Lintech 200 Series' }));
    expect(screen.queryByText(/Pre-filled from/)).toBeNull();
  });

  it('clicked record triggers motor lookup; mismatched frames produce empty list', async () => {
    const la = [makeLinearActuator()];
    const motors = [makeMotor({ frame_size: 'Ø230' })]; // false-positive guard
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) => {
      if (type === 'linear_actuator') return Promise.resolve(la);
      if (type === 'electric_cylinder') return Promise.resolve([]);
      if (type === 'motor') return Promise.resolve(motors);
      return Promise.resolve([]);
    });
    render(<ActuatorPage />);
    fireEvent.click(await screen.findByText('TRS165-BNM10'));
    // Motor panel renders, but the Ø230 motor is rejected by word-boundary
    // matching ("23" boundary doesn't match Ø230).
    await waitFor(() => {
      expect(screen.getByText(/No catalogued motors/i)).toBeTruthy();
    });
  });

  it('clicked record surfaces real motor candidates', async () => {
    const la = [makeLinearActuator()];
    const motors = [
      makeMotor({ product_id: 'm-good', manufacturer: 'GoodCo', frame_size: 'NEMA 23' }),
      makeMotor({ product_id: 'm-bad', manufacturer: 'BadCo', frame_size: 'Ø230' }),
    ];
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) => {
      if (type === 'linear_actuator') return Promise.resolve(la);
      if (type === 'electric_cylinder') return Promise.resolve([]);
      if (type === 'motor') return Promise.resolve(motors);
      return Promise.resolve([]);
    });
    render(<ActuatorPage />);
    fireEvent.click(await screen.findByText('TRS165-BNM10'));
    await waitFor(() => {
      expect(screen.getByText(/GoodCo/)).toBeTruthy();
    });
    expect(screen.queryByText(/BadCo/)).toBeNull();
  });

  it('records without a matching template are not clickable', async () => {
    const la = [makeLinearActuator({ manufacturer: 'UnknownVendor', series: 'Unknown' })];
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) =>
      Promise.resolve(type === 'linear_actuator' ? la : []),
    );
    render(<ActuatorPage />);
    await screen.findByText('TRS165-BNM10');
    // Row is rendered but has no clickable class.
    const rows = document.querySelectorAll('.actuator-page__product-table tbody tr');
    expect(rows.length).toBe(1);
    expect(rows[0].classList.contains('actuator-page__row--clickable')).toBe(false);
  });

  it('shows API failure state when listProducts rejects', async () => {
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('network down'),
    );
    render(<ActuatorPage />);
    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeTruthy();
    });
  });

  it('subtype tab switch toggles which records render', async () => {
    const la = [makeLinearActuator()];
    const ec = [
      {
        product_id: 'ec-1',
        product_type: 'electric_cylinder' as const,
        product_name: 'ERD 15',
        manufacturer: 'Tolomatic',
        series: 'ERD',
        part_number: 'ERD15-BNM05-304.8',
        stroke: { value: 304.8, unit: 'mm' },
        max_push_force: { value: 4448, unit: 'N' },
        motor_type: 'servo_motor',
      },
    ];
    (apiClient.listProducts as ReturnType<typeof vi.fn>).mockImplementation((type: string) => {
      if (type === 'linear_actuator') return Promise.resolve(la);
      if (type === 'electric_cylinder') return Promise.resolve(ec);
      return Promise.resolve([]);
    });
    render(<ActuatorPage />);
    // Linear actuator row appears first (default tab).
    await screen.findByText('TRS165-BNM10');
    expect(screen.queryByText('ERD15-BNM05-304.8')).toBeNull();
    // Switch tab.
    fireEvent.click(screen.getByRole('tab', { name: /Electric Cylinder1/ }));
    await screen.findByText('ERD15-BNM05-304.8');
    expect(screen.queryByText('TRS165-BNM10')).toBeNull();
  });
});
