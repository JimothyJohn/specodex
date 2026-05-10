/**
 * Density toggle — cozy ↔ compact. The icon depicts the CURRENT state:
 * 3 horizontal lines = cozy (relaxed spreadsheet), 5 horizontal lines =
 * compact (Bloomberg/DigiKey spreadsheet vibe — more rows, denser).
 *
 * Pre-rename note: this used to switch `compact ↔ comfy`. The May-2026
 * rename keeps the same toggle slot but the modes mean different things;
 * see AppContext for the localStorage migration via the .v2 key bump.
 */

import { useApp } from '../context/AppContext';
import Tooltip from './ui/Tooltip';

export default function DensityToggle() {
  const { rowDensity, setRowDensity } = useApp();
  const isCompact = rowDensity === 'compact';

  const toggle = () => {
    setRowDensity(isCompact ? 'cozy' : 'compact');
  };

  const title = isCompact
    ? 'Row density: compact — click for cozy spacing'
    : 'Row density: cozy — click for compact spacing';

  return (
    <Tooltip content={title}>
    <button
      className="theme-toggle density-toggle"
      onClick={toggle}
      aria-label={title}
      aria-pressed={isCompact}
    >
      {isCompact ? (
        // Compact: 5 thin lines, tight stack.
        <svg
          className="density-toggle-icon"
          viewBox="0 0 24 16"
          width="22"
          height="14"
          aria-hidden="true"
          fill="currentColor"
        >
          <rect x="2" y="1"  width="20" height="1.5" rx="0.5" />
          <rect x="2" y="4"  width="20" height="1.5" rx="0.5" />
          <rect x="2" y="7"  width="20" height="1.5" rx="0.5" />
          <rect x="2" y="10" width="20" height="1.5" rx="0.5" />
          <rect x="2" y="13" width="20" height="1.5" rx="0.5" />
        </svg>
      ) : (
        // Cozy: 3 lines, breathing room between them. Same glyph the
        // pre-rename `compact` mode used — the historical default.
        <svg
          className="density-toggle-icon"
          viewBox="0 0 24 16"
          width="22"
          height="14"
          aria-hidden="true"
          fill="currentColor"
        >
          <rect x="2" y="3"  width="20" height="2" rx="0.5" />
          <rect x="2" y="7"  width="20" height="2" rx="0.5" />
          <rect x="2" y="11" width="20" height="2" rx="0.5" />
        </svg>
      )}
    </button>
    </Tooltip>
  );
}
