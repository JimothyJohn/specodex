import { LENS_HINTS, LENS_LABELS, LENS_ORDER, type Lens } from '../types/lens';
import Tooltip from './ui/Tooltip';
import './LensSelector.css';

interface LensSelectorProps {
  active: readonly Lens[];
  onChange: (next: Lens[]) => void;
}

/**
 * Form / Fit / Function toggles. Each is independent: click one to snap
 * the table to that lens, click more to combine, click an active one to
 * drop it. With none active the table shows its normal curated view.
 */
export default function LensSelector({ active, onChange }: LensSelectorProps) {
  const toggle = (lens: Lens) => {
    const next = active.includes(lens)
      ? active.filter(l => l !== lens)
      : LENS_ORDER.filter(l => l === lens || active.includes(l));
    onChange(next);
  };

  return (
    <div className="lens-selector" role="group" aria-label="Spec lens">
      {LENS_ORDER.map(lens => {
        const on = active.includes(lens);
        return (
          <Tooltip key={lens} content={LENS_HINTS[lens]}>
            <button
              type="button"
              className={`lens-selector-btn${on ? ' is-active' : ''}`}
              aria-pressed={on}
              onClick={() => toggle(lens)}
            >
              {LENS_LABELS[lens]}
            </button>
          </Tooltip>
        );
      })}
    </div>
  );
}
