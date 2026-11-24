/**
 * AnchoredPopover — placement + dismissal primitive (UI_CLEANUP Phase 2).
 *
 * jsdom's getBoundingClientRect returns zeros, which is fine: the panel
 * only needs a non-null anchor to compute a position. These tests pin
 * the dismissal contract (outside click closes, inside click and anchor
 * click don't, Escape closes) and that a closed panel renders nothing.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';
import AnchoredPopover from './AnchoredPopover';

function Harness({ onClose, initialOpen = true }: { onClose: () => void; initialOpen?: boolean }) {
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  return (
    <div>
      <button ref={setAnchor}>anchor</button>
      <button>elsewhere</button>
      <AnchoredPopover open={initialOpen} anchorEl={anchor} width={200} onClose={onClose} ariaLabel="test panel">
        <span>panel body</span>
      </AnchoredPopover>
    </div>
  );
}

describe('AnchoredPopover', () => {
  it('renders nothing when closed', () => {
    render(<Harness onClose={vi.fn()} initialOpen={false} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('portals a dialog with the label and children when open', () => {
    render(<Harness onClose={vi.fn()} />);
    const dialog = screen.getByRole('dialog', { name: 'test panel' });
    expect(dialog).toBeInTheDocument();
    expect(dialog.parentElement).toBe(document.body);
    expect(screen.getByText('panel body')).toBeInTheDocument();
  });

  it('closes on an outside pointer-down, not on inside or anchor clicks', () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.mouseDown(screen.getByText('panel body'));
    fireEvent.mouseDown(screen.getByText('anchor'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByText('elsewhere'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
