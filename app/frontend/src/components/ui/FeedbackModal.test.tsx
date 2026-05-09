import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';

import FeedbackModal from './FeedbackModal';

function Harness({
  defaultCategory,
  context,
}: {
  defaultCategory?: Parameters<typeof FeedbackModal>[0]['defaultCategory'];
  context?: Parameters<typeof FeedbackModal>[0]['context'];
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button data-testid="trigger" onClick={() => setOpen(true)}>
        Open
      </button>
      <span data-testid="open">{open ? 'yes' : 'no'}</span>
      <FeedbackModal
        open={open}
        onClose={() => setOpen(false)}
        defaultCategory={defaultCategory}
        context={context}
      />
    </>
  );
}

describe('FeedbackModal', () => {
  let originalHref: string;
  let lastHref: string;

  beforeEach(() => {
    lastHref = '';
    originalHref = window.location.href;
    // jsdom forbids overwriting window.location.href directly via assignment
    // semantics that trigger navigation, so replace the location object with
    // a stub that captures the assignment.
    Object.defineProperty(window, 'location', {
      writable: true,
      value: {
        ...window.location,
        get href() {
          return lastHref || originalHref;
        },
        set href(v: string) {
          lastHref = v;
        },
        pathname: '/',
        search: '',
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders nothing when closed', () => {
    render(<Harness />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens on trigger and shows all four categories', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('trigger'));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('A product is missing')).toBeTruthy();
    expect(screen.getByText('A spec is wrong')).toBeTruthy();
    expect(screen.getByText("I can't find what I need")).toBeTruthy();
    expect(screen.getByText('General feedback')).toBeTruthy();
  });

  it('respects defaultCategory', () => {
    render(<Harness defaultCategory="missing_product" />);
    fireEvent.click(screen.getByTestId('trigger'));
    const radio = screen.getByLabelText('A product is missing') as HTMLInputElement;
    expect(radio.checked).toBe(true);
  });

  it('switches category on click', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.click(screen.getByLabelText('A spec is wrong'));
    const radio = screen.getByLabelText('A spec is wrong') as HTMLInputElement;
    expect(radio.checked).toBe(true);
  });

  it('submits via mailto: with subject + body and closes', () => {
    render(<Harness defaultCategory="no_match" context={{ productType: 'motor' }} />);
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.change(screen.getByLabelText("Tell us what's going on"), {
      target: { value: 'Need a 2 kW frameless servo' },
    });
    fireEvent.click(screen.getByText('Compose email'));
    expect(lastHref.startsWith('mailto:nick@advin.io?')).toBe(true);
    expect(decodeURIComponent(lastHref)).toContain('Need a 2 kW frameless servo');
    expect(decodeURIComponent(lastHref)).toContain('(motor)');
    expect(screen.getByTestId('open').textContent).toBe('no');
  });

  it('closes on Cancel without navigating', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.click(screen.getByText('Cancel'));
    expect(lastHref).toBe('');
    expect(screen.getByTestId('open').textContent).toBe('no');
  });

  it('closes on Escape without navigating', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('trigger'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(lastHref).toBe('');
    expect(screen.getByTestId('open').textContent).toBe('no');
  });

  it('resets message between opens', () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId('trigger'));
    const ta1 = screen.getByLabelText("Tell us what's going on") as HTMLTextAreaElement;
    fireEvent.change(ta1, { target: { value: 'first message' } });
    fireEvent.click(screen.getByText('Cancel'));
    fireEvent.click(screen.getByTestId('trigger'));
    const ta2 = screen.getByLabelText("Tell us what's going on") as HTMLTextAreaElement;
    expect(ta2.value).toBe('');
  });
});
