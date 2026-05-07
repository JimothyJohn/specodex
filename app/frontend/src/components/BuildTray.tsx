/**
 * Sticky bottom-of-viewport tray showing the in-progress motion-system build.
 *
 * Three slots — drive, motor, gearhead. Filled slots show the chosen product
 * with a remove affordance. Empty slots are inert; the user fills them by
 * picking the type in the sidebar dropdown and clicking "Add to build" inside
 * a product's detail modal.
 *
 * Junction badges between filled adjacent slots run the same client-side
 * compat check used by the list filter — strict-failed junctions show as
 * partial here too (fits-partial mode), but the colour cue still points at
 * which junction to inspect.
 *
 * When every slot is filled and every junction rolls up to `ok`, the tray
 * flips to a "complete" visual state (green accent border + ✓) and the
 * Copy BOM button writes a plain-text bill of materials to the clipboard.
 */
import { useMemo, useState } from 'react';
import { useApp } from '../context/AppContext';
import { BUILD_SLOTS, BuildSlot, check } from '../utils/compat';
import type { Product } from '../types/models';
import CompatBadge from './CompatBadge';
import ChainReviewModal, { adjacentFilledPairs } from './ChainReviewModal';
import Tooltip from './ui/Tooltip';

const SLOT_LABEL: Record<BuildSlot, string> = {
  drive: 'Drive',
  motor: 'Motor',
  gearhead: 'Gearhead',
};

interface JunctionInfo {
  from: BuildSlot;
  to: BuildSlot;
  status: 'ok' | 'partial' | null;
  detail: string;
}

export function buildBomText(
  build: Partial<Record<BuildSlot, Product>>,
  junctions: JunctionInfo[],
): string {
  const lines: string[] = [];
  // Pad slot labels to a stable column so the BOM block reads as a list,
  // not a paragraph. "Gearhead:" is the longest label at 9 chars including
  // the colon — pad to 10 so there's at least one space before the value.
  const labelWidth = 10;
  for (const slot of BUILD_SLOTS) {
    const p = build[slot];
    if (!p) continue;
    const label = `${SLOT_LABEL[slot]}:`.padEnd(labelWidth);
    const name = `${p.manufacturer || 'Unknown'}${p.part_number ? ` — ${p.part_number}` : ''}`;
    lines.push(`${label}${name}`);
  }
  const filledJunctions = junctions.filter(j => j.status !== null);
  if (filledJunctions.length > 0) {
    lines.push('');
    for (const j of filledJunctions) {
      const tag = j.status === 'ok' ? '✓' : '!';
      const detail = j.status === 'ok' ? 'compatible' : (j.detail || 'partial match');
      lines.push(`${SLOT_LABEL[j.from]} → ${SLOT_LABEL[j.to]}: ${tag} ${detail}`);
    }
  }
  return lines.join('\n');
}

export default function BuildTray() {
  const { build, removeFromBuild, clearBuild } = useApp();
  const filledCount = Object.values(build).filter(Boolean).length;

  const junctions = useMemo<JunctionInfo[]>(() => {
    const out: JunctionInfo[] = [];
    for (let i = 0; i < BUILD_SLOTS.length - 1; i++) {
      const from = BUILD_SLOTS[i];
      const to = BUILD_SLOTS[i + 1];
      const a = build[from];
      const b = build[to];
      if (!a || !b) {
        out.push({ from, to, status: null, detail: '' });
        continue;
      }
      try {
        const r = check(a, b);
        const status = r.status === 'fail' ? 'partial' : r.status;
        let detail = '';
        if (r.status !== 'ok') {
          const issues = r.results.flatMap(p => p.checks.filter(c => c.status !== 'ok'));
          detail = issues.map(c => `${c.field}: ${c.detail}`).join(' • ') || 'partial match';
        }
        out.push({ from, to, status, detail });
      } catch {
        out.push({ from, to, status: null, detail: '' });
      }
    }
    return out;
  }, [build]);

  // "Complete" = every slot filled AND every junction rolled up to ok.
  // Drives the green accent + ✓ marker; pure visual, no behavioural
  // change beyond the Copy BOM button always being available when at
  // least one slot is filled.
  const isComplete = useMemo(() => {
    if (filledCount !== BUILD_SLOTS.length) return false;
    return junctions.every(j => j.status === 'ok');
  }, [filledCount, junctions]);

  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [reviewOpen, setReviewOpen] = useState(false);
  // Show "Review chain" once at least one adjacent pair is filled — the
  // modal is the right place to inspect cross-product fit, and a single
  // pair (drive+motor or motor+gearhead) is enough to be useful.
  const adjacentPairCount = useMemo(() => adjacentFilledPairs(build).length, [build]);
  const copyBom = async () => {
    const text = buildBomText(build, junctions);
    try {
      await navigator.clipboard.writeText(text);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
    // Auto-revert the label so the button stays useful for a second copy.
    window.setTimeout(() => setCopyState('idle'), 1600);
  };

  if (filledCount === 0) return null;

  const trayClass = `build-tray${isComplete ? ' is-complete' : ''}`;

  return (
    <div className={trayClass} role="region" aria-label="Motion system build">
      <div className="build-tray-inner">
        <span className="build-tray-label">Build:</span>
        {isComplete && (
          <Tooltip content="All slots filled and every junction is compatible">
            <span className="build-tray-complete-mark" aria-label="Build complete">
              ✓
            </span>
          </Tooltip>
        )}
        {BUILD_SLOTS.map((slot, idx) => {
          const product = build[slot];
          const isLast = idx === BUILD_SLOTS.length - 1;
          const junction = junctions[idx];
          const junctionStatus = junction?.status ?? null;
          const junctionDetail = junction?.detail ?? '';
          return (
            <span key={slot} className="build-tray-slot-wrap">
              <span className={`build-tray-slot ${product ? 'filled' : 'empty'}`}>
                <span className="build-tray-slot-type">{SLOT_LABEL[slot]}</span>
                {product ? (
                  <>
                    <span className="build-tray-slot-name">
                      {product.manufacturer || 'Unknown'}
                      {product.part_number ? ` — ${product.part_number}` : ''}
                    </span>
                    <Tooltip content="Remove">
                      <button
                        type="button"
                        className="build-tray-remove"
                        onClick={() => removeFromBuild(slot)}
                        aria-label={`Remove ${SLOT_LABEL[slot]} from build`}
                      >
                        ×
                      </button>
                    </Tooltip>
                  </>
                ) : (
                  <span className="build-tray-slot-empty">empty</span>
                )}
              </span>
              {!isLast && (
                <span className="build-tray-junction">
                  {junctionStatus ? (
                    <CompatBadge status={junctionStatus} detail={junctionDetail || undefined} />
                  ) : (
                    <span className="build-tray-arrow" aria-hidden="true">→</span>
                  )}
                </span>
              )}
            </span>
          );
        })}
        {adjacentPairCount > 0 && (
          <Tooltip content="Open a side-by-side compatibility audit of every adjacent pair">
            <button
              type="button"
              className="build-tray-review"
              onClick={() => setReviewOpen(true)}
            >
              Review chain
            </button>
          </Tooltip>
        )}
        <Tooltip content="Copy a plain-text bill of materials to the clipboard">
          <button
            type="button"
            className="build-tray-copy"
            onClick={copyBom}
          >
            {copyState === 'copied' ? 'Copied!' : copyState === 'failed' ? 'Copy failed' : 'Copy BOM'}
          </button>
        </Tooltip>
        <button type="button" className="build-tray-clear" onClick={clearBuild}>
          Clear
        </button>
      </div>
      <ChainReviewModal isOpen={reviewOpen} onClose={() => setReviewOpen(false)} />
    </div>
  );
}
