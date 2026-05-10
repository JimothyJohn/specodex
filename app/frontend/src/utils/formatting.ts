/**
 * Formatting utilities for displaying product properties
 */

import { isPlaceholder } from './sanitize';
import {
  UnitSystem,
  convertValueUnit,
  convertMinMaxUnit,
} from './unitConversion';

/**
 * Format a snake_case property key into a properly capitalized label
 *
 * Handles common acronyms that should be fully capitalized (IP, AC, DC, etc.)
 * and converts snake_case to Title Case for regular words.
 *
 * Examples:
 * - formatPropertyLabel('ip_rating') → 'IP Rating'
 * - formatPropertyLabel('rated_voltage') → 'Rated Voltage'
 * - formatPropertyLabel('ac_input') → 'AC Input'
 * - formatPropertyLabel('pwm_frequency') → 'PWM Frequency'
 *
 * @param key - Snake_case property key
 * @returns Properly formatted display label
 */
export const formatPropertyLabel = (key: string): string => {
  // Common acronyms that should be fully capitalized
  const acronyms = new Set([
    'ip', 'ac', 'dc', 'pwm', 'rpm', 'emf', 'rms', 'led', 'usb', 'io',
    'api', 'url', 'id', 'can', 'uart', 'spi', 'i2c', 'nema', 'iec',
    'din', 'ansi', 'iso', 'ul', 'ce', 'fcc', 'rohs', 'pid', 'plc',
    'hmi', 'vfd', 'hp', 'kw', 'fps', 'dpi', 'psi', 'gpm', 'cfm'
  ]);

  return key
    .split('_')
    .map(word => {
      const lowerWord = word.toLowerCase();
      // If it's an acronym, uppercase the entire word
      if (acronyms.has(lowerWord)) {
        return word.toUpperCase();
      }
      // Otherwise, capitalize first letter only
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(' ');
};

/**
 * Recursively format a value for display, handling nested objects and arrays
 *
 * Missing/placeholder values render as an empty string so the gaps in our spec
 * coverage are visually obvious instead of being papered over with "N/A".
 *
 * Handles common patterns:
 * - Primitives (strings, numbers, booleans)
 * - null/undefined/placeholder → ''
 * - Arrays → comma-separated values
 * - Objects with value+unit → "value unit"
 * - Objects with min+max+unit → "min-max unit"
 * - Objects with nominal+unit → "nominal unit"
 * - Objects with rated+unit → "rated unit"
 * - Nested objects → formatted key-value pairs
 *
 * @param value - The value to format
 * @param depth - Current recursion depth (prevents infinite recursion)
 * @param maxDepth - Maximum recursion depth (default: 5)
 * @returns Formatted string representation
 */
export const formatValue = (
  value: any,
  depth: number = 0,
  maxDepth: number = 5,
  system: UnitSystem = 'metric',
): string => {
  // Prevent infinite recursion
  if (depth > maxDepth) {
    return '[Max depth exceeded]';
  }

  if (isPlaceholder(value)) {
    return '';
  }

  // Handle primitives
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  // Handle arrays
  if (Array.isArray(value)) {
    if (value.length === 0) return '';

    // Check if array contains objects with value/unit pattern
    if (value.length > 0 && typeof value[0] === 'object' && value[0] !== null) {
      if ('value' in value[0] && 'unit' in value[0]) {
        const converted = value.map(item => convertValueUnit(item, system));
        const formattedValues = converted.map(item => String(item.value)).join(', ');
        const commonUnit = converted[0].unit;
        return `${formattedValues} ${commonUnit}`;
      }

      // Check if array contains objects with min/max/unit pattern
      if ('min' in value[0] && 'max' in value[0] && 'unit' in value[0]) {
         const converted = value.map(item => convertMinMaxUnit(item, system));
         const formattedValues = converted.map(item => `${item.min}-${item.max}`).join(', ');
         const commonUnit = converted[0].unit;
         return `${formattedValues} ${commonUnit}`;
      }
    }

    // Otherwise, recursively format each element
    return value.map(item => formatValue(item, depth + 1, maxDepth, system)).join(', ');
  }

  // Handle objects
  if (typeof value === 'object') {
    // Pattern: { value, unit }
    if ('value' in value && 'unit' in value) {
      const c = convertValueUnit(value, system);
      return `${c.value} ${c.unit}`;
    }

    // Pattern: { min, max, unit }
    if ('min' in value && 'max' in value && 'unit' in value) {
      const c = convertMinMaxUnit(value, system);
      return `${c.min}-${c.max} ${c.unit}`;
    }

    // Pattern: { nominal, unit }
    if ('nominal' in value && 'unit' in value) {
      const c = convertValueUnit({ value: value.nominal, unit: value.unit }, system);
      return `${c.value} ${c.unit}`;
    }

    // Pattern: { rated, unit }
    if ('rated' in value && 'unit' in value) {
      const c = convertValueUnit({ value: value.rated, unit: value.unit }, system);
      return `${c.value} ${c.unit}`;
    }

    // Pattern: { min, max } without unit
    if ('min' in value && 'max' in value && !('unit' in value)) {
      const otherKeys = Object.keys(value).filter(k => k !== 'min' && k !== 'max');
      if (otherKeys.length === 0) {
        return `${value.min}-${value.max}`;
      }
    }

    // Pattern: Multiple numeric properties with a common unit
    const entries = Object.entries(value);
    const unitEntry = entries.find(([key]) => key.toLowerCase() === 'unit');

    if (unitEntry) {
      const canonicalUnit = String(unitEntry[1]);
      const numericEntries = entries.filter(([key, val]) =>
        key.toLowerCase() !== 'unit' && typeof val === 'number'
      );

      if (numericEntries.length > 0 && numericEntries.length === entries.length - 1) {
        // All non-unit entries are numeric — convert each through the
        // common unit so dimensions {width, height, depth, unit: "mm"}
        // flip cleanly when the unit toggle flips.
        const converted = numericEntries.map(([key, val]) => {
          const c = convertValueUnit({ value: val as number, unit: canonicalUnit }, system);
          return [key, c.value, c.unit] as const;
        });
        const finalUnit = converted[0]?.[2] ?? canonicalUnit;
        const formatted = converted.map(([key, val]) =>
          `${formatPropertyLabel(key)}: ${val}`
        ).join(', ');
        return `${formatted} ${finalUnit}`;
      }
    }

    // Generic nested object: format as key-value pairs
    const formattedEntries = entries
      .filter(([key]) => key.toLowerCase() !== 'unit') // Filter out standalone unit keys
      .map(([key, val]) => ({ label: formatPropertyLabel(key), formattedVal: formatValue(val, depth + 1, maxDepth, system) }))
      .filter(({ formattedVal }) => formattedVal !== '')
      .map(({ label, formattedVal }) => `${label}: ${formattedVal}`);

    if (formattedEntries.length === 0) return '';

    // If there was a unit at this level, append it (in the active system)
    if (unitEntry) {
      const canonicalUnit = String(unitEntry[1]);
      const c = convertValueUnit({ value: 0, unit: canonicalUnit }, system);
      return `${formattedEntries.join(', ')} ${c.unit}`;
    }

    return formattedEntries.join(', ');
  }

  // Fallback
  return String(value);
};

/**
 * Compute default column widths from the data actually loaded.
 *
 * Width is driven by the P90 of formatted-value lengths per column, so a
 * column adapts to whatever's in front of it without one freak outlier
 * (a 60-char free-text spec) blowing out every row. Header label length
 * is the floor — column titles never get truncated. Per-column min/max
 * clamps stop pathologically narrow or wide columns.
 *
 * Returns a map of `key → px width`. Manual user resizes (tracked in
 * the parent state) win over these defaults; the caller decides how to
 * merge.
 */
export interface AutoWidthInputs {
  rows: Array<Record<string, unknown>>;
  columns: Array<{ key: string; displayName: string }>;
  /** 'cozy' | 'compact' — drives px-per-char and padding. Compact is
   *  significantly denser than cozy (smaller font, tighter padding). */
  density: 'cozy' | 'compact';
  unitSystem: UnitSystem;
  /** Per-column floor. Use for `part_number`, which we want extra-wide
   *  even if its values happen to fit in fewer chars on a thin dataset. */
  perKeyMin?: Record<string, number>;
  /** Cap so a single absurdly long value doesn't dominate. */
  maxPx?: number;
  /** Percentile (0..1). Default 0.90 — 90% of values fit, top 10% truncate. */
  percentile?: number;
}

export const computeAutoColumnWidths = (
  inputs: AutoWidthInputs,
): Record<string, number> => {
  const {
    rows,
    columns,
    density,
    unitSystem,
    perKeyMin = {},
    maxPx = 400,
    percentile = 0.9,
  } = inputs;

  // Mono-ish glyph ≈ 0.6em. Cozy is the historical compact (~0.85rem
  // cells); the new compact tightens font + padding for spreadsheet feel.
  const charPx = density === 'compact' ? 6.0 : 7.5;
  const cellPaddingPx = density === 'compact' ? 14 : 22;
  const minPx = density === 'compact' ? 40 : 60;

  const widths: Record<string, number> = {};
  for (const col of columns) {
    const lengths: number[] = [];
    for (const row of rows) {
      const formatted = formatValue(row[col.key], 0, 5, unitSystem);
      if (formatted) lengths.push(formatted.length);
    }
    lengths.sort((a, b) => a - b);

    const pIdx = Math.max(0, Math.min(lengths.length - 1,
      Math.floor(lengths.length * percentile)));
    const dataChars = lengths.length > 0 ? lengths[pIdx] : 0;
    const headerChars = col.displayName.length;
    const chars = Math.max(dataChars, headerChars);

    const desiredPx = Math.round(chars * charPx + cellPaddingPx);
    const floor = Math.max(minPx, perKeyMin[col.key] ?? 0);
    widths[col.key] = Math.min(maxPx, Math.max(floor, desiredPx));
  }
  return widths;
};
