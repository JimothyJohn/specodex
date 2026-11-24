import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import LensSelector from './LensSelector';

describe('LensSelector', () => {
  it('renders the three lenses as toggle buttons with pressed state', () => {
    render(<LensSelector active={['form']} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'Form' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Fit' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: 'Function' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('adds a lens in canonical order, and removes an active one', () => {
    const onChange = vi.fn();
    const { rerender } = render(<LensSelector active={['form']} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Function' }));
    expect(onChange).toHaveBeenLastCalledWith(['function', 'form']);
    rerender(<LensSelector active={['function', 'form']} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Form' }));
    expect(onChange).toHaveBeenLastCalledWith(['function']);
  });

  it('uses no native title tooltips', () => {
    const { container } = render(<LensSelector active={[]} onChange={() => {}} />);
    expect(container.querySelector('[title]')).toBeNull();
  });
});
