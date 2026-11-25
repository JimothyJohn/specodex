/**
 * Product list component with advanced filtering and sorting
 */

import { useState, useEffect, useMemo, useRef } from 'react';
import { useApp, isUnitSystem } from '../context/AppContext';
import { UnitSystem } from '../utils/unitConversion';
import { ProductType, Product } from '../types/models';
import { FilterCriterion, SortConfig, applyFilters, sortProducts, getAttributesForType, deriveAttributesFromRecords, mergeAttributesByKey, AttributeMetadata, getAvailableOperators, buildDefaultFiltersForType, DERIVATION_EXCLUDED_KEYS } from '../types/filters';
// Column order is authored in types/columnOrder.ts — edit that file to
// change what columns appear and in what order.
import { orderColumnAttributes, computeVisibleColumnAttributes, computeFillRates } from '../types/columnOrder';
import VendorDrawer from './VendorDrawer';
import { formatValue, formatNumber, formatRange, computeAutoColumnWidths } from '../utils/formatting';
import Tooltip from './ui/Tooltip';
import { convertValueUnit, convertMinMaxUnit } from '../utils/unitConversion';
import { extractNumeric, numericFromValue } from '../utils/filterValues';
import { applyGearCascade, outputTorqueTargets, GEARED_KEYS } from '../utils/gearCascade';
import { useColumnResize } from '../utils/hooks';
import {
  safeLoad,
  safeSave,
  isStringArray,
} from '../utils/localStorage';
import ColumnHeader from './ColumnHeader';
import ActiveFilterChips from './ActiveFilterChips';
import ProductDetailModal from './ProductDetailModal';
import AttributeSelector from './AttributeSelector';
import LensSelector from './LensSelector';
import { computeLensColumns, isLensArray, type Lens } from '../types/lens';
import Dropdown from './Dropdown';
import FeedbackModal from './ui/FeedbackModal';
import { ProgressBar } from './ui/ProgressBar';
import type { FeedbackCategory } from '../utils/feedback';
import { ADJACENT_TYPES, BuildSlot, check as compatCheck } from '../utils/compat';

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

export type ListBodyState = 'pick-type' | 'loading' | 'no-products' | 'no-match' | 'rows';

/**
 * Decide what the listing body renders. Pure so the contract is
 * unit-testable without mounting the page.
 *
 * 'no-match' is the load-bearing case: products exist but the active
 * filters (or compat narrowing) eliminate every row. The grid — and its
 * column headers, which host the filter controls — MUST stay mounted so
 * the user can loosen the filters. Before 2026-09-13 this state shared a
 * branch with 'no-products' and unmounted the headers, stranding the
 * user with no way to readjust.
 */
export function resolveListBody(args: {
  productType: ProductType | null;
  loading: boolean;
  totalProducts: number;
  displayedProducts: number;
}): ListBodyState {
  const { productType, loading, totalProducts, displayedProducts } = args;
  if (productType === null) return 'pick-type';
  if (loading && totalProducts === 0) return 'loading';
  if (totalProducts === 0) return 'no-products';
  if (!loading && displayedProducts === 0) return 'no-match';
  return 'rows';
}

export function defaultStateForType(type: ProductType): ProductListResetState {
  return {
    filters: buildDefaultFiltersForType(type),
    sorts: [],
    selectedProduct: null,
    clickPosition: null,
  };
}

// Keys that never render as their own spec column: the identity /
// bookkeeping / commercial keys the filter UI already excludes, plus
// `part_number`, which the render code pins as the leading column.
const COLUMN_EXCLUDED_KEYS: ReadonlySet<string> = new Set([
  ...DERIVATION_EXCLUDED_KEYS,
  'part_number',
]);

/** True when a filter/sort on `path` targets column `key` or one of its
 *  nested children (e.g. `rated_voltage.min` targets `rated_voltage`). */
const targetsColumn = (path: string, key: string): boolean =>
  path === key || path.startsWith(key + '.');

/** Numeric reading of a cell for a filter/sort `path` that targets the
 *  cell's column `key` — the cell value itself, or its nested child. */
const cellNumeric = (cellValue: any, key: string, path: string): number | null => {
  if (path === key) return numericFromValue(cellValue);
  if (!cellValue || typeof cellValue !== 'object') return null;
  return extractNumeric(cellValue, path.slice(key.length + 1));
};

/** Auto-fit width for the part-number column plus every visible spec
 *  column, floored per column and with the floor as the fallback. */
function autoFitColumnWidths(args: {
  products: readonly Product[];
  columns: readonly AttributeMetadata[];
  density: 'compact' | 'cozy';
  unitSystem: UnitSystem;
  partMin: number;
  colMin: number;
}): Record<string, number> {
  const { products, columns, density, unitSystem, partMin, colMin } = args;
  const cols = [
    { key: 'part_number', displayName: 'Part Number' },
    ...columns.map(a => ({ key: a.key, displayName: a.displayName })),
  ];
  // Floor every spec column at colMin (see defaultColWidth for why it is
  // 96/72 and not the old 120/90). Auto-fit widens past the floor when
  // data warrants; the floor only kicks in for naturally narrow columns.
  const perKeyMin: Record<string, number> = {};
  for (const col of cols) perKeyMin[col.key] = col.key === 'part_number' ? partMin : colMin;
  const auto = computeAutoColumnWidths({
    rows: products as unknown as Array<Record<string, unknown>>,
    columns: cols,
    density,
    unitSystem,
    perKeyMin,
  });
  const widths: Record<string, number> = {};
  for (const col of cols) widths[col.key] = auto[col.key] ?? perKeyMin[col.key];
  return widths;
}

export default function ProductList() {
  const { products, categories, loading, loadProgress, error, loadProducts, loadCategories, unitSystem, build, compatibleOnly, setCompatibleOnly, rowDensity } = useApp();
  const [productType, setProductType] = useState<ProductType>(null);
  const [filters, setFilters] = useState<FilterCriterion[]>([]);
  const [sorts, setSorts] = useState<SortConfig[]>([]);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [clickPosition, setClickPosition] = useState<{ x: number; y: number } | null>(null);
  const [showColumnSelector, setShowColumnSelector] = useState(false);
  const [columnSelectorCursor, setColumnSelectorCursor] = useState<{ x: number; y: number } | null>(null);
  // Empty-state feedback launcher. Stored alongside its category so the
  // same modal can be opened from "no products in DB" (missing_product)
  // or "filtered to zero" (no_match) without splitting into two modals.
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory>('no_match');
  // Vendor drawer — opened by clicking a manufacturer name in the table.
  // Shows the manufacturer's cited facts from the vendor-facts registry
  // (the one commercial surface that survived the 2026-07-24 removal:
  // registry facts are citation-required, unlike the per-product data).
  const [vendorDrawerFor, setVendorDrawerFor] = useState<string | null>(null);
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

  // Form / Fit / Function lenses (types/lens.ts). Empty = normal view.
  const [activeLenses, setActiveLenses] = useState<Lens[]>(() =>
    safeLoad('productListLenses', isLensArray, []),
  );
  useEffect(() => {
    safeSave('productListLenses', activeLenses);
  }, [activeLenses]);
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
  // Floor for spec columns. The 120/90 floors dated from the in-header
  // slider + operator + value pill (UI_CLEANUP Phase 1); the
  // popover-per-column header (Phase 2) only needs the label — which
  // wraps — the sparkline, and the "any" trigger, so short numeric
  // columns can sit at ~96px cozy / ~72px compact. Auto-fit still widens
  // past this when the data warrants (computeAutoColumnWidths).
  const defaultColWidth = rowDensity === 'compact' ? 72 : 96;
  const { columnWidths, setColumnWidths, startResize } = useColumnResize({ part_number: defaultPartWidth });

  // Full ordered column list — authored order from types/columnOrder.ts
  // first, then alphabetical for any unlisted keys. Excludes identity/
  // metadata keys. Does NOT yet filter by hidden set; we keep the full
  // list so AttributeSelector can tell what's hideable vs hidden.
  // `visibleColumnAttributes` below is the filtered view.
  const allAttributes = useMemo<AttributeMetadata[]>(() => {
    if (!productType) return [];
    return mergeAttributesByKey(
      getAttributesForType(productType),
      deriveAttributesFromRecords(products, productType),
    );
  }, [productType, products]);

  const columnAttributes = useMemo<AttributeMetadata[]>(
    () =>
      orderColumnAttributes(
        allAttributes.filter(a => !COLUMN_EXCLUDED_KEYS.has(a.key)),
        productType,
      ),
    [allAttributes, productType],
  );

  // Columns actually rendered in the table. The visibility rules + cap
  // live in `computeVisibleColumnAttributes` so the contract is unit-
  // testable. User-restored columns always render (even past the cap)
  // — without that carve-out, "Add spec" silently dropped the user's
  // column whenever the default set already filled the cap.
  // Fill rate per column over the loaded rows — the default-visible rule
  // drops unit-bearing columns that are mostly empty (UI_CLEANUP S2), so
  // a 90 %-empty column doesn't hold the widest default slot. Curated
  // `defaultVisible: true` columns and user restores are exempt.
  const columnFillRates = useMemo(
    () =>
      computeFillRates(
        products as unknown as readonly Record<string, unknown>[],
        columnAttributes.map(a => a.key),
      ),
    [products, columnAttributes],
  );

  const visibleColumnAttributes = useMemo<AttributeMetadata[]>(
    () =>
      computeLensColumns(
        columnAttributes,
        activeLenses,
        userHiddenKeys,
        MAX_VISIBLE_COLUMNS,
        columnFillRates,
      ) ??
      computeVisibleColumnAttributes(
        columnAttributes,
        userHiddenKeys,
        userRestoredKeys,
        MAX_VISIBLE_COLUMNS,
        columnFillRates,
      ),
    [columnAttributes, activeLenses, userHiddenKeys, userRestoredKeys, MAX_VISIBLE_COLUMNS, columnFillRates],
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
  const autoWidths = () =>
    autoFitColumnWidths({
      products,
      columns: visibleColumnAttributes,
      density: rowDensity,
      unitSystem,
      partMin: defaultPartWidth,
      colMin: defaultColWidth,
    });

  useEffect(() => {
    if (!productType) return;
    const auto = autoWidths();
    setColumnWidths(prev => {
      const next: Record<string, number> = {};
      for (const key of Object.keys(auto)) next[key] = prev[key] ?? auto[key];
      return next;
    });
  }, [productType, visibleColumnAttributes, products, unitSystem, defaultPartWidth, defaultColWidth, setColumnWidths]);

  // Density flip → clobber to the new mode's auto-fit defaults so the
  // bumped px-per-char and padding actually take effect. Manual resizes
  // within a mode are preserved by the effect above; they only reset on
  // an intentional density toggle.
  useEffect(() => {
    if (!productType) return;
    setColumnWidths(autoWidths());
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

  // Gearbox cascade (motor view). Rated Torque / Peak Torque / Rated
  // Speed always show the motor's own shaft values; a `>=` filter on the
  // computed "Output Torque (with gearbox)" column picks a per-row ratio
  // and fills output_torque / output_speed. Cells, header histograms,
  // sliders, filters and sorts all read the same fields — see
  // utils/gearCascade.ts for why the old in-place rewrite of
  // rated_torque was split out.
  //
  // Keyed on the output-torque targets — the only part of `filters` the
  // cascade reads — not on `filters` itself. Otherwise every filter
  // change (each slider drag tick on any column) re-copies every motor
  // row, and the fresh `gearedSource` identity defeats ColumnHeader's
  // memo and its per-column range/option memos.
  const gearTargetsKey = outputTorqueTargets(filters).join(',');
  const { products: gearedSource, gearMap, active: gearActive } = useMemo(
    () => applyGearCascade(compatNarrowed, filters, productType),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [compatNarrowed, gearTargetsKey, productType],
  );

  /* Gear ratio column appears on the motor view while an output-torque
   * filter is active — the only time gearMap can hold a ratio other
   * than 1 (UI_CLEANUP N2: no always-empty default column). The output
   * torque/speed headers carry a "geared" marker while it is active. */
  const showGearColumn = productType === 'motor' && gearActive;
  const cascadeKeys: readonly string[] = showGearColumn ? GEARED_KEYS : [];

  const filteredProducts = useMemo(() => {
    return applyFilters(gearedSource, filters);
  }, [gearedSource, filters]);

  // Apply sorting to filtered products
  const displayProducts = useMemo(
    () => sortProducts(filteredProducts, sorts.length > 0 ? sorts : null),
    [filteredProducts, sorts]
  );

  const listBody = resolveListBody({
    productType,
    loading,
    totalProducts: products.length,
    displayedProducts: displayProducts.length,
  });

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
      let min = Infinity;
      let max = -Infinity;
      for (const p of filteredProducts) {
        const n = extractNumeric(p, path);
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
  const getProximityColor = (filter: FilterCriterion | null, attribute: string, productValue: any): string => {
    if (!filter || filter.operator === '!=') return '';
    // Default chips are seeded valueless ("any"); until the user dials
    // in a threshold there is nothing to be near, so no gradient. Without
    // this guard the seeded rated_torque / rated_speed chips painted a
    // full-height percentile band down both columns on every motor load
    // (UI_CLEANUP N7).
    if (filter.value === undefined || filter.value === null) return '';

    const numericProductValue = cellNumeric(productValue, attribute, filter.attribute);
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

  // Per-sort-attribute sorted numeric arrays over the visible (post-filter,
  // post-linear-transform) set. Used to map each cell's value to its
  // empirical CDF position, so the sort highlight gradient is robust to
  // outliers — same idea as the slider's percentile mapping in FilterChip.
  const sortedAttrValues = useMemo(() => {
    const map = new Map<string, number[]>();
    for (const s of sorts) {
      const path = s.attribute;
      const values: number[] = [];
      for (const p of displayProducts) {
        const n = extractNumeric(p, path);
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
  const getSortGradientColor = (sort: SortConfig | null, attribute: string, productValue: any): string => {
    if (!sort) return '';

    const numericProductValue = cellNumeric(productValue, attribute, sort.attribute);
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

  // Per-column filter/sort state, resolved once per render and shared by
  // the header row and every body row (it depends on the column only,
  // never on the row).
  const columns = visibleColumnAttributes.map(attribute => {
    const key = attribute.key;
    return {
      key,
      attribute,
      unitSystem: unitSystemFor(key),
      filter: filters.find(f => targetsColumn(f.attribute, key)) ?? null,
      // Body cells tint on a sort of the column or a nested child of it…
      sort: sorts.find(s => targetsColumn(s.attribute, key)) ?? null,
      // …while the header arrow and the flat "sorted" cell class only
      // answer to a sort on the column's own key.
      sortIndex: sorts.findIndex(s => s.attribute === key),
      hasFilterValue: filters.some(f => f.value !== undefined && targetsColumn(f.attribute, key)),
    };
  });

  const handleProductClick = (product: Product, event: React.MouseEvent) => {
    setClickPosition({ x: event.clientX, y: event.clientY });
    setSelectedProduct(product);
  };

  const handleCloseModal = () => {
    setSelectedProduct(null);
    setClickPosition(null);
  };

  const handleColumnSort = (attribute: string) => {
    const attributeMetadata = allAttributes.find(attr => attr.key === attribute);

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
    // Per-attribute override first (e.g. backlash starts at '<' — users
    // spec a ceiling); generic '>=' floor otherwise.
    const defaultOperator = attributeMetadata.defaultOperator
      ?? (hasComparison
        ? '>='
        : availableOperators.length > 0
          ? availableOperators[0]
          : '=');
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

  // Hide a column: drop any active sort and filter for it, add it to
  // the user hidden set, and clear any prior restore.
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

  // Restore a column: un-hide it and record the explicit restore.
  const handleAddColumn = (attribute: AttributeMetadata) => {
    setUserHiddenKeys(prev => prev.filter(k => k !== attribute.key));
    // EVERY added column goes into userRestoredKeys — that's the
    // "explicit restores always render, even past the cap" carve-out
    // in computeVisibleColumnAttributes. The old !attribute.nested
    // condition assumed unit-bearing columns are visible by default,
    // but past the cap they aren't: adding a nested spec (e.g.
    // input_shaft_diameter on gearheads, whose default set fills the
    // cap) silently did nothing. Reported 2026-07-25.
    setUserRestoredKeys(prev => (prev.includes(attribute.key) ? prev : [...prev, attribute.key]));
    setShowColumnSelector(false);
  };



  return (
    <div className="page-products-layout">
      <main className="results-main">
        {/* Single top toolbar — type selector on the left; the match
         * summary (the one and only result count), Add Spec and Clear on
         * the right. This is the only fixed chrome above the results
         * grid; the previous `.results-header` row is gone so the table
         * gets the height back. UI_CLEANUP N1 retired the second, mono
         * "1-25 of N" count that used to sit next to the type selector. */}
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
            {productType && productType !== 'datasheet' && (
              <LensSelector active={activeLenses} onChange={setActiveLenses} />
            )}
          </div>
          {/* Applied constraints, one chip each — the at-a-glance record
              now that the per-column controls live in popovers
              (UI_CLEANUP Phase 2). Valueless seeded chips don't show. */}
          {productType && (
            <ActiveFilterChips
              filters={filters}
              attributes={columnAttributes}
              products={gearedSource}
              unitSystemFor={unitSystemFor}
              onRemove={(f) => setFilters(prev => prev.filter(x => x !== f))}
            />
          )}
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
            {/* Restore-hidden-column button — only rendered when
                there's something to restore. Lives here, not at the end
                of the header row, so it doesn't hang past the table. */}
            {/* Hidden while a lens is active: the lens defines the column
                set (types/lens.ts); clear the lens to mix specs by hand. */}
            {productType && activeLenses.length === 0 && hiddenColumnAttributes.length > 0 && (
              <Tooltip content={`Add spec column (${hiddenColumnAttributes.length} available)`}>
                <button
                  type="button"
                  ref={(el) => setAddColumnBtnRef(el)}
                  className="add-column-btn"
                  onClick={(e) => {
                    setColumnSelectorCursor({ x: e.clientX, y: e.clientY });
                    setShowColumnSelector(true);
                  }}
                >
                  + Add Spec
                </button>
              </Tooltip>
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

        {listBody === 'loading' ? (
          /* Initial load, nothing fetched yet — without this branch the
             grid rendered zero rows with no indicator and a slow type
             looked like a hang. */
          <div className="loading-state" role="status">
            <ProgressBar
              label={`Loading ${
                productType === 'all' ? 'products' : `${productType}s`
              }…`}
            />
          </div>
        ) : listBody === 'pick-type' || listBody === 'no-products' ? (
          /* Nothing to show at all. The filters-eliminated-everything
             case is NOT here on purpose — it renders inside the grid
             below so the column headers (the filter controls) survive. */
          <div className="empty-state-minimal">
            <p>
              {listBody === 'pick-type'
                ? 'Select a product type to begin'
                : 'No products in database'}
            </p>
            {listBody === 'no-products' && (
              <div className="empty-state-feedback">
                <p className="empty-state-feedback-hint">
                  Looking for a manufacturer or part we don't carry?
                </p>
                <button
                  type="button"
                  className="feedback-trigger"
                  onClick={() => {
                    setFeedbackCategory('missing_product');
                    setFeedbackOpen(true);
                  }}
                >
                  Tell us what to add
                </button>
              </div>
            )}
          </div>
        ) : (
          <>
            {loadProgress &&
              (loadProgress.total === null ||
                loadProgress.loaded < loadProgress.total) && (
                /* First page is on screen; the rest of the listing is
                   still streaming in. Filters apply to what's loaded. */
                <div className="stream-progress" role="status">
                  <ProgressBar
                    value={
                      loadProgress.total !== null ? loadProgress.loaded : undefined
                    }
                    max={loadProgress.total ?? undefined}
                    label={
                      loadProgress.total !== null
                        ? `Loading ${loadProgress.loaded.toLocaleString()} of ${loadProgress.total.toLocaleString()} records…`
                        : `Loading… ${loadProgress.loaded.toLocaleString()} records so far`
                    }
                  />
                </div>
              )}
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
              <div
                className="product-grid-header-part clickable"
                style={{ width: columnWidths['part_number'] ?? defaultPartWidth }}
                onClick={() => handleColumnSort('part_number')}
              >
                <Tooltip content="Click anywhere to sort • click again to reverse, again to clear">
                  <span className="product-grid-header-part-label">Part Number</span>
                </Tooltip>
                <span className="sort-indicator">
                  {sorts.find(s => s.attribute === 'part_number')?.direction === 'asc' && '↑'}
                  {sorts.find(s => s.attribute === 'part_number')?.direction === 'desc' && '↓'}
                  {sorts.some(s => s.attribute === 'part_number') && sorts.length > 1 &&
                    <span className="sort-order">{sorts.findIndex(s => s.attribute === 'part_number') + 1}</span>
                  }
                </span>
                <div className="col-resize-handle" onMouseDown={(e) => startResize('part_number', e)} />
              </div>
              {/* Gear ratio (computed). Shown on the motor view while an
                  output-torque filter is active; '—' marks direct drive
                  (ratio 1). Per-row value comes from gearMap; only the
                  Output Torque / Output Speed columns are post-gear —
                  Rated Torque and Rated Speed stay the motor's own. */}
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
              {columns.map(({ key, attribute, filter, sortIndex, unitSystem: columnUnitSystem }) => {
                  return (
                    <ColumnHeader
                      key={key}
                      attribute={attribute}
                      label={attribute.displayName}
                      products={displayProducts}
                      allProducts={gearedSource}
                      filter={filter}
                      allFilters={filters}
                      sortConfig={sortIndex !== -1 ? sorts[sortIndex] : null}
                      sortIndex={sortIndex}
                      totalSorts={sorts.length}
                      cascadeKey={cascadeKeys.includes(key)}
                      width={columnWidths[key] ?? defaultColWidth}
                      unitSystem={columnUnitSystem}
                      onUnitToggle={() => toggleColumnUnit(key)}
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
                      onSort={() => handleColumnSort(key)}
                      onRemove={() => handleRemoveColumn(key)}
                      onResizeStart={(e) => startResize(key, e)}
                    />
                  );
              })}
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
                  {columns.map(({ key: attrKey, unitSystem: cellSys, filter, sort, sortIndex, hasFilterValue }) => {
                    const productValue = (product as any)[attrKey];
                    const numericValue = extractNumericOnly(productValue, cellSys);
                    const proximityColor = getProximityColor(filter, attrKey, productValue);
                    const hasProximityColor = !!proximityColor;
                    const sortColor = !hasProximityColor
                      ? getSortGradientColor(sort, attrKey, productValue)
                      : '';
                    const cellColor = proximityColor || sortColor || undefined;

                    return (
                      <div
                        key={`default-value-${attrKey}`}
                        className={`spec-header-item ${
                          !hasProximityColor && hasFilterValue ? 'spec-header-item-filtered' :
                          !hasProximityColor && sortIndex !== -1 ? 'spec-header-item-sorted' : ''
                        }`}
                        style={{
                          backgroundColor: cellColor
                        }}
                      >
                        <div className="spec-header-value">
                          {attrKey === 'manufacturer' && typeof productValue === 'string' && productValue ? (
                            /* Manufacturer opens the vendor-facts drawer;
                               stopPropagation keeps the row's detail
                               modal from opening underneath. */
                            <Tooltip content={`Vendor facts for ${productValue}`}>
                              <button
                                type="button"
                                className="vendor-link"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setVendorDrawerFor(productValue);
                                }}
                              >
                                {productValue}
                              </button>
                            </Tooltip>
                          ) : (
                            numericValue || formatValue(productValue, 0, 5, cellSys)
                          )}
                        </div>
                      </div>
                    );
                  })}

                </div>
              ))}
            </div>

            {listBody === 'no-match' && (
              /* Filters eliminated every row. Headers above stay mounted
                 so the sliders/chips can be loosened in place. */
              <div className="product-grid-no-match" role="status">
                <p>No results match your specs</p>
                <div className="empty-state-feedback">
                  <p className="empty-state-feedback-hint">
                    Specs too tight, or expecting a product to show up?
                  </p>
                  {filters.length > 0 && (
                    <button
                      type="button"
                      className="feedback-trigger"
                      onClick={() => {
                        setFilters([]);
                        setSorts([]);
                      }}
                    >
                      Clear filters
                    </button>
                  )}
                  <button
                    type="button"
                    className="feedback-trigger"
                    onClick={() => {
                      setFeedbackCategory('no_match');
                      setFeedbackOpen(true);
                    }}
                  >
                    Tell us what you need
                  </button>
                </div>
              </div>
            )}

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

      {/* Vendor-facts drawer — opened by clicking a manufacturer name. */}
      <VendorDrawer
        manufacturer={vendorDrawerFor}
        onClose={() => setVendorDrawerFor(null)}
      />

      {/* Attribute Selector Modal — shows currently-hidden columns so
          the user can restore them. Passes hiddenColumnAttributes (not
          the full list) so only hideable items appear. */}
      <AttributeSelector
        attributes={hiddenColumnAttributes}
        onSelect={handleAddColumn}
        onClose={() => {
          setShowColumnSelector(false);
          setColumnSelectorCursor(null);
        }}
        isOpen={showColumnSelector}
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
