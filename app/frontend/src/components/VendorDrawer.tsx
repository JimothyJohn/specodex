/**
 * VendorDrawer — published commercial facts for a manufacturer
 * (todo/COMMERCIAL.md Phases 3+4, drawer shell).
 *
 * Opens when a manufacturer name is clicked in the results table. Reads
 * the vendor-facts registry (src/data/vendors.json — seeded empty; the
 * registry generator ships separately) through the tolerant accessor in
 * utils/commercial.ts. Ground rules: facts only, every fact cited, no
 * ratings, no invented copy. A vendor without registry facts gets the
 * honest empty state, plainly.
 *
 * App-native drawer: portaled, backdrop click + Escape dismissal, themed
 * scrollbar, no <dialog>.
 */

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import vendorsRegistry from '../data/vendors.json';
import {
  getVendorFacts,
  toDisplayCitation,
  type VendorFact,
} from '../utils/commercial';
import SourcePopover from './ui/SourcePopover';
import './VendorDrawer.css';

export interface VendorDrawerProps {
  /** Manufacturer display name; null renders nothing. */
  manufacturer: string | null;
  onClose: () => void;
  /** Registry override for tests. Defaults to the bundled vendors.json. */
  registry?: unknown;
}

function FactRow({ fact }: { fact: VendorFact }) {
  const sourcesRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const citations = fact.sources.map(toDisplayCitation);
  return (
    <li className="vendor-drawer-fact">
      <div className="vendor-drawer-fact-label">{fact.label}</div>
      <div className="vendor-drawer-fact-value">{fact.value}</div>
      <button
        ref={sourcesRef}
        type="button"
        className="vendor-drawer-fact-sources"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
      >
        {citations.length} source{citations.length === 1 ? '' : 's'}
      </button>
      <SourcePopover
        open={open}
        anchorEl={sourcesRef.current}
        heading={fact.label}
        citations={citations}
        onClose={() => setOpen(false)}
      />
    </li>
  );
}

export default function VendorDrawer({
  manufacturer,
  onClose,
  registry = vendorsRegistry,
}: VendorDrawerProps) {
  const open = manufacturer !== null;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const facts = getVendorFacts(registry, manufacturer);

  const drawer = (
    <div className="vendor-drawer-root">
      <div
        className="vendor-drawer-backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className="vendor-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Commercial facts for ${manufacturer}`}
      >
        <header className="vendor-drawer-header">
          <h2 className="vendor-drawer-title">{manufacturer}</h2>
          <button
            type="button"
            className="vendor-drawer-close"
            aria-label="Close vendor facts"
            onClick={onClose}
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 10 10"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M2 2 L8 8 M8 2 L2 8" />
            </svg>
          </button>
        </header>
        <div className="vendor-drawer-body scrollable">
          {facts.length === 0 ? (
            <p className="vendor-drawer-empty">
              No published commercial facts on file for {manufacturer}. Facts
              appear here only with a verifiable public citation.
            </p>
          ) : (
            <ul className="vendor-drawer-facts">
              {facts.map((fact, i) => (
                <FactRow key={`${fact.label}-${i}`} fact={fact} />
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );

  return createPortal(drawer, document.body);
}
