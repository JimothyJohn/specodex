/* eslint-disable */
/**
 * AUTO-GENERATED — do not edit by hand.
 * Regenerate with: ./Quickstart gen-types
 * Source: specodex.config.SCHEMA_CHOICES (auto-discovered
 * product types under specodex/models/).
 *
 * Twin of the PRODUCT_TYPES export at the bottom of
 * app/frontend/src/types/generated.ts. Express's tsconfig
 * pins rootDir to ./src so it can't import the frontend file
 * directly; this module is the workaround until the Express
 * backend retires (PYTHON_BACKEND.md Phase 3).
 */

export const PRODUCT_TYPES = [
  "contactor",
  "drive",
  "electric_cylinder",
  "gearhead",
  "linear_actuator",
  "motor",
  "robot_arm",
] as const;
export type ProductTypeLiteral = (typeof PRODUCT_TYPES)[number];
