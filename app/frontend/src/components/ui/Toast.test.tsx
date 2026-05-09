/**
 * Toast tests — STYLE.md Phase 3.
 *
 * Covers the imperative API (success/error/info), variant rendering,
 * auto-dismiss timer, manual close, stack cap (MAX_VISIBLE), and the
 * provider/hook contract.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';

import { ToastProvider, useToast, ToastApi } from './Toast';

let capturedApi: ToastApi | null = null;

function ApiCapture() {
  capturedApi = useToast();
  return null;
}

function renderWithProvider() {
  capturedApi = null;
  return render(
    <ToastProvider>
      <ApiCapture />
    </ToastProvider>,
  );
}

describe('Toast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders nothing until a toast is pushed', () => {
    renderWithProvider();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows a success toast with role=status', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.success('Copied BOM');
    });
    const toast = screen.getByRole('status');
    expect(toast).toBeTruthy();
    expect(toast.className).toContain('toast--success');
    expect(screen.getByText('Copied BOM')).toBeTruthy();
  });

  it('shows an error toast with role=alert and detail line', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.error("Couldn't update product", { detail: 'HTTP 503' });
    });
    const toast = screen.getByRole('alert');
    expect(toast).toBeTruthy();
    expect(toast.className).toContain('toast--error');
    expect(screen.getByText("Couldn't update product")).toBeTruthy();
    expect(screen.getByText('HTTP 503')).toBeTruthy();
  });

  it('shows an info toast with role=status', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.info('Refreshing categories…');
    });
    const toast = screen.getByRole('status');
    expect(toast.className).toContain('toast--info');
  });

  it('auto-dismisses success after default 5s', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.success('Done');
    });
    expect(screen.queryByRole('status')).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(4999);
    });
    expect(screen.queryByRole('status')).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('auto-dismisses error after default 8s (longer than success)', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.error('Boom');
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.queryByRole('alert')).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('manual close dismisses the toast', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.success('Done');
    });
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('caps visible toasts at MAX_VISIBLE (4) — oldest evicted', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.info('one');
      capturedApi!.info('two');
      capturedApi!.info('three');
      capturedApi!.info('four');
      capturedApi!.info('five');
    });
    const toasts = screen.getAllByRole('status');
    expect(toasts).toHaveLength(4);
    // 'one' was evicted; 'two'..'five' remain.
    expect(screen.queryByText('one')).toBeNull();
    expect(screen.getByText('five')).toBeTruthy();
  });

  it('honors custom durationMs (0 disables auto-dismiss)', () => {
    renderWithProvider();
    act(() => {
      capturedApi!.success('Sticky', { durationMs: 0 });
    });
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.queryByRole('status')).toBeTruthy();
  });

  it('returns a no-op API outside ToastProvider', () => {
    // Toasts are non-essential UI side effects, so missing the provider
    // should degrade silently rather than crash. Tests in particular
    // benefit from this — they often mount a deeper provider (e.g.
    // AppProvider) without bothering to wrap with ToastProvider.
    function NoProviderHarness() {
      const toast = useToast();
      // Calling any method should be a no-op, not a throw.
      toast.success('ignored');
      toast.error('ignored');
      toast.info('ignored');
      toast.dismiss('ignored');
      return <div data-testid="rendered">ok</div>;
    }
    const { getByTestId } = render(<NoProviderHarness />);
    expect(getByTestId('rendered').textContent).toBe('ok');
    // Nothing should have been added to the DOM.
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
