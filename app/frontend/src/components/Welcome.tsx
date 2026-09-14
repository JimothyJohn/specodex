import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import ThemeToggle from './ThemeToggle';
import GitHubLink from './GitHubLink';
import './Welcome.css';

const ISSUE_STAMP = `ISSUE 1 — ${new Date().getFullYear()}`;

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

      </main>
    </div>
  );
}
