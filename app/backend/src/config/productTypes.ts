/**
 * Product Types Configuration
 *
 * Single source of truth for "what product types exist" is now the
 * generated constant `PRODUCT_TYPES` derived from
 * `specodex/config.py:SCHEMA_CHOICES` (auto-discovered from
 * `specodex/models/*.py`). Regenerate with `./Quickstart gen-types`;
 * CI fails on drift.
 *
 * Adding a new product type:
 * 1. Drop a new file under `specodex/models/`.
 * 2. `./Quickstart gen-types`.
 * (Old runbook had 6 places; this file is no longer one of them.)
 */

import { PRODUCT_TYPES, ProductTypeLiteral } from '../types/generated_constants';

/** All valid product types in the system. */
export const VALID_PRODUCT_TYPES = PRODUCT_TYPES;

/** Type helper for valid product types. */
export type ValidProductType = ProductTypeLiteral;

/**
 * Format a product type as a display name.
 * Examples: "motor" -> "Motors", "robot_arm" -> "Robot Arms"
 */
export function formatDisplayName(type: string): string {
  return type
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ') + 's';
}
