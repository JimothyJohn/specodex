"""Deterministic price inference from DB comparables (price-comps-v1).

todo/COMMERCIAL.md Phase 2 — "scan the database and price from similar
results", deterministic and explainable, no black box:

- **Family ladder** (preferred): within the same manufacturer + same
  ``product_family``, industrial pricing is power-law in the dominant
  sizing spec, so a least-squares fit of log10(price) vs log10(spec)
  over >= 3 priced points interpolates the target's price.
- **KNN fallback**: distance-weighted median of the k <= 8 nearest
  priced comparables in log10-spec space over the type's dominant
  sizing specs.

Gates — an absent figure beats a junk figure. No estimate is emitted
when the used comparable pool is smaller than 3, when the used pool's
price dispersion (p75/p25) exceeds 4x, or when the family ladder would
extrapolate beyond 2x outside its observed spec range (that last case
falls back to KNN before giving up).

Determinism is a hard requirement (COMMERCIAL.md ground rule 4): the
pool is sorted by product_id before any arithmetic, distance ties break
on product_id, there is no randomness and no clock read — the caller
injects ``retrieved_at``. Identical inputs produce an identical
``SourcedFigure``, bit for bit.

Units are NOT converted here. The store is canonical per key (the
ValueUnit validators canonicalise on ingest), so comparables whose spec
unit differs from the target's are dropped rather than converted.

Price only — lead-time estimates are deliberately out of scope (Phase 4
vendor-published data; the stocked signal lives in ``availability``).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from specodex.models.commercial import SourceCitation, SourcedFigure
from specodex.models.common import MinMaxUnit, ValueUnit
from specodex.models.product import ProductBase

METHOD_FAMILY = "price-comps-v1:family-ladder"
METHOD_KNN = "price-comps-v1:knn"

# Dominant sizing specs per product_type: (primary, secondary). The
# primary spec drives the family ladder; KNN measures distance over
# whichever of the two the target carries. Field names are verified
# against the concrete models in specodex/models/.
DOMINANT_SPECS: dict[str, Tuple[str, str]] = {
    "motor": ("rated_power", "rated_torque"),
    "drive": ("rated_power", "rated_current"),
    "gearhead": ("max_continuous_torque", "gear_ratio"),
    "electric_cylinder": ("continuous_force", "stroke"),
    "linear_actuator": ("max_push_force", "stroke"),
    "robot_arm": ("payload", "reach"),
    "contactor": ("conventional_thermal_current", "rated_operational_voltage_max"),
}

# Minimum comparables actually used by either method.
MIN_POOL = 3
# Maximum neighbours for the KNN fallback.
KNN_K = 8
# Used-pool price dispersion gate: p75/p25 above this -> no estimate.
MAX_DISPERSION = 4.0
# KNN distance weight = 1 / (d + softening) — keeps a zero-distance
# neighbour from collapsing the median onto itself alone.
DISTANCE_SOFTENING = 0.1
# Family-ladder extrapolation clamp: the target's spec must lie within
# [min_spec / 2, max_spec * 2] of the ladder's observed range.
EXTRAPOLATION_FACTOR = 2.0


def _spec_scalar(
    product: ProductBase, field: str
) -> Optional[Tuple[float, Optional[str]]]:
    """Extract a positive finite scalar (value, unit) for one spec field.

    Handles the three shapes a spec field takes in the store:
    ``ValueUnit`` -> its value; ``MinMaxUnit`` -> the midpoint (or the
    single present bound); plain int/float (e.g. ``gear_ratio``) ->
    itself with ``unit=None``. Anything else — missing field, None,
    bool, non-positive or non-finite value — returns None: rows with
    an unusable spec are excluded, never guessed.
    """
    value = getattr(product, field, None)
    if value is None:
        return None
    if isinstance(value, ValueUnit):
        scalar, unit = value.value, value.unit
    elif isinstance(value, MinMaxUnit):
        if value.min is not None and value.max is not None:
            scalar = (value.min + value.max) / 2.0
        elif value.min is not None:
            scalar = value.min
        elif value.max is not None:
            scalar = value.max
        else:
            return None
        unit = value.unit
    elif isinstance(value, bool):
        return None
    elif isinstance(value, (int, float)):
        scalar, unit = float(value), None
    else:
        return None
    try:
        scalar = float(scalar)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(scalar) or scalar <= 0:
        return None
    return scalar, unit


def _price_usd(product: ProductBase) -> Optional[float]:
    """The product's listed USD price, or None if unusable for comps."""
    msrp = getattr(product, "msrp", None)
    if not isinstance(msrp, ValueUnit):
        return None
    if msrp.unit != "USD":
        return None
    try:
        price = float(msrp.value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (numpy default) on pre-sorted values."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _dispersion_ok(prices: Sequence[float]) -> Tuple[bool, float, float]:
    """(passes_gate, p25, p75) for the used comparables' prices."""
    ordered = sorted(prices)
    p25 = _percentile(ordered, 0.25)
    p75 = _percentile(ordered, 0.75)
    # Prices are > 0 by pool construction, so p25 > 0.
    return (p75 / p25) <= MAX_DISPERSION, p25, p75


def _figure(
    estimate: float,
    used: Sequence[ProductBase],
    p25: float,
    p75: float,
    method: str,
    retrieved_at: str,
) -> SourcedFigure:
    return SourcedFigure(
        value=ValueUnit(value=round(estimate, 2), unit="USD"),
        confidence="inferred",
        observed_range=MinMaxUnit(min=round(p25, 2), max=round(p75, 2), unit="USD"),
        sources=[
            SourceCitation(
                kind="db_inference",
                retrieved_at=retrieved_at,
                comparable_ids=sorted(str(p.product_id) for p in used),
                method_version=method,
            )
        ],
    )


def _family_ladder(
    target: ProductBase,
    pool: List[ProductBase],
    primary: str,
    retrieved_at: str,
) -> Optional[SourcedFigure]:
    """Log-log least-squares interpolation on the same-family price ladder.

    Returns None whenever any precondition or gate fails; the caller
    falls back to KNN.
    """
    manufacturer = (target.manufacturer or "").strip()
    family = (target.product_family or "").strip()
    if not manufacturer or not family:
        return None
    target_spec = _spec_scalar(target, primary)
    if target_spec is None:
        return None
    t_value, t_unit = target_spec

    ladder: List[Tuple[ProductBase, float, float]] = []  # (comp, spec, price)
    for comp in pool:
        if (comp.manufacturer or "").strip() != manufacturer:
            continue
        if (comp.product_family or "").strip() != family:
            continue
        spec = _spec_scalar(comp, primary)
        if spec is None or spec[1] != t_unit:
            # Unit mismatch: the store is canonical per key — drop,
            # never convert.
            continue
        price = _price_usd(comp)
        if price is None:
            continue
        ladder.append((comp, spec[0], price))

    if len(ladder) < MIN_POOL:
        return None

    specs = [s for _, s, _ in ladder]
    lo, hi = min(specs), max(specs)
    if not (lo / EXTRAPOLATION_FACTOR <= t_value <= hi * EXTRAPOLATION_FACTOR):
        # Would extrapolate beyond 2x outside the ladder's range.
        return None

    prices = [p for _, _, p in ladder]
    ok, p25, p75 = _dispersion_ok(prices)
    if not ok:
        return None

    xs = [math.log10(s) for s in specs]
    ys = [math.log10(p) for p in prices]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0.0:
        # Degenerate ladder (all points share one spec value): the best
        # least-squares fit is the flat line through the mean log-price.
        slope = 0.0
    else:
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
    intercept = mean_y - slope * mean_x

    log_estimate = intercept + slope * math.log10(t_value)
    try:
        estimate = 10.0**log_estimate
    except OverflowError:
        return None
    if not math.isfinite(estimate) or estimate <= 0:
        return None

    used = [comp for comp, _, _ in ladder]
    return _figure(estimate, used, p25, p75, METHOD_FAMILY, retrieved_at)


def _knn(
    target: ProductBase,
    pool: List[ProductBase],
    spec_fields: Tuple[str, str],
    retrieved_at: str,
) -> Optional[SourcedFigure]:
    """Distance-weighted median of the k nearest priced comparables.

    Distance is root-mean-square over the log10 gap of the specs both
    the target and the comparable carry (same unit required per spec);
    the per-dimension normalisation keeps distances comparable between
    neighbours that share one spec and neighbours that share two.
    """
    target_specs: dict[str, Tuple[float, Optional[str]]] = {}
    for field in spec_fields:
        spec = _spec_scalar(target, field)
        if spec is not None:
            target_specs[field] = spec
    if not target_specs:
        return None

    scored: List[Tuple[float, str, ProductBase, float]] = []  # (d, pid, comp, price)
    for comp in pool:
        gaps: List[float] = []
        for field, (t_value, t_unit) in target_specs.items():
            spec = _spec_scalar(comp, field)
            if spec is None or spec[1] != t_unit:
                continue
            gaps.append(math.log10(spec[0]) - math.log10(t_value))
        if not gaps:
            continue  # requires >= 1 shared spec
        price = _price_usd(comp)
        if price is None:
            continue
        distance = math.sqrt(sum(g * g for g in gaps) / len(gaps))
        scored.append((distance, str(comp.product_id), comp, price))

    # Nearest first; ties in distance broken by product_id.
    scored.sort(key=lambda item: (item[0], item[1]))
    used = scored[:KNN_K]
    if len(used) < MIN_POOL:
        return None

    prices = [price for _, _, _, price in used]
    ok, p25, p75 = _dispersion_ok(prices)
    if not ok:
        return None

    # Distance-weighted median: walk the price-sorted neighbours until
    # the cumulative weight crosses half the total.
    weighted = sorted(
        (
            (price, 1.0 / (distance + DISTANCE_SOFTENING))
            for distance, _, _, price in used
        ),
        key=lambda pair: pair[0],
    )
    total_weight = sum(weight for _, weight in weighted)
    half = total_weight / 2.0
    cumulative = 0.0
    estimate = weighted[-1][0]
    for price, weight in weighted:
        cumulative += weight
        if cumulative >= half:
            estimate = price
            break

    comps = [comp for _, _, comp, _ in used]
    return _figure(estimate, comps, p25, p75, METHOD_KNN, retrieved_at)


def estimate_price(
    target: ProductBase,
    pool: Sequence[ProductBase],
    *,
    retrieved_at: str,
) -> Optional[SourcedFigure]:
    """Estimate the target's USD price from DB comparables, or None.

    ``pool`` may be any collection of products; rows of a different
    product_type, the target itself, and rows without a usable listed
    USD price are filtered out here. ``retrieved_at`` is injected by
    the caller (an ISO 8601 timestamp) so the algorithm itself never
    reads a clock — a re-run against the same snapshot with the same
    timestamp reproduces the figure exactly.
    """
    spec_fields = DOMINANT_SPECS.get(target.product_type)
    if spec_fields is None:
        return None

    target_id = str(target.product_id)
    priced = [
        comp
        for comp in pool
        if comp.product_type == target.product_type
        and str(comp.product_id) != target_id
        and _price_usd(comp) is not None
    ]
    # Determinism: fix the iteration order before any float arithmetic
    # so input permutations cannot change summation order.
    priced.sort(key=lambda comp: str(comp.product_id))
    if len(priced) < MIN_POOL:
        return None

    figure = _family_ladder(target, priced, spec_fields[0], retrieved_at)
    if figure is not None:
        return figure
    return _knn(target, priced, spec_fields, retrieved_at)
