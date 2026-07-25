import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProgressBar } from './ProgressBar';

describe('ProgressBar', () => {
  it('renders determinate width and ARIA values from value/max', () => {
    render(<ProgressBar value={2500} max={5000} label="Loading 2,500 of 5,000" />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '2500');
    expect(bar).toHaveAttribute('aria-valuemax', '5000');
    expect(screen.getByTestId('progress-bar-fill')).toHaveStyle({ width: '50%' });
    expect(screen.getByText('Loading 2,500 of 5,000')).toBeInTheDocument();
  });

  it('clamps overshoot to 100%', () => {
    render(<ProgressBar value={700} max={500} />);
    expect(screen.getByTestId('progress-bar-fill')).toHaveStyle({ width: '100%' });
  });

  it('renders indeterminate without value/max', () => {
    render(<ProgressBar label="Loading…" />);
    const bar = screen.getByRole('progressbar');
    expect(bar).not.toHaveAttribute('aria-valuenow');
    expect(screen.getByTestId('progress-bar-fill').className).toContain(
      'progress-bar-indeterminate'
    );
  });

  it('zero max is treated as indeterminate, not a division by zero', () => {
    render(<ProgressBar value={0} max={0} />);
    expect(screen.getByTestId('progress-bar-fill').className).toContain(
      'progress-bar-indeterminate'
    );
  });
});
