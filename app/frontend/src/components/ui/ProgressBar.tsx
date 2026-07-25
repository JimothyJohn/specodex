/**
 * App-native progress bar — the themed replacement for the banned
 * `<progress>` element (see CLAUDE.md "No native browser/OS chrome").
 *
 * Determinate when `value` and `max` are both provided (and max > 0);
 * otherwise renders an indeterminate sweep. The label renders beside
 * the track so screen readers and sighted users get the same message.
 */
import './ProgressBar.css';

interface ProgressBarProps {
  /** Current progress. Omit for an indeterminate bar. */
  value?: number;
  /** Total expected. Omit for an indeterminate bar. */
  max?: number;
  /** Short status text rendered beside the track. */
  label?: string;
  className?: string;
}

export function ProgressBar({ value, max, label, className }: ProgressBarProps) {
  const determinate = value !== undefined && max !== undefined && max > 0;
  const pct = determinate ? Math.min(100, (value / max) * 100) : undefined;

  return (
    <div
      className={`progress-bar${className ? ` ${className}` : ''}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={determinate ? max : undefined}
      aria-valuenow={determinate ? value : undefined}
      aria-label={label ?? 'Loading'}
    >
      {label && <span className="progress-bar-label">{label}</span>}
      <div className="progress-bar-track">
        <div
          className={`progress-bar-fill${determinate ? '' : ' progress-bar-indeterminate'}`}
          style={determinate ? { width: `${pct}%` } : undefined}
          data-testid="progress-bar-fill"
        />
      </div>
    </div>
  );
}
