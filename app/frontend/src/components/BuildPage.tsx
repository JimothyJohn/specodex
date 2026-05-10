/**
 * BuildPage — the requirements-first system assembler.
 *
 * SCAFFOLD for BUILD.md Phase 1 PR-1. The full design (left pane
 * RequirementsForm + right pane CandidatesPane + bottom SystemSummary,
 * all wired through buildDerivation.ts) is multi-PR work; this PR
 * stands up the navigable surface so subsequent PRs have a place to
 * land their pieces.
 *
 * What this scaffold demonstrates today:
 *   1. /build is a navigable route.
 *   2. Slot 1 (Actuator) loads real linear_actuator records from
 *      /api/v1/search and lets the user pick one.
 *   3. Once an actuator is picked, the Motor slot unlocks and
 *      RelationsPanel surfaces compatible motors via the relations API
 *      (PR #90). Picking a motor unlocks Drive and Gearhead, which
 *      consume the same panel with different `relation` kinds.
 *   4. A flat list of picks at the bottom — placeholder for the
 *      future <SystemSummary> with Copy BOM.
 *
 * Anything past that — the requirements form, derivation, the
 * "47 → 12" candidate-count diff, the orientation gravity vector — is
 * deliberately deferred to follow-up PRs in the Phase 1 sequence.
 */

import { useEffect, useState } from 'react';
import RelationsPanel, {
  type CandidateProduct,
  type SourceProduct,
} from './RelationsPanel';
import './BuildPage.css';

interface ActuatorRow {
  product_id: string;
  product_type: string;
  manufacturer?: string;
  product_name?: string;
  part_number?: string;
}

interface SearchResponse {
  success: boolean;
  data?: ActuatorRow[];
  error?: string;
}

type SlotKey = 'actuator' | 'motor' | 'drive' | 'gearhead';

interface SlotPicks {
  actuator: SourceProduct | null;
  motor: SourceProduct | null;
  drive: SourceProduct | null;
  gearhead: SourceProduct | null;
}

const EMPTY_PICKS: SlotPicks = {
  actuator: null,
  motor: null,
  drive: null,
  gearhead: null,
};

function asSourceProduct(c: CandidateProduct | ActuatorRow): SourceProduct {
  return {
    product_id: c.product_id,
    product_type: c.product_type,
    product_name: c.product_name,
    manufacturer: c.manufacturer,
  };
}

export default function BuildPage() {
  const [picks, setPicks] = useState<SlotPicks>(EMPTY_PICKS);
  const [actuators, setActuators] = useState<ActuatorRow[]>([]);
  const [actuatorLoading, setActuatorLoading] = useState(true);
  const [actuatorError, setActuatorError] = useState<string | null>(null);

  // Slot 1: list available linear_actuator records. Production design
  // will narrow this with the requirements form (stroke, peak force,
  // peak velocity); for the scaffold we surface the full catalog.
  useEffect(() => {
    let cancelled = false;
    setActuatorLoading(true);
    fetch('/api/v1/search?type=linear_actuator&limit=50')
      .then(r => r.json() as Promise<SearchResponse>)
      .then(body => {
        if (cancelled) return;
        if (!body.success) throw new Error(body.error || 'Search failed');
        setActuators(body.data || []);
        setActuatorLoading(false);
      })
      .catch(e => {
        if (cancelled) return;
        setActuatorError(e instanceof Error ? e.message : String(e));
        setActuatorLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function pick(slot: SlotKey, candidate: SourceProduct | null) {
    setPicks(prev => {
      const next: SlotPicks = { ...prev, [slot]: candidate };
      // Clear downstream slots on upstream re-pick — Build's narrowing
      // depends on each upstream pick and a stale downstream pick is
      // worse than no pick.
      if (slot === 'actuator') {
        next.motor = null;
        next.drive = null;
        next.gearhead = null;
      } else if (slot === 'motor') {
        next.drive = null;
        next.gearhead = null;
      }
      return next;
    });
  }

  function reset() {
    setPicks(EMPTY_PICKS);
  }

  const motorUnlocked = picks.actuator !== null;
  const driveUnlocked = picks.motor !== null;
  const gearheadUnlocked = picks.motor !== null;

  return (
    <div className="build-page">
      <header className="build-page__header">
        <h1 className="build-page__title">Build a motion system</h1>
        <p className="build-page__subtitle">
          Scaffold of the requirements-first assembler — see{' '}
          <code>todo/BUILD.md</code> for the full design. Today: pick an
          actuator, then surface compatible motors / drives / gearheads via
          the relations API.
        </p>
        <button type="button" className="build-page__reset" onClick={reset}>
          Reset picks
        </button>
      </header>

      <div className="build-page__panes">
        {/* Left pane — RequirementsForm placeholder. Full schema in
         * BUILD.md Part 2; deferred to a follow-up PR. */}
        <aside className="build-page__requirements" aria-labelledby="requirements-heading">
          <h2 id="requirements-heading" className="build-page__pane-title">
            Requirements
          </h2>
          <p className="build-page__placeholder">
            Requirements form lands in a follow-up PR. For now, every
            actuator in the catalog is shown as a candidate.
          </p>
        </aside>

        {/* Right pane — CandidatesPane with four slots. Each slot
         * unlocks left-to-right per BUILD.md Part 3. */}
        <main className="build-page__candidates">
          <h2 className="build-page__pane-title">Candidates</h2>

          {/* Slot 1: Actuator */}
          <section className="build-page__slot" aria-label="Actuator slot">
            <header className="build-page__slot-header">
              <span className="build-page__slot-label">Actuator</span>
              {picks.actuator && (
                <span className="build-page__slot-pick">
                  ✓ {picks.actuator.manufacturer || ''} {picks.actuator.product_name || picks.actuator.product_id}
                </span>
              )}
            </header>
            {actuatorLoading && <p className="build-page__slot-status">Loading…</p>}
            {actuatorError && (
              <p className="build-page__slot-error" role="alert">
                {actuatorError}
              </p>
            )}
            {!actuatorLoading && !actuatorError && actuators.length === 0 && (
              <p className="build-page__slot-status">
                No linear actuators in the catalog yet.
              </p>
            )}
            {!actuatorLoading && !actuatorError && actuators.length > 0 && (
              <ul className="build-page__candidate-list">
                {actuators.map(a => {
                  const isPicked = picks.actuator?.product_id === a.product_id;
                  return (
                    <li key={a.product_id}>
                      <button
                        type="button"
                        className={
                          'build-page__candidate' +
                          (isPicked ? ' build-page__candidate--picked' : '')
                        }
                        onClick={() => pick('actuator', isPicked ? null : asSourceProduct(a))}
                      >
                        <span className="build-page__candidate-mfg">
                          {a.manufacturer || '—'}
                        </span>
                        <span className="build-page__candidate-name">
                          {a.product_name || a.product_id}
                        </span>
                        {a.part_number && (
                          <span className="build-page__candidate-pn">{a.part_number}</span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* Slot 2: Motor — unlocks once an actuator is picked. */}
          <section className="build-page__slot" aria-label="Motor slot">
            <header className="build-page__slot-header">
              <span className="build-page__slot-label">Motor</span>
              {picks.motor && (
                <span className="build-page__slot-pick">
                  ✓ {picks.motor.manufacturer || ''} {picks.motor.product_name || picks.motor.product_id}
                </span>
              )}
              {!motorUnlocked && (
                <span className="build-page__slot-locked">— locked</span>
              )}
            </header>
            {motorUnlocked && picks.actuator && (
              <RelationsPanel
                sourceProduct={picks.actuator}
                relation="motors-for-actuator"
                onCandidateClick={c => pick('motor', asSourceProduct(c))}
              />
            )}
            {!motorUnlocked && (
              <p className="build-page__slot-status">
                Pick an actuator to surface compatible motors.
              </p>
            )}
          </section>

          {/* Slot 3: Drive — unlocks once a motor is picked. */}
          <section className="build-page__slot" aria-label="Drive slot">
            <header className="build-page__slot-header">
              <span className="build-page__slot-label">Drive</span>
              {picks.drive && (
                <span className="build-page__slot-pick">
                  ✓ {picks.drive.manufacturer || ''} {picks.drive.product_name || picks.drive.product_id}
                </span>
              )}
              {!driveUnlocked && (
                <span className="build-page__slot-locked">— locked</span>
              )}
            </header>
            {driveUnlocked && picks.motor && (
              <RelationsPanel
                sourceProduct={picks.motor}
                relation="drives-for-motor"
                onCandidateClick={c => pick('drive', asSourceProduct(c))}
              />
            )}
            {!driveUnlocked && (
              <p className="build-page__slot-status">
                Pick a motor to surface compatible drives.
              </p>
            )}
          </section>

          {/* Slot 4: Gearhead — optional; unlocks once a motor is picked. */}
          <section className="build-page__slot" aria-label="Gearhead slot">
            <header className="build-page__slot-header">
              <span className="build-page__slot-label">Gearhead (optional)</span>
              {picks.gearhead && (
                <span className="build-page__slot-pick">
                  ✓ {picks.gearhead.manufacturer || ''} {picks.gearhead.product_name || picks.gearhead.product_id}
                </span>
              )}
              {!gearheadUnlocked && (
                <span className="build-page__slot-locked">— locked</span>
              )}
            </header>
            {gearheadUnlocked && picks.motor && (
              <RelationsPanel
                sourceProduct={picks.motor}
                relation="gearheads-for-motor"
                onCandidateClick={c => pick('gearhead', asSourceProduct(c))}
              />
            )}
            {!gearheadUnlocked && (
              <p className="build-page__slot-status">
                Pick a motor to surface compatible gearheads.
              </p>
            )}
          </section>
        </main>
      </div>

      {/* Bottom strip — SystemSummary placeholder. Full BOM rendering +
       * Copy BOM button lands when BuildTray is absorbed. */}
      <footer className="build-page__summary" aria-label="System summary">
        <span className="build-page__summary-label">System:</span>
        <span>Actuator {picks.actuator ? '✓' : '—'}</span>
        <span>Motor {picks.motor ? '✓' : '—'}</span>
        <span>Drive {picks.drive ? '✓' : '—'}</span>
        <span>Gearhead {picks.gearhead ? '✓' : '—'}</span>
      </footer>
    </div>
  );
}
