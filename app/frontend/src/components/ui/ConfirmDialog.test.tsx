/**
 * ConfirmDialog tests — STYLE.md Phase 2.
 *
 * Covers the imperative API surface (resolve true/false, Esc cancels,
 * Enter confirms) plus the basic provider/hook contract. Uses a
 * minimal `<TestHarness>` that drives `useConfirm()` from inside the
 * provider so the tests can't hit the "hook outside provider" branch.
 */

import { describe, it, expect } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';

import { ConfirmProvider, useConfirm } from './ConfirmDialog';

function TestHarness({
  options = { title: 'Are you sure?', body: 'This is reversible.' },
}: {
  options?: Parameters<ReturnType<typeof useConfirm>>[0];
}) {
  const confirm = useConfirm();
  const [result, setResult] = useState<'idle' | 'true' | 'false'>('idle');

  return (
    <>
      <button
        type="button"
        data-testid="trigger"
        onClick={async () => {
          const ok = await confirm(options);
          setResult(ok ? 'true' : 'false');
        }}
      >
        Open
      </button>
      <span data-testid="result">{result}</span>
    </>
  );
}

function renderWithProvider(opts?: Parameters<ReturnType<typeof useConfirm>>[0]) {
  return render(
    <ConfirmProvider>
      <TestHarness options={opts} />
    </ConfirmProvider>,
  );
}

describe('ConfirmDialog', () => {
  it('renders nothing until confirm() is called', () => {
    renderWithProvider();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens on trigger and resolves true on Confirm click', async () => {
    renderWithProvider();
    fireEvent.click(screen.getByTestId('trigger'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('Are you sure?')).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByText('OK'));
    });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('result').textContent).toBe('true');
  });

  it('resolves false on Cancel click', async () => {
    renderWithProvider();
    fireEvent.click(screen.getByTestId('trigger'));

    await act(async () => {
      fireEvent.click(screen.getByText('Cancel'));
    });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('result').textContent).toBe('false');
  });

  it('resolves false on Escape', async () => {
    renderWithProvider();
    fireEvent.click(screen.getByTestId('trigger'));

    await act(async () => {
      fireEvent.keyDown(document, { key: 'Escape' });
    });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('result').textContent).toBe('false');
  });

  it('resolves true on Enter', async () => {
    renderWithProvider();
    fireEvent.click(screen.getByTestId('trigger'));

    await act(async () => {
      fireEvent.keyDown(document, { key: 'Enter' });
    });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('result').textContent).toBe('true');
  });

  it('renders custom labels and danger variant', () => {
    renderWithProvider({
      title: 'Delete project?',
      confirmLabel: 'Delete',
      cancelLabel: 'Keep',
      confirmVariant: 'danger',
    });
    fireEvent.click(screen.getByTestId('trigger'));

    const confirmBtn = screen.getByText('Delete');
    expect(confirmBtn).toBeTruthy();
    expect(confirmBtn.className).toContain('confirm-dialog-confirm--danger');
    expect(screen.getByText('Keep')).toBeTruthy();
  });

  it('throws if useConfirm is called outside ConfirmProvider', () => {
    // Suppress the React error log so the test output stays clean.
    const original = console.error;
    console.error = () => {};
    try {
      function NoProviderHarness() {
        useConfirm();
        return null;
      }
      expect(() => render(<NoProviderHarness />)).toThrow(/ConfirmProvider/);
    } finally {
      console.error = original;
    }
  });
});
