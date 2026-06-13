/**
 * Product list component with advanced filtering and sorting
 */

import { useState, useEffect, useMemo, useRef } from 'react';
import { useApp, isUnitSystem } from '../context/AppContext';
import { UnitSystem } from '../utils/unitConversion';
import { ProductType, Product } from '../types/models';
import { FilterCriterion, SortConfig, applyFilters, sortProducts, getAttributesForType, deriveAttributesFromRecords, mergeAttributesByKey, AttributeMetadata, getAvailableOperators, buildDefaultFiltersForType } from '../types/filters';
// Column order is authored in types/columnOrder.ts — edit that file to
// change what columns appear and in what order.
import { orderColumnAttributes, computeVisibleColumnAttributes } from '../types/columnOrder';
import { formatValue, formatNumber, formatRange, computeAutoColumnWidths } from '../utils/formatting';
import Tooltip from './ui/Tooltip';
import { displayUnit, convertValueUnit, convertMinMaxUnit } from '../utils/unitConversion';
import { numericFromValue } from '../utils/filterValues';
import { useColumnResize } from '../utils/hooks';
import {
  safeLoad,
  safeSave,
  isStringArray,
} from '../utils/localStorage';
import ColumnHeader from './ColumnHeader';
import ProductDetailModal from './ProductDetailModal';
import AttributeSelector from './AttributeSelector';
import Dropdown from './Dropdown';
import FeedbackModal from './ui/FeedbackModal';
import type { FeedbackCategory } from '../utils/feedback';
import { ADJACENT_TYPES, BuildSlot, check as compatCheck } from '../utils/compat';

// Human labels for the `availability` enum (the "Lead Time" column).
// The raw values are schema.org ItemAvailability tokens; the table
// shouldn't show `in_stock` to a buyer. Unknown / null values fall
// through to an empty cell (rendered as N/A by the table).
const AVAILABILITY_LABELS: Record<string, string> = {
  in_stock: 'In Stock',
  back_order: 'Back-order',
  out_of_stock: 'Out of Stock',
  pre_order: 'Pre-order',
  limited: 'Limited',
  discontinued: 'Discontinued',
};

const humanizeAvailability = (value: unknown): string =>
  typeof value === 'string' ? AVAILABILITY_LABELS[value] ?? value : '';

/**
 * The state slices ProductList resets when the user picks a new product
 * type. Extracted so the spillover bestiary (FRONTEND_TESTING.md) is
 * pinned by a unit test instead of drifting silently:
 *   L1: selectedProduct + clickPosition cleared (else the modal stays
 *       open across the type switch)
 *   L2: filters reset to the new type's curated defaults
 *   L3: sorts cleared
 *
 * The infinite-scroll window (`revealedRows`) is intentionally not in
 * this bundle — the existing `useEffect` that watches
 * `filters, sorts, itemsPerPage, rowDensity, productType` resets it
 * transitively when this bundle is applied.
 */
export interface ProductListResetState {
  filters: FilterCriterion[];
  sorts: SortConfig[];
  selectedProduct: Product | null;
  clickPosition: { x: number; y: number } | null;
}

export function defaultStateForType(type: ProductType): ProductListResetState {
  return {
    filters: buildDefaultFiltersForType(type),
    sorts: [],
    selectedProduct: null,
    clickPosition: null,
  };
}

export default function ProductList() {
  const { products, categories, loading, error, loadProducts, loadCategories, unitSystem, build, compatibleOnly, setCompatibleOnly, rowDensity } = useApp();
  const [productType, setProductType] = useState<ProductType>(null);
  const [filters, setFilters] = useState<FilterCriterion[]>([]);
  const [sorts, setSorts] = useState<SortConfig[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [clickPosition, setClickPosition] = useState<{ x: number; y: number } | null>(null);
  const [showSortSelector, setShowSortSelector] = useState(false);
  const [columnSelectorCursor, setColumnSelectorCursor] = useState<{ x: number; y: number } | null>(null);
  // Empty-state feedback launcher. Stored alongside its category so the
  // same modal can be opened from "no products in DB" (missing_product)
  // or "filtered to zero" (no_match) without splitting into two modals.
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>('no_match');
  // Both densities use infinite scroll; the reveal step is
  // density-driven (compact rows are shorter, so a step has to be
  // bigger to outrun the viewport). The discrete cozy pager retired
  // 2026-06: one scroll model, sticky headers, no page buttons.
  const itemsPerPage = rowDensity === 'compact' ? 50 : 25;
  // Infinite-scroll window: rows revealed past the initial step.
  // Reset whenever the data set, density, or product type changes.
  const [revealedRows, setRevealedRows] = useState<number>(0);
  // Column visibility model — two sets, because default visibility
  // depends on the attribute's *kind*:
  //
  // - ValueUnit / MinMaxUnit columns (numeric with a unit) are visible
  //   by default. User hides them via the × button; stored in
  //   `userHiddenKeys`.
  // - Every other kind (strings, booleans, arrays, bare ints/floats)
  //   is hidden by default — too noisy in a spec table — and must be
  //   explicitly pulled out of the "+ N hidden" dropdown. Those opt-in
  //   restores are stored in `userRestoredKeys`.
  //
  // Both sets persist across sessions. The old 'productListHiddenColumns'
  // key now stores userHiddenKeys.
  const [userHiddenKeys, setUserHiddenKeys] = useState<string[]>(() =>
    safeLoad('productListHiddenColumns', isStringArray, []),
  );
  useEffect(() => {
    safeSave('productListHiddenColumns', userHiddenKeys);
  }, [userHiddenKeys]);

  const [userRestoredKeys, setUserRestoredKeys] = useState<string[]>(() =>
    safeLoad('productListRestoredColumns', isStringArray, []),
  );
  useEffect(() => {
    safeSave('productListRestoredColumns', userRestoredKeys);
  }, [userRestoredKeys]);

  // Per-column unit overrides. Clicking a column's unit text in the
  // header flips that column's display system independently — without
  // dragging every other column with it. Unset columns inherit the
  // global default ('metric' on first load); the override map is the
  // only way the user can move things to imperial now that the toolbar
  // toggle is gone.
  const isColumnUnitMap = (v: unknown): v is Record<string, UnitSystem> => {
    if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
    return Object.values(v as Record<string, unknown>).every(
      x => typeof x === 'string' && isUnitSystem(x),
    );
  };
  const [columnUnitOverrides, setColumnUnitOverrides] = useState<Record<string, UnitSystem>>(() =>
    safeLoad('productListColumnUnits', isColumnUnitMap, {}),
  );
  useEffect(() => {
    safeSave('productListColumnUnits', columnUnitOverrides);
  }, [columnUnitOverrides]);
  const unitSystemFor = (key: string): UnitSystem =>
    columnUnitOverrides[key] ?? unitSystem;
  const toggleColumnUnit = (key: string) => {
    setColumnUnitOverrides(prev => {
      const current = prev[key] ?? unitSystem;
      return { ...prev, [key]: current === 'metric' ? 'imperial' : 'metric' };
    });
  };

  // Hard cap on simultaneously visible spec columns. Cozy keeps the
  // historical 10-column comfort zone; compact pushes to 16 for the
  // spreadsheet-style scan. Extras spill into the restore dropdown and
  // stay individually restorable.
  const MAX_VISIBLE_COLUMNS = rowDensity === 'compact' ? 16 : 10;
  const [addColumnBtnRef, setAddColumnBtnRef] = useState<HTMLButtonElement | null>(null);

  // Floor widths (px) for the part-number column. Compact tightens
  // further than cozy on the assumption that part numbers will truncate
  // gracefully (they're scannable abbreviations, not prose).
  const defaultPartWidth = rowDensity === 'compact' ? 130 : 160;
  // Spec column floor. Cozy keeps the slider+operator+value row legible
  // (~120px). Compact strips the slider in PR 2 so the floor drops to
  // ~90px — value+operator+unit fits comfortably.
  const defaultColWidth = rowDensity === 'compact' ? 90 : 120;
  const { columnWidths, setColumnWidths, startResize } = useColumnResize({ part_number: defaultPartWidth });

  // Keys that should never render as their own column. `part_number` is
  // pinned as the leading column by the existing render code; the rest are
  // identity, bookkeeping, or per-record URLs that aren't useful in a
  // spec table.
  const COLUMN_EXCLUDED_KEYS = useMemo(
    () =>
      new Set<string>([
        'part_number',
        'datasheet_url',
        'pages',
        'PK',
        'SK',
        'product_id',
        'product_type',
        'msrp_source_url',
        'msrp_fetched_at',
        'availability_source_url',
        'availability_fetched_at',
      ]),
    [],
  );

  // Full ordered column list — authored order from types/columnOrder.ts
  // first, then alphabetical for any unlisted keys. Excludes identity/
  // metadata keys. Does NOT yet filter by hidden set; we keep the full
  // list so AttributeSelector can tell what's hideable vs hidden.
  // `visibleColumnAttributes` below is the filtered view.
  const columnAttributes = useMemo<AttributeMetadata[]>(() => {
    if (!productType) return [];
    const staticAttrs = getAttributesForType(productType);
    const derivedAttrs = deriveAttributesFromRecords(products, productType);
    const merged = mergeAttributesByKey(staticAttrs, derivedAttrs).filter(
      a => !COLUMN_EXCLUDED_KEYS.has(a.key),
    );
    return orderColumnAttributes(merged, productType);
  }, [productType, products, COLUMN_EXCLUDED_KEYS]);

  // Columns actually rendered in the table. The visibility rules + cap
  // live in `computeVisibleColumnAttributes` so the contract is unit-
  // testable. User-restored columns always render (even past the cap)
  // — without that carve-out, "Add spec" silently dropped the user's
  // column whenever the default set already filled the cap.
  const visibleColumnAttributes = useMemo<AttributeMetadata[]>(
    () =>
      computeVisibleColumnAttributes(
        columnAttributes,
        userHiddenKeys,
        userRestoredKeys,
        MAX_VISIBLE_COLUMNS,
      ),
    [columnAttributes, userHiddenKeys, userRestoredKeys, MAX_VISIBLE_COLUMNS],
  );

  // Restore-dropdown candidates: everything the user could bring back —
  // explicit hides, cap overflow, and the hidden-by-default non-unit
  // kinds (strings, booleans, arrays, bare numbers).
  const hiddenColumnAttributes = useMemo<AttributeMetadata[]>(() => {
    const visibleKeys = new Set(visibleColumnAttributes.map(a => a.key));
    return columnAttributes.filter(a => !visibleKeys.has(a.key));
  }, [columnAttributes, visibleColumnAttributes]);

  // Auto-fit defaults from data: each column's width = P90 of its formatted-
  // value lengths in the loaded rows, header label as the floor, with
  // `part_number` floored at 2x so it always reveals more characters than
  // the rest. Computed fresh whenever the productType, visible-column set,
  // or loaded data changes — but a user's manual resize (tracked in
  // columnWidths) wins and is preserved across these transitions.
  useEffect(() => {
    if (!productType) return;
    const cols = [
      { key: 'part_number', displayName: 'Part Number' },
      ...visibleColumnAttributes.map(a => ({ key: a.key, displayName: a.displayName })),
    ];
    // Floor every spec column at defaultColWidth — the in-header slider +
    // operator + value pill needs ~120-145px to render without truncating.
    // Auto-fit widens past the floor when data warrants; the floor only
    // kicks in for naturally narrow columns.
    const specMin: Record<string, number> = { part_number: defaultPartWidth };
    for (const a of visibleColumnAttributes) specMin[a.key] = defaultColWidth;
    const auto = computeAutoColumnWidths({
      rows: products as unknown as Array<Record<string, unknown>>,
      columns: cols,
      density: rowDensity,
      unitSystem,
      perKeyMin: specMin,
    });
    setColumnWidths(prev => {
      const next: Record<string, number> = {};
      for (const col of cols) {
        next[col.key] = prev[col.key] ?? auto[col.key]
          ?? (col.key === 'part_number' ? defaultPartWidth : defaultColWidth);
      }
      return next;
    });
  }, [productType, visibleColumnAttributes, products, unitSystem, defaultPartWidth, defaultColWidth, setColumnWidths]);

  // Density flip → clobber to the new mode's auto-fit defaults so the
  // bumped px-per-char and padding actually take effect. Manual resizes
  // within a mode are preserved by the effect above; they only reset on
  // an intentional density toggle.
  useEffect(() => {
    if (!productType) return;
    const cols = [
      { key: 'part_number', displayName: 'Part Number' },
      ...visibleColumnAttributes.map(a => ({ key: a.key, displayName: a.displayName })),
    ];
    const specMin: Record<string, number> = { part_number: defaultPartWidth };
    for (const a of visibleColumnAttributes) specMin[a.key] = defaultColWidth;
    const auto = computeAutoColumnWidths({
      rows: products as unknown as Array<Record<string, unknown>>,
      columns: cols,
      density: rowDensity,
      unitSystem,
      perKeyMin: specMin,
    });
    setColumnWidths(() => {
      const next: Record<string, number> = {};
      for (const col of cols) {
        next[col.key] = auto[col.key]
          ?? (col.key === 'part_number' ? defaultPartWidth : defaultColWidth);
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowDensity]);

  // Load products and categories when product type changes or on mount
  useEffect(() => {
    if (productType !== null) {
      loadProducts(productType);
    }
    if (categories.length === 0) {
      loadCategories();
    }
  }, [productType, loadProducts, loadCategories]);

  // Default filter chips appear in column headers but ship with no value
  // ("any") so users see the full catalog on load — narrowing is the
  // user's job via the slider/operator. Pre-seeding to a P10 floor was
  // confusing when expected parts didn't appear in the initial result
  // set; removed deliberately. The chip's own slider auto-init in
  // FilterChip is also off for the same reason.

  // Add keyboard shortcut for opening filter (Ctrl+K)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const addButton = document.querySelector('.filter-bar-button.primary') as HTMLButtonElement;
        addButton?.click();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Handle product type change. Seed the filter list with this type's
  // curated defaults (e.g. motor → rated_torque, rated_speed) so the
  // sidebar opens with the chips an integrator almost always reaches
  // for. Chips land valueless; the user dials in the constraint.
  // Sort starts empty — rows render in the catalog's natural order
  // until the user clicks a column header.
  const handleProductTypeChange = (newType: ProductType) => {
    const reset = defaultStateForType(newType);
    setProductType(newType);
    setFilters(reset.filters);
    setSorts(reset.sorts);
    setSelectedProduct(reset.selectedProduct);
    setClickPosition(reset.clickPosition);
  };

  // Torque/speed keys — driven by the per-row gear-ratio cascade below.
  const TORQUE_KEYS = ['rated_torque', 'peak_torque'];
  const SPEED_KEYS = ['rated_speed'];

  // Discrete gear ratios. Direct drive plus multiples of 4 — real
  // gearhead catalogs cluster around these and a finer grid would give
  // false precision. Snap *up* (next ratio that's enough to meet the
  // torque target) rather than rounding, so the displayed value never
  // overstates what the motor would actually deliver.
  const GEAR_OPTIONS = [1, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 48, 64, 80, 100];
  const snapGearUp = (raw: number): number => {
    for (const g of GEAR_OPTIONS) {
      if (g >= raw) return g;
    }
    return GEAR_OPTIONS[GEAR_OPTIONS.length - 1];
  };

  const scaleSpec = (value: any, factor: number): any => {
    if (!value || typeof value !== 'object') return value;
    if ('value' in value && typeof value.value === 'number') {
      return { ...value, value: parseFloat((value.value * factor).toPrecision(4)) };
    }
    if ('min' in value || 'max' in value) {
      return {
        ...value,
        min: typeof value.min === 'number' ? parseFloat((value.min * factor).toPrecision(4)) : value.min,
        max: typeof value.max === 'number' ? parseFloat((value.max * factor).toPrecision(4)) : value.max,
      };
    }
    return value;
  };

  // Build-aware compat narrowing. When the user has anchored part of their
  // motion-system build and the active type is adjacent to one of those
  // anchors, drop products that strict-fail compat. Soft-partials (missing
  // data) stay visible — we can't prove them incompatible.
  const compatAnchors = useMemo(() => {
    if (!productType || !ADJACENT_TYPES[productType]) return [];
    return ADJACENT_TYPES[productType]
      .map(t => build[t as BuildSlot])
      .filter((p): p is Product => !!p);
  }, [productType, build]);

  const compatFilterActive = compatibleOnly && compatAnchors.length > 0;

  const compatNarrowed = useMemo(() => {
    if (!compatFilterActive) return products;
    return products.filter(p => {
      for (const anchor of compatAnchors) {
        try {
          if (compatCheck(p, anchor).status === 'fail') return false;
        } catch {
          // Pair unsupported — leave the row visible rather than silently hiding.
        }
      }
      return true;
    });
  }, [products, compatAnchors, compatFilterActive]);

  const compatHiddenCount = compatFilterActive ? products.length - compatNarrowed.length : 0;

  // Per-row gear ratio. Picks the smallest discrete ratio that lifts
  // each motor's rated/peak torque to clear the active torque filter,
  // then scales speed down by the same factor — every motor gets a
  // chance at the spec, and the user sees what speed they'd actually
  // get with that gear in place. With no torque filter, gearMap is
  // empty and gearedSource passes through unchanged.
  const torqueTargets = useMemo(() => {
    const out: { key: string; target: number }[] = [];
    for (const f of filters) {
      if (!TORQUE_KEYS.includes(f.attribute)) continue;
      if (f.operator !== '>=' && f.operator !== '>') continue;
      const v = numericFromValue(f.value);
      if (v == null || !Number.isFinite(v) || v <= 0) continue;
      out.push({ key: f.attribute, target: v });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  const { gearedSource, gearMap } = useMemo(() => {
    if (productType !== 'motor' || torqueTargets.length === 0) {
      return { gearedSource: compatNarrowed, gearMap: new Map<string, number>() };
    }
    const map = new Map<string, number>();
    const next = compatNarrowed.map(p => {
      let ratio = 1;
      for (const { key, target } of torqueTargets) {
        const motorVal = numericFromValue((p as any)[key]);
        if (motorVal == null || motorVal <= 0) continue;
        if (motorVal >= target) continue;
        const needed = snapGearUp(target / motorVal);
        if (needed > ratio) ratio = needed;
      }
      map.set(p.product_id, ratio);
      if (ratio === 1) return p;
      const copy: any = { ...p };
      for (const k of TORQUE_KEYS) {
        if (copy[k]) copy[k] = scaleSpec(copy[k], ratio);
      }
      for (const k of SPEED_KEYS) {
        if (copy[k]) copy[k] = scaleSpec(copy[k], 1 / ratio);
      }
      return copy as Product;
    });
    return { gearedSource: next, gearMap: map };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compatNarrowed, torqueTargets, productType]);

  /* Gear ratio is always visible on the motor view, not gated on
   * `torqueTargets.length > 0`. Per-row value comes from gearMap
   * (which is empty when there's no torque filter), so rows fall
   * back to ratio 1 → "—" until a torque filter promotes them
   * into a real gear pick. Keeps the column in the table's
   * mental model rather than appearing/disappearing as filters
   * are toggled. */
  const showGearColumn = productType === 'motor';

  const filteredProducts = useMemo(() => {
    return applyFilters(gearedSource, filters);
  }, [gearedSource, filters]);

  // Apply sorting to filtered products
  const sortedProducts = useMemo(
    () => sortProducts(filteredProducts, sorts.length > 0 ? sorts : null),
    [filteredProducts, sorts]
  );

  // The display set is now identical to the sorted set — linearization
  // already happened upstream. Kept as an alias so call sites read the
  // same as before.
  const displayProducts = sortedProducts;

  // Infinite-scroll window: reveal the first step, grow by another
  // step each time the sentinel nears the viewport.
  const windowSize = itemsPerPage + revealedRows;
  const paginatedProducts = useMemo(
    () => displayProducts.slice(0, windowSize),
    [displayProducts, windowSize]
  );

  const hasMoreRows = windowSize < displayProducts.length;

  // Reset the window when the data shape changes underneath us.
  // `rowDensity`/`itemsPerPage` are in the dep list because flipping
  // density mid-scroll changes the step size; `productType` because a
  // 5000-row window carried into a freshly selected type would render
  // it all in one frame.
  useEffect(() => {
    setRevealedRows(0);
  }, [filters, sorts, itemsPerPage, rowDensity, productType]);

  // IntersectionObserver-driven reveal. The sentinel sits below the
  // last row inside the grid's scroll container; once it enters the
  // viewport, we bump the revealed count by another `itemsPerPage`.
  // The effect re-arms after each reveal (paginatedProducts.length dep)
  // so a tall viewport keeps revealing until the sentinel leaves view.
  const loadMoreSentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!hasMoreRows) return;
    const node = loadMoreSentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some(e => e.isIntersecting)) {
          setRevealedRows(prev => prev + itemsPerPage);
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMoreRows, itemsPerPage, paginatedProducts.length]);

  // Column headers — label + unit per column. Unit strings flip through
  // `displayUnit()` so e.g. Nm → in·lb when the per-column unit override
  // is set to imperial.
  const getColumnHeaders = (): Array<{ key: string; label: string; unit: string | null }> => {
    return visibleColumnAttributes.map(attr => {
      const label = attr.displayName;
      const unit: string | null = attr.unit || null;
      return { key: attr.key, label, unit: unit ? displayUnit(unit, unitSystemFor(attr.key)) : null };
    });
  };

  // Extract just the numeric value from a spec (no unit). Converts
  // through the supplied unit system so a column's per-column unit
  // override is honored — pass unitSystemFor(key) at the call site.
  const extractNumericOnly = (value: any, sys: UnitSystem): string | null => {
    if (!value) return null;

    if (typeof value === 'number') return formatNumber(value);
    if (typeof value === 'string') return value;

    if (typeof value === 'object') {
      if ('value' in value && value.value !== null && value.value !== undefined) {
        if ('unit' in value) {
          const c = convertValueUnit(value, sys);
          return formatNumber(c.value);
        }
        return formatNumber(value.value);
      }

      const hasMin = 'min' in value && value.min !== null && value.min !== undefined;
      const hasMax = 'max' in value && value.max !== null && value.max !== undefined;

      if (hasMin && hasMax) {
        if ('unit' in value) {
          const c = convertMinMaxUnit(value, sys);
          return formatRange(c.min, c.max);
        }
        return formatRange(value.min, value.max);
      } else if (hasMin) {
        if ('unit' in value) {
          const c = convertValueUnit({ value: value.min, unit: value.unit }, sys);
          return formatNumber(c.value);
        }
        return formatNumber(value.min);
      } else if (hasMax) {
        if ('unit' in value) {
          const c = convertValueUnit({ value: value.max, unit: value.unit }, sys);
          return formatNumber(c.value);
        }
        return formatNumber(value.max);
      }
    }

    return null;
  };

  // Min/max for each filtered numeric attribute across the visible result set.
  // Drives the per-cell highlight gradient — see getProximityColor.
  const filteredAttrRanges = useMemo(() => {
    const map = new Map<string, { min: number; max: number }>();
    for (const filter of filters) {
      if (filter.value === undefined || filter.operator === '!=') continue;
      const path = filter.attribute;
      const [baseAttr, ...rest] = path.split('.');
      const nestedKeys = rest;
      let min = Infinity;
      let max = -Infinity;
      for (const p of filteredProducts) {
        const root = (p as any)[baseAttr];
        if (root == null) continue;
        const sub = nestedKeys.length === 0
          ? root
          : nestedKeys.reduce((acc: any, k) => (acc == null ? acc : acc[k]), root);
        const n = numericFromValue(sub);
        if (n == null || !Number.isFinite(n)) continue;
        if (n < min) min = n;
        if (n > max) max = n;
      }
      if (Number.isFinite(min) && Number.isFinite(max)) {
        map.set(path, { min, max });
      }
    }
    return map;
  }, [filters, filteredProducts]);

  // Tint a cell in a filtered column based on where its value sits in the
  // visible result set. Direction follows the operator: `>`/`>=` brightens
  // high values, `<`/`<=` brightens low values, `=` brightens values nearest
  // the filter value. Opacity is a hint, not a banner.
  const getProximityColor = (attribute: string, productValue: any): string => {
    const filter = filters.find(f => f.attribute === attribute || f.attribute.startsWith(attribute + '.'));
    if (!filter || filter.operator === '!=') return '';

    let numericProductValue: number | null = null;
    if (filter.attribute === attribute) {
      numericProductValue = numericFromValue(productValue);
    } else if (filter.attribute.startsWith(attribute + '.')) {
      const nestedKey = filter.attribute.split('.').slice(1).join('.');
      if (nestedKey && productValue && typeof productValue === 'object') {
        const sub = nestedKey.split('.').reduce((acc: any, k) => (acc == null ? acc : acc[k]), productValue);
        numericProductValue = numericFromValue(sub);
      }
    }
    if (numericProductValue === null) return '';

    const range = filteredAttrRanges.get(filter.attribute);
    const span = range ? range.max - range.min : 0;
    const pct = span > 0 && range
      ? Math.max(0, Math.min(1, (numericProductValue - range.min) / span))
      : 0.5;

    const numericFilterValue = numericFromValue(filter.value);

    let intensity: number;
    if (filter.operator === '=' && numericFilterValue !== null && numericFilterValue !== 0) {
      const percentDiff = Math.abs((numericProductValue - numericFilterValue) / numericFilterValue);
      intensity = Math.max(0, 1 - percentDiff * 5);
    } else if (filter.operator === '<' || filter.operator === '<=') {
      intensity = 1 - pct;
    } else {
      intensity = pct;
    }

    const opacity = 0.04 + intensity * 0.16;
    return `hsla(45, 60%, 45%, ${opacity.toFixed(3)})`;
  };

  const isSortedAttribute = (attribute: string): boolean => {
    return sorts.some(sort => sort.attribute === attribute);
  };

  // Per-sort-attribute sorted numeric arrays over the visible (post-filter,
  // post-linear-transform) set. Used to map each cell's value to its
  // empirical CDF position, so the sort highlight gradient is robust to
  // outliers — same idea as the slider's percentile mapping in FilterChip.
  const sortedAttrValues = useMemo(() => {
    const map = new Map<string, number[]>();
    for (const s of sorts) {
      const path = s.attribute;
      const [baseAttr, ...rest] = path.split('.');
      const values: number[] = [];
      for (const p of displayProducts) {
        const root = (p as any)[baseAttr];
        if (root == null) continue;
        const sub = rest.length === 0
          ? root
          : rest.reduce((acc: any, k) => (acc == null ? acc : acc[k]), root);
        const n = numericFromValue(sub);
        if (n != null && Number.isFinite(n)) values.push(n);
      }
      if (values.length > 0) {
        values.sort((a, b) => a - b);
        map.set(path, values);
      }
    }
    return map;
  }, [sorts, displayProducts]);

  // Tint a sorted column cell by the value's percentile rank in the visible
  // set. Direction follows the sort: desc → high values brightest (they're
  // at the top), asc → low values brightest. Linear min/max scaling would
  // let a single 10× outlier compress the rest of the column into a flat
  // band; percentile rank gives every row a fair share of the gradient.
  const getSortGradientColor = (attribute: string, productValue: any): string => {
    const sort = sorts.find(
      s => s.attribute === attribute || s.attribute.startsWith(attribute + '.'),
    );
    if (!sort) return '';

    let numericProductValue: number | null = null;
    if (sort.attribute === attribute) {
      numericProductValue = numericFromValue(productValue);
    } else if (sort.attribute.startsWith(attribute + '.')) {
      const nestedKey = sort.attribute.split('.').slice(1).join('.');
      if (nestedKey && productValue && typeof productValue === 'object') {
        const sub = nestedKey.split('.').reduce(
          (acc: any, k) => (acc == null ? acc : acc[k]),
          productValue,
        );
        numericProductValue = numericFromValue(sub);
      }
    }
    if (numericProductValue === null) return '';

    const sorted = sortedAttrValues.get(sort.attribute);
    if (!sorted || sorted.length < 2) return '';

    // Binary search for empirical CDF position (mirrors valueToPosition in FilterChip).
    let lo = 0;
    let hi = sorted.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sorted[mid] < numericProductValue) lo = mid + 1;
      else hi = mid;
    }
    let idx = lo;
    if (
      lo > 0 &&
      Math.abs(sorted[lo - 1] - numericProductValue) <
        Math.abs(sorted[lo] - numericProductValue)
    ) {
      idx = lo - 1;
    }
    const pct = idx / (sorted.length - 1);
    const intensity = sort.direction === 'asc' ? 1 - pct : pct;
    const opacity = 0.04 + intensity * 0.22;
    return `hsla(45, 70%, 50%, ${opacity.toFixed(3)})`;
  };

  const isFilteredAttribute = (attribute: string): boolean => {
    return filters.some(filter => {
      if (filter.value === undefined) return false;
      return filter.attribute === attribute || filter.attribute.startsWith(attribute + '.');
    });
  };

  const handleProductClick = (product: Product, event: React.MouseEvent) => {
    setClickPosition({ x: event.clientX, y: event.clientY });
    setSelectedProduct(product);
  };

  const handleCloseModal = () => {
    setSelectedProduct(null);
    setClickPosition(null);
  };

  const handleColumnSort = (attribute: string) => {
    const staticAttrs = getAttributesForType(productType || 'motor');
    const derivedAttrs = deriveAttributesFromRecords(products, productType);
    const allAttrs = mergeAttributesByKey(staticAttrs, derivedAttrs);
    const attributeMetadata = allAttrs.find(attr => attr.key === attribute);

    // Function-updater form is load-bearing: this handler is passed to
    // memo'd ColumnHeader instances via `onSort={() => handleColumnSort(...)}`.
    // ColumnHeader's `arePropsEqual` intentionally ignores callback identity
    // (see ColumnHeader.tsx near `function arePropsEqual`), so sibling
    // columns keep their STALE `onSort` callback wrapping a STALE
    // handleColumnSort, whose closures over `sorts`/`filters` are also
    // stale. Reading from the React-provided `prev` argument inside the
    // updater gets the latest state regardless of memo. Without this,
    // clicking sort on column B after setting a value on column A's slider
    // wiped column A's value (bug reported 2026-05-23, repro:
    // PageUp on Rated Torque → click sort header on any other column →
    // Rated Torque's value goes back to "any").
    setSorts(prev => {
      const existingSortIndex = prev.findIndex(s => s.attribute === attribute);
      if (existingSortIndex !== -1) {
        const existingSort = prev[existingSortIndex];
        if (existingSort.direction === 'asc') {
          const next = [...prev];
          next[existingSortIndex] = { ...existingSort, direction: 'desc' };
          return next;
        }
        return prev.filter((_, i) => i !== existingSortIndex);
      }
      if (attributeMetadata) {
        return [...prev, {
          attribute: attribute,
          direction: 'asc',
          displayName: attributeMetadata.displayName,
        }];
      }
      return prev;
    });

    // Mirror the sort with a filter chip for the same spec, so the user can
    // narrow as well as order in one click. Skip if a filter for this
    // attribute (or its nested children, e.g. `rated_voltage.min`) is
    // already present, and skip when we can't resolve metadata.
    if (!attributeMetadata) return;
    const availableOperators = getAvailableOperators(products, attribute);
    const hasComparison = availableOperators.some(
      op => op === '>' || op === '>=' || op === '<' || op === '<=',
    );
    const defaultOperator = hasComparison
      ? '>='
      : availableOperators.length > 0
        ? availableOperators[0]
        : '=';
    setFilters(prev => {
      if (prev.some(
        f => f.attribute === attribute || f.attribute.startsWith(attribute + '.'),
      )) {
        return prev;
      }
      return [
        ...prev,
        {
          attribute: attribute,
          mode: 'include',
          operator: defaultOperator,
          displayName: attributeMetadata.displayName,
        },
      ];
    });
  };

  // Hide a column: drop any active sort for it, add it to the user
  // hidden set, and clear any prior restore (non-unit kinds only —
  // unit-bearing columns can stay visible again just by removing them
  // from userHiddenKeys).
  const handleRemoveColumn = (attribute: string) => {
    // Function-updater form — see comment on handleColumnSort: this is
    // wired to ColumnHeader via `onRemove={...}`, whose identity is
    // ignored by the memo. Closure-read `sorts` would clobber sibling
    // updates that landed between renders.
    setSorts(prev => prev.filter(s => s.attribute !== attribute));
    setFilters(prev => prev.filter(f => f.attribute !== attribute));
    setUserHiddenKeys(prev => (prev.includes(attribute) ? prev : [...prev, attribute]));
    setUserRestoredKeys(prev => prev.filter(k => k !== attribute));
  };

  // Restore a column. Two paths:
  // - Unit-bearing column was user-hidden → remove from userHiddenKeys.
  // - Non-unit column (hidden by default) → add to userRestoredKeys.
  // With the cap locked (10 cozy / 16 compact), a restore that lands
  // past the cap won't appear until the user hides one of the visible
  // columns.
  const handleAddColumn = (attribute: ReturnType<typeof getAttributesForType>[0]) => {
    setUserHiddenKeys(userHiddenKeys.filter(k => k !== attribute.key));
    if (!attribute.nested && !userRestoredKeys.includes(attribute.key)) {
      setUserRestoredKeys([...userRestoredKeys, attribute.key]);
    }
    setShowSortSelector(false);
  };



  return (
    <div className="page-products-layout">
      <main className="results-main">
        {/* Single top toolbar — type selector, page-size, and result count
         * sit on the left; match summary and Clear sit on the right. This
         * is the only fixed chrome above the results grid; the previous
         * `.results-header` row is gone so the table gets the height back. */}
        <div className="page-toolbar">
          <div className="page-toolbar-left">
            <Dropdown<string>
              value={productType === null ? '' : productType}
              onChange={(v) => handleProductTypeChange(v === '' ? null : (v as ProductType))}
              disabled={categories.length === 0}
              ariaLabel="Product type"
              placeholder={categories.length === 0 ? 'Loading...' : 'Select Product Type...'}
              options={categories.map((c) => ({
                value: c.type,
                label: c.display_name,
              }))}
              className="page-toolbar-type-select"
            />
            <span className="results-count">
              {displayProducts.length === 0
                ? '0'
                : `1-${paginatedProducts.length}`
              } of {displayProducts.length}
            </span>
          </div>
          <div className="page-toolbar-right">
            {productType && compatNarrowed.length > 0 && (
              <div className="page-toolbar-match">
                <div className="page-toolbar-match-numbers">
                  <span className="page-toolbar-match-count">{filteredProducts.length}</span>
                  <span className="page-toolbar-match-divider">/</span>
                  <span className="page-toolbar-match-total">{compatNarrowed.length}</span>
                  <span className="page-toolbar-match-label">matching</span>
                  <span className="page-toolbar-match-percent">
                    {compatNarrowed.length === 0
                      ? '0%'
                      : `${Math.round((filteredProducts.length / compatNarrowed.length) * 100)}%`}
                  </span>
                </div>
                <div className="page-toolbar-match-bar" aria-hidden="true">
                  <div
                    className="page-toolbar-match-bar-fill"
                    style={{
                      width: `${compatNarrowed.length === 0 ? 0 : (filteredProducts.length / compatNarrowed.length) * 100}%`,
                    }}
                  />
                </div>
              </div>
            )}
            {filters.length > 0 && (
              <Tooltip content="Clear all filters and sorts">
                <button
                  type="button"
                  className="page-toolbar-clear"
                  onClick={() => {
                    setFilters([]);
                    setSorts([]);
                  }}
                >
                  Clear
                </button>
              </Tooltip>
            )}
          </div>
        </div>

        {error && (
          <div className="error" style={{ margin: '0.5rem 0' }}>
            {error}
            <button onClick={() => loadProducts(productType)} style={{ marginLeft: '0.8rem' }}>
              Retry
            </button>
          </div>
        )}

        {productType === null || (!loading && displayProducts.length === 0) ? (
          <div className="empty-state-minimal">
            <p>
              {productType === null
                ? 'Select a product type to begin'
                : products.length === 0
                ? 'No products in database'
                : 'No results match your specs'}
            </p>
            {productType !== null && (
              <div className="empty-state-feedback">
                <p className="empty-state-feedback-hint">
                  {products.length === 0
                    ? "Looking for a manufacturer or part we don't carry?"
                    : 'Specs too tight, or expecting a product to show up?'}
                </p>
                <button
                  type="button"
                  className="feedback-trigger"
                  onClick={() => {
                    setFeedbackCategory(
                      products.length === 0 ? 'missing_product' : 'no_match',
                    );
                    setFeedbackOpen(true);
                  }}
                >
                  {products.length === 0 ? 'Tell us what to add' : 'Tell us what you need'}
                </button>
              </div>
            )}
          </div>
        ) : (
          <>
            {compatFilterActive && compatHiddenCount > 0 && (
              <div className="compat-filter-banner" role="status">
                <span>
                  Showing {compatNarrowed.length} of {products.length} {productType}s compatible with{' '}
                  {compatAnchors.map(a => `${a.product_type} ${a.part_number || a.manufacturer}`).join(' & ')}
                  . {compatHiddenCount} hidden.
                </span>
                <button
                  type="button"
                  className="compat-filter-banner-toggle"
                  onClick={() => setCompatibleOnly(false)}
                >
                  Show all
                </button>
              </div>
            )}
            {!compatibleOnly && compatAnchors.length > 0 && (
              <div className="compat-filter-banner" role="status">
                <span>Compatibility filter is off. Build anchors: {compatAnchors.length}.</span>
                <button
                  type="button"
                  className="compat-filter-banner-toggle"
                  onClick={() => setCompatibleOnly(true)}
                >
                  Re-enable
                </button>
              </div>
            )}
            <div className="product-grid-scroll">
            <div className={`product-grid density-${rowDensity}`}>
            {/* Column headers */}
            <div className="product-grid-headers">
              <Tooltip content="Click anywhere to sort • click again to reverse, again to clear">
              <div
                className="product-grid-header-part clickable"
                style={{ width: columnWidths['part_number'] ?? defaultPartWidth }}
                onClick={() => handleColumnSort('part_number')}
              >
                Part Number
                <span className="sort-indicator">
                  {sorts.find(s => s.attribute === 'part_number')?.direction === 'asc' && '↑'}
                  {sorts.find(s => s.attribute === 'part_number')?.direction === 'desc' && '↓'}
                  {sorts.some(s => s.attribute === 'part_number') && sorts.length > 1 &&
                    <span className="sort-order">{sorts.findIndex(s => s.attribute === 'part_number') + 1}</span>
                  }
                </span>
                <div className="col-resize-handle" onMouseDown={(e) => startResize('part_number', e)} />
              </div>
              </Tooltip>
              {/* Gear ratio (computed). Always visible on the motor view;
                  displays '—' for direct drive (gearMap unset or ratio 1).
                  Per-row value comes from gearMap; rated_torque and
                  rated_speed cells display the post-gear values so the
                  table is an accurate depiction of what each motor would
                  deliver at the chosen ratio. */}
              {showGearColumn && (
                <div className="product-grid-header-item computed-col" style={{ width: 70 }}>
                  <div className="product-grid-header-label">Gear</div>
                  <div className="product-grid-header-unit">(ratio)</div>
                </div>
              )}
              {/* Spec columns — each header is now a self-contained filter:
                  anchored histogram on top, sortable label, slider, and
                  inline operator/value/unit readout. Filters lazily promote
                  when the user first drags a column's slider. */}
              {(() => {
                const headers = getColumnHeaders();
                return headers.map((header) => {
                  const sortIndex = sorts.findIndex(s => s.attribute === header.key);
                  const sortConfig = sortIndex !== -1 ? sorts[sortIndex] : null;
                  const filter = filters.find(
                    f => f.attribute === header.key
                      || f.attribute.startsWith(header.key + '.')
                  ) ?? null;
                  const attribute = visibleColumnAttributes.find(a => a.key === header.key);
                  if (!attribute) return null;

                  return (
                    <ColumnHeader
                      key={header.key}
                      attribute={attribute}
                      label={header.label}
                      products={displayProducts}
                      allProducts={compatNarrowed}
                      filter={filter}
                      allFilters={filters}
                      sortConfig={sortConfig}
                      sortIndex={sortIndex}
                      totalSorts={sorts.length}
                      width={columnWidths[header.key] ?? defaultColWidth}
                      unitSystem={unitSystemFor(header.key)}
                      onUnitToggle={() => toggleColumnUnit(header.key)}
                      onFilterChange={(updated) => {
                        // Function-updater form is load-bearing here:
                        // ColumnHeader is memo'd with `arePropsEqual` that
                        // intentionally ignores callback identity (see the
                        // comment in ColumnHeader.tsx near `function
                        // arePropsEqual`). When a sibling column's filter
                        // value changes, OTHER columns skip re-render via
                        // memo and keep their stale onFilterChange closures.
                        // If we read `filters` from the closure, a later
                        // append on a different column clobbers values set
                        // on already-updated columns. Reading `prev` from
                        // React gets the latest state regardless of memo.
                        if (updated == null) {
                          setFilters(prev => prev.filter(f => f !== filter));
                        } else if (filter) {
                          setFilters(prev =>
                            prev.map(f => (f === filter ? updated : f))
                          );
                        } else {
                          setFilters(prev => [...prev, updated]);
                        }
                      }}
                      onSort={() => handleColumnSort(header.key)}
                      onRemove={() => handleRemoveColumn(header.key)}
                      onResizeStart={(e) => startResize(header.key, e)}
                    />
                  );
                });
              })()}
              {/* Restore-hidden-column button — only rendered when
                  there's something to restore. */}
              {hiddenColumnAttributes.length > 0 && (
                <Tooltip content={`Add spec column (${hiddenColumnAttributes.length} available)`}>
                  <button
                    ref={(el) => setAddColumnBtnRef(el)}
                    className="add-column-btn"
                    onClick={(e) => {
                      setColumnSelectorCursor({ x: e.clientX, y: e.clientY });
                      setShowSortSelector(true);
                    }}
                  >
                    + Add Spec
                  </button>
                </Tooltip>
              )}
            </div>

              {paginatedProducts.map((product) => (
                <div
                  key={product.product_id}
                  className="product-card-minimal"
                  onClick={(e) => handleProductClick(product, e)}
                >
                  {/* Product info - first grid cell. Part number is plain
                      text; clicking the row opens the detail modal, which
                      hosts the (intentionally singular) datasheet link. */}
                  <div className="product-card-info">
                    <div className="product-info-part">
                      {product.part_number || 'N/A'}
                    </div>
                  </div>

                  {/* Gear ratio cell — '—' for direct drive, 'N:1'
                      otherwise. */}
                  {showGearColumn && (() => {
                    const r = gearMap.get(product.product_id) ?? 1;
                    return (
                      <div className="spec-header-item computed-cell">
                        <div className="spec-header-value">{r === 1 ? '—' : `${r}:1`}</div>
                      </div>
                    );
                  })()}

                  {/* Spec values - each as a direct grid cell. Unit
                      conversion uses the per-column override so a column
                      flipped to imperial doesn't drag its neighbors. */}
                  {getColumnHeaders().map((header) => {
                    const attrKey = header.key;
                    const productValue = (product as any)[attrKey];
                    const cellSys = unitSystemFor(attrKey);
                    const numericValue = extractNumericOnly(productValue, cellSys);
                    const proximityColor = getProximityColor(attrKey, productValue);
                    const hasProximityColor = !!proximityColor;
                    const sortColor = !hasProximityColor
                      ? getSortGradientColor(attrKey, productValue)
                      : '';
                    const cellColor = proximityColor || sortColor || undefined;

                    return (
                      <div
                        key={`default-value-${attrKey}`}
                        className={`spec-header-item ${
                          !hasProximityColor && isFilteredAttribute(attrKey) ? 'spec-header-item-filtered' :
                          !hasProximityColor && isSortedAttribute(attrKey) ? 'spec-header-item-sorted' : ''
                        }`}
                        style={{
                          backgroundColor: cellColor
                        }}
                      >
                        <div className="spec-header-value">{attrKey === 'availability' ? humanizeAvailability(productValue) : (numericValue || formatValue(productValue, 0, 5, cellSys))}</div>
                      </div>
                    );
                  })}

                </div>
              ))}
            </div>

            {/* Infinite-scroll affordance. Lives INSIDE the grid's
                scroll container so the IntersectionObserver fires as
                the user nears the bottom of the grid's own scrollport
                (the headers stick to the same scrollport). */}
            <div
              ref={loadMoreSentinelRef}
              className="infinite-scroll-sentinel"
              aria-hidden="true"
            >
              {hasMoreRows ? (
                <span className="infinite-scroll-status">
                  Loading more… ({paginatedProducts.length} of {displayProducts.length})
                </span>
              ) : displayProducts.length > 0 ? (
                <span className="infinite-scroll-status">
                  End of {displayProducts.length} results
                </span>
              ) : null}
            </div>
            </div>
          </>
        )}
      </main>

      <ProductDetailModal
        product={selectedProduct}
        onClose={handleCloseModal}
        clickPosition={clickPosition}
      />

      {/* Attribute Selector Modal — shows currently-hidden columns so
          the user can restore them. Passes hiddenColumnAttributes (not
          the full list) so only hideable items appear. */}
      <AttributeSelector
        attributes={hiddenColumnAttributes}
        onSelect={handleAddColumn}
        onClose={() => {
          setShowSortSelector(false);
          setColumnSelectorCursor(null);
        }}
        isOpen={showSortSelector}
        anchorElement={addColumnBtnRef}
        cursorPosition={columnSelectorCursor}
      />

      <FeedbackModal
        open={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        defaultCategory={feedbackCategory}
        context={{ productType, filters }}
      />
    </div>
  );
}
