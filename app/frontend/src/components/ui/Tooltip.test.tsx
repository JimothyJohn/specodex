/**
 * Tooltip — STYLE.md Phase 1.
 *
 * Locks down the contract that lets us replace 36 native `title=`
 * attributes safely:
 *   - Tooltip body renders into the DOM only when open.
 *   - Hover (mouseenter) opens after the configured delay; pointer-leave hides.
 *   - Focus opens (keyboard nav parity); blur hides.
 *   - Esc hides regardless of how it was opened.
 *   - The anchor element gets `aria-describedby` linking to the
 *     tooltip's id while open, and the link is removed when closed.
 *
 * Positioning math (viewport-edge clamp, auto-flip) is intentionally
 * NOT covered here — jsdom's getBoundingClientRect returns zeros, so a
 * unit test would just exercise the math against the wrong inputs. That
 * coverage belongs in a Playwright visual smoke test (STYLE.md Phase 7).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { Tooltip } from './Tooltip';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

const advance = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

describe('Tooltip', () => {
  it('does not render the tooltip body until opened', () => {
    render(
      <Tooltip content="Hint text" delay={0}>
        <button type="button">Trigger</button>
      </Tooltip>,
    );
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('shows on hover after the configured delay and hides on pointer-leave', () => {
    render(
      <Tooltip content="Hint text" delay={300}>
        <button type="button">Trigger</button>
      </Tooltip>,
    );

    const trigger = screen.getByRole('button', { name: 'Trigger' });
    fireEvent.mouseEnter(trigger);

    // Still hidden mid-delay — the timer hasn't elapsed.
    advance(200);
    expect(screen.queryByRole('tooltip')).toBeNull();

    advance(150);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Hint text');

    fireEvent.mouseLeave(trigger);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('shows immediately on hover when delay=0 (used by tests / fast contexts)', () => {
    render(
      <Tooltip content="Immediate" delay={0}>
        <button type="button">Trigger</button>
      </Tooltip>,
    );
    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Trigger' }));
    advance(0);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Immediate');
  });

  it('shows on focus and hides on blur (keyboard nav parity)', () => {
    render(
      <Tooltip content="Focus hint" delay={0}>
        <button type="button">Trigger</button>
      </Tooltip>,
    );

    const trigger = screen.getByRole('button', { name: 'Trigger' });
    fireEvent.focus(trigger);
    advance(0);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Focus hint');

    fireEvent.blur(trigger);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('hides on Escape', () => {
    render(
      <Tooltip content="Hint" delay={0}>
        <button type="button">Trigger</button>
      </Tooltip>,
    );

    fireEvent.mouseEnter(screen.getByRole('button', { name: 'Trigger' }));
    advance(0);
    expect(screen.getByRole('tooltip')).not.toBeNull();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('wires aria-describedby to the tooltip id while open and clears it on close', () => {
    render(
      <Tooltip content="Hint" delay={0}>
        <button type="button">Trigger</button>
      </Tooltip>,
    );

    const trigger = screen.getByRole('button', { name: 'Trigger' });
    expect(trigger.getAttribute('aria-describedby')).toBeNull();

    fireEvent.mouseEnter(trigger);
    advance(0);
    const tooltip = screen.getByRole('tooltip');
    expect(trigger.getAttribute('aria-describedby')).toBe(tooltip.id);

    fireEvent.mouseLeave(trigger);
    expect(trigger.getAttribute('aria-describedby')).toBeNull();
  });

  it('wraps non-element children in a span so the affordances still work', () => {
    render(
      <Tooltip content="Hint" delay={0}>
        plain text
      </Tooltip>,
    );

    // The wrapper span receives the handlers — query by visible text and
    // hover its parent.
    const text = screen.getByText('plain text');
    fireEvent.mouseEnter(text);
    advance(0);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Hint');
  });
});
