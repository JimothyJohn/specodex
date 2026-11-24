/**
 * URL builder for the device-relations API (/api/v1/relations/*).
 *
 * Pure (no React, no CSS) so the backend contract tests can import the
 * exact function the UI uses and fire its output at the real routers:
 *   - Express: app/backend/tests/relations.frontend-contract.test.ts
 *     imports this module directly.
 *   - FastAPI: app/backend_py/tests/test_relations_frontend_contract.py
 *     replays relationsUrl.contract.json, which relationsUrl.test.ts pins
 *     to this function's output.
 *
 * Route shape (both backends, plus the MCP client in specodex/mcp/api.py):
 *   GET /api/v1/relations/motors-for-actuator?id=<uuid>&type=<actuator type>
 *   GET /api/v1/relations/drives-for-motor?id=<uuid>
 *   GET /api/v1/relations/gearheads-for-motor?id=<uuid>
 *
 * The RelationKind names are the route segments, so there is no
 * translation layer to drift. (An earlier version translated them to a
 * `/relations/motors?output=…&output_type=…` shape that neither backend
 * ever served; CloudFront's SPA fallback turned the 404 into index.html
 * and /build showed a JSON parse error.)
 */

export type RelationKind =
  | 'motors-for-actuator'
  | 'drives-for-motor'
  | 'gearheads-for-motor';

export interface RelationSource {
  product_id: string;
  product_type: string;
}

export function buildRelationsUrl(
  apiBase: string,
  relation: RelationKind,
  source: RelationSource,
): string {
  const params = new URLSearchParams({ id: source.product_id });
  if (relation === 'motors-for-actuator') {
    // linear_actuator and electric_cylinder live under different
    // partition keys, so the backend needs the type to read the record.
    params.set('type', source.product_type);
  }
  return `${apiBase}/api/v1/relations/${relation}?${params.toString()}`;
}
