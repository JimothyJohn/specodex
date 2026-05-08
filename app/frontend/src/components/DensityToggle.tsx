/**
 * Density toggle: compact = 3 thin lines, comfy = 2 rectangles.
 * Icon depicts the CURRENT state.
 */

import { useApp } from '../context/AppContext';
import Tooltip from './ui/Tooltip';

export default function DensityToggle() {
  const { rowDensity, setRowDensity } = useApp();
  const isCompact = rowDensity === 'compact';

  const toggle = () => {
    setRowDensity(isCompact ? 'comfy' : 'compact');
  };

  const title = isCompact
    ? 'Row density: compact — click for comfortable spacing'
    : 'Row density: comfortable — click for compact spacing';

  return (
    <Tooltip content={title}>
    <button
      className="theme-toggle density-toggle"
      onClick={toggle}
      aria-label={title}
      aria-pressed={isCompact}
    >
      {isCompact ? (
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
      ) : (
        <svg
          className="density-toggle-icon"
          viewBox="0 0 24 16"
          width="22"
          height="14"
          aria-hidden="true"
          fill="currentColor"
        >
          <rect x="2" y="2"  width="20" height="4" rx="1" />
          <rect x="2" y="10" width="20" height="4" rx="1" />
        </svg>
      )}
    </button>
    </Tooltip>
  );
}
