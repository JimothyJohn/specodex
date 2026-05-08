import { useEffect, type ReactElement } from 'react';
import { Link } from 'react-router-dom';
import ThemeToggle from './ThemeToggle';
import Tooltip from './ui/Tooltip';
import GitHubLink from './GitHubLink';
import './Welcome.css';

const ISSUE_STAMP = `ISSUE 1 — ${new Date().getFullYear()}`;

type MarkStudy = {
  id: string;
  name: string;
  note: string;
  svg: ReactElement;
};

const T1_RATIO_STUDIES: MarkStudy[] = [
  {
    id: 'T1a',
    name: 'Compressed top, open bottom',
    note: 'Asymmetric vertical rhythm — upper gap tight (12 u), lower gap wide (28 u). Arrow pair scales to its gap: small and quick on top, longer and weightier below. The S leans into the bottom half.',
    svg: (
      <svg
        viewBox="0 0 64 64"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.5"
        strokeLinecap="square"
        strokeLinejoin="miter"
        aria-hidden="true"
      >
        <line x1="6" y1="10" x2="58" y2="10" />
        <line x1="6" y1="22" x2="58" y2="22" />
        <line x1="6" y1="50" x2="58" y2="50" />
        <polygon points="14,10 12,14 16,14" fill="currentColor" stroke="none" />
        <line x1="14" y1="14" x2="14" y2="18" />
        <polygon points="14,22 12,18 16,18" fill="currentColor" stroke="none" />
        <polygon points="50,22 47,28 53,28" fill="currentColor" stroke="none" />
        <line x1="50" y1="28" x2="50" y2="44" />
        <polygon points="50,50 47,44 53,44" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    id: 'T1b',
    name: 'Tapered bars',
    note: 'Bars narrow as they descend — top widest, middle 70%, bottom 50%. Centered. The arrow pairs sit at symmetric inset points, anchored to where the narrowest bar still reaches. Pyramidal silhouette.',
    svg: (
      <svg
        viewBox="0 0 64 64"
        fill="none"
        stroke="currentColor"
        strokeWidth="3.5"
        strokeLinecap="square"
        strokeLinejoin="miter"
        aria-hidden="true"
      >
        <line x1="2" y1="12" x2="62" y2="12" />
        <line x1="10" y1="32" x2="54" y2="32" />
        <line x1="18" y1="52" x2="46" y2="52" />
        <polygon points="20,12 17,18 23,18" fill="currentColor" stroke="none" />
        <line x1="20" y1="18" x2="20" y2="26" />
        <polygon points="20,32 17,26 23,26" fill="currentColor" stroke="none" />
        <polygon points="44,32 41,38 47,38" fill="currentColor" stroke="none" />
        <line x1="44" y1="38" x2="44" y2="46" />
        <polygon points="44,52 41,46 47,46" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    id: 'T1c',
    name: 'Bold spine',
    note: 'Middle bar rendered as a filled stripe; top and bottom are hairlines. Arrow pairs are medium-weight and tuck into the heavy spine cleanly. The middle bar becomes the visual axis the rest hangs from.',
    svg: (
      <svg
        viewBox="0 0 64 64"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeLinejoin="miter"
        aria-hidden="true"
      >
        <line x1="6" y1="12" x2="58" y2="12" strokeWidth="1.5" />
        <rect x="6" y="29" width="52" height="6" fill="currentColor" stroke="none" />
        <line x1="6" y1="52" x2="58" y2="52" strokeWidth="1.5" />
        <polygon points="14,12 11,18 17,18" fill="currentColor" stroke="none" />
        <line x1="14" y1="18" x2="14" y2="23" strokeWidth="3" />
        <polygon points="14,29 11,23 17,23" fill="currentColor" stroke="none" />
        <polygon points="50,35 47,41 53,41" fill="currentColor" stroke="none" />
        <line x1="50" y1="41" x2="50" y2="46" strokeWidth="3" />
        <polygon points="50,52 47,46 53,46" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
  {
    id: 'T1d',
    name: 'Heavy bars, hairline pair',
    note: 'Inverse hierarchy. Bars are heavy (stroke 5); the dim pairs are delicate hairlines with small arrowheads. Bars dominate as architecture; the pairs are the engineering callout — quiet but precise.',
    svg: (
      <svg
        viewBox="0 0 64 64"
        fill="none"
        stroke="currentColor"
        strokeLinecap="square"
        strokeLinejoin="miter"
        aria-hidden="true"
      >
        <line x1="6" y1="12" x2="58" y2="12" strokeWidth="5" />
        <line x1="6" y1="32" x2="58" y2="32" strokeWidth="5" />
        <line x1="6" y1="52" x2="58" y2="52" strokeWidth="5" />
        <polygon points="14,15 12,18 16,18" fill="currentColor" stroke="none" />
        <line x1="14" y1="18" x2="14" y2="26" strokeWidth="1.5" />
        <polygon points="14,29 12,26 16,26" fill="currentColor" stroke="none" />
        <polygon points="50,35 48,38 52,38" fill="currentColor" stroke="none" />
        <line x1="50" y1="38" x2="50" y2="46" strokeWidth="1.5" />
        <polygon points="50,49 48,46 52,46" fill="currentColor" stroke="none" />
      </svg>
    ),
  },
];

export default function Welcome() {
  useEffect(() => {
    const previous = document.title;
    document.title = 'Specodex — A product selection frontend that only an engineer could love';
    return () => {
      document.title = previous;
    };
  }, []);

  return (
    <div className="specodex-landing" data-issue={ISSUE_STAMP}>
      <div className="specodex-grain" aria-hidden="true" />

      <header className="specodex-band">
        <div className="specodex-band-inner">
          <span className="specodex-wordmark">SPECODEX</span>
          <div className="specodex-band-right">
            <span className="specodex-band-meta">
              <span className="specodex-band-spec">SPEC · ODEX</span>
              <span className="specodex-band-rule" aria-hidden="true" />
              <span className="specodex-band-stamp">{ISSUE_STAMP}</span>
            </span>
            <GitHubLink />
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* CTA anchored to where the catalog's product-type dropdown lands
       * after navigation, so the cursor is already inside the dropdown
       * the moment the page loads. Right-edge of the viewport, sized to
       * the catalog's 500px filter sidebar; falls back to inline flow on
       * narrow viewports where the catalog stacks the filter pane on top. */}
      <Link
        to="/"
        className="specodex-cta specodex-cta-primary specodex-cta-anchor"
      >
        Make Your Selection
      </Link>

      <main className="specodex-main">
        <section className="specodex-hero">
          <h1 className="specodex-hero-title">
            A product selection frontend
            <br />
            that only an engineer could love.
          </h1>
          <p className="specodex-hero-sub">
            Industrial spec data — drives, motors, gearheads, contactors, actuators —
            indexed, filtered, and exportable. No marketing copy on the rows. No
            "request a quote" gates. The number you need, with the datasheet that
            produced it.
          </p>
        </section>

        <section className="specodex-marks" aria-labelledby="specodex-t1-ratios-title">
          <header className="specodex-marks-header">
            <h2 id="specodex-t1-ratios-title" className="specodex-marks-title">
              T1 — Ratio Studies
            </h2>
            <p className="specodex-marks-sub">
              Same three-bar + arrow-pair structure as T1, but with deliberate
              proportion play — asymmetric gaps, tapered bars, weight hierarchies.
              Minimal vocabulary, four different rhythms.
            </p>
          </header>
          <div className="specodex-marks-grid">
            {T1_RATIO_STUDIES.map((mark) => (
              <figure key={mark.id} className="specodex-mark">
                <div className="specodex-mark-frame">{mark.svg}</div>
                <figcaption className="specodex-mark-caption">
                  <span className="specodex-mark-tag">
                    <span className="specodex-mark-id">{mark.id}</span>
                    <span className="specodex-mark-name">{mark.name}</span>
                  </span>
                  <span className="specodex-mark-note">{mark.note}</span>
                </figcaption>
              </figure>
            ))}
          </div>
          <div
            className="specodex-favicon-strip"
            aria-label="T1 ratio studies at 32-pixel favicon scale"
          >
            <span className="specodex-favicon-label">Favicon · 32 px</span>
            <div className="specodex-favicon-row">
              {T1_RATIO_STUDIES.map((mark) => (
                <Tooltip key={mark.id} content={`${mark.id} — ${mark.name}`}>
                  <div
                    className="specodex-favicon-tile"
                    tabIndex={0}
                    aria-label={`${mark.id} — ${mark.name}`}
                  >
                    {mark.svg}
                  </div>
                </Tooltip>
              ))}
            </div>
          </div>
        </section>

      </main>
    </div>
  );
}
