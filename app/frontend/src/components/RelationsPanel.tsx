/**
 * RelationsPanel — surfaces compatibility candidates from /api/v1/relations.
 *
 * Skeleton component for SCHEMA Phase 3c. Consumes the device-relations
 * API (PR #90) to answer one of three questions:
 *
 *   - "Which motors fit this actuator?"
 *   - "Which drives can run this motor?"
 *   - "Which gearheads accept this motor frame?"
 *
 * Initially mounted on /actuators (forthcoming) and later on /build's
 * slot-fill grid (BUILD.md Part 3). The contract here is intentionally
 * narrow — props are { sourceProduct, relation } and the component
 * fetches + renders. No filter chips, no sort, no column resize. Build's
 * eventual surface adds those, but the skeleton stays simple so it can
 * be reused inside both surfaces.
 */

import { useEffect, useState } from 'react';
import './RelationsPanel.css';

export type RelationKind =
  | 'motors-for-actuator'
  | 'drives-for-motor'
  | 'gearheads-for-motor';

export interface SourceProduct {
  product_id: string;
  product_type: string;
  product_name?: string;
  manufacturer?: string;
}

export interface CandidateProduct {
  product_id: string;
  product_type: string;
  product_name?: string;
  manufacturer?: string;
  part_number?: string;
}

interface RelationsPanelProps {
  /** The product whose compatibility space we're querying. */
  sourceProduct: SourceProduct;
  /** Which relation to surface. */
  relation: RelationKind;
  /** Override the API base for testing / staging environments. */
  apiBase?: string;
  /** Click handler when a candidate row is selected. */
  onCandidateClick?: (candidate: CandidateProduct) => void;
}

interface RelationsResponse {
  success: boolean;
  data?: CandidateProduct[];
  count?: number;
  error?: string;
}

function buildUrl(
  apiBase: string,
  relation: RelationKind,
  sourceProduct: SourceProduct,
): string {
  const params = new URLSearchParams({ id: sourceProduct.product_id });
  if (relation === 'motors-for-actuator') {
    // Backend Zod schema requires `type` for the actuator branch since
    // the DynamoDB SK includes the type in its prefix. Default to the
    // source product's type — caller passes a LinearActuator or
    // ElectricCylinder; we forward whichever it is.
    params.set('type', sourceProduct.product_type);
  }
  return `${apiBase}/api/v1/relations/${relation}?${params.toString()}`;
}

function relationHeading(relation: RelationKind): string {
  switch (relation) {
    case 'motors-for-actuator':
      return 'Compatible motors';
    case 'drives-for-motor':
      return 'Compatible drives';
    case 'gearheads-for-motor':
      return 'Compatible gearheads';
  }
}

export default function RelationsPanel({
  sourceProduct,
  relation,
  apiBase = '',
  onCandidateClick,
}: RelationsPanelProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<CandidateProduct[]>([]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const url = buildUrl(apiBase, relation, sourceProduct);
    fetch(url)
      .then(async r => {
        const body = (await r.json()) as RelationsResponse;
        if (!r.ok || !body.success) {
          throw new Error(body.error || `HTTP ${r.status}`);
        }
        return body.data || [];
      })
      .then(data => {
        if (!cancelled) {
          setCandidates(data);
          setLoading(false);
        }
      })
      .catch(e => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [apiBase, relation, sourceProduct]);

  return (
    <section className="relations-panel" aria-labelledby="relations-panel-heading">
      <h3 id="relations-panel-heading" className="relations-panel__heading">
        {relationHeading(relation)}
      </h3>

      {loading && (
        <div className="relations-panel__status" role="status">
          Loading…
        </div>
      )}

      {error && !loading && (
        <div className="relations-panel__error" role="alert">
          {error}
        </div>
      )}

      {!loading && !error && candidates.length === 0 && (
        <div className="relations-panel__empty">
          No compatible products found.
        </div>
      )}

      {!loading && !error && candidates.length > 0 && (
        <ul className="relations-panel__list">
          {candidates.map(c => (
            <li key={c.product_id} className="relations-panel__item">
              <button
                type="button"
                className="relations-panel__item-button"
                onClick={() => onCandidateClick?.(c)}
              >
                <span className="relations-panel__item-mfg">{c.manufacturer || '—'}</span>
                <span className="relations-panel__item-name">{c.product_name || c.product_id}</span>
                {c.part_number && (
                  <span className="relations-panel__item-pn">{c.part_number}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
