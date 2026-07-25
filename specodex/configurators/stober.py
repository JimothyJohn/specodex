"""Stober configurator adapter (configurator.stober.com).

Stober ships an Angular SPA over a same-origin REST API at
``/api/{locale}/…`` (``API_BASE_URL = origin + "/api/"``; the *shop*, e.g.
``SDI``, is a path segment). This adapter knows that endpoint map, walks the
type → group → series tree, and parses the typed requirement schema the API
returns at each node.

Endpoint map (reverse-engineered from the SPA bundle, 2026-07-25)::

    GET  /api/{loc}/ProductSelection/{shop}                       -> product types
    POST /api/{loc}/ProductSelection/{type}/{shop}                -> groups + prefilters
    POST /api/{loc}/ProductSelection/{type}/{group}/{shop}        -> select group (configurationId)
    POST /api/{loc}/ProductSelection/{type}/{group}/Series/{shop} -> series[] + filterGroups

The requirement schema is the list of ``ParameterDoubleValue`` (numeric,
unit + [min, max]) and ``ParameterSingleSelection`` (enumerated) filters the
series response carries. Each maps to a Gearhead field via
:data:`FILTER_TO_GEARHEAD_FIELD`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from specodex.configurators.base import ConfiguratorClient

ORIGIN: str = "https://configurator.stober.com"
API_BASE: str = "/api/"
DEFAULT_LOCALE: str = "en-US"
DEFAULT_SHOP: str = "SDI"

# Product type id for gearboxes (the servo-gearbox groups live under it).
GEARBOX_TYPE: str = "Getriebe"

# Stober filterId -> Gearhead model field. These are the numeric requirement
# sliders the sizing engine ranks by; the mapping is the one bespoke bit an
# adapter owns. Units are carried through from the API response, not assumed.
FILTER_TO_GEARHEAD_FIELD: dict[str, str] = {
    "ig": "gear_ratio",  # gear ratio (unitless)
    "Macc": "max_peak_torque",  # acceleration torque, Nm (transient)
    "Fr": "max_radial_load",  # nominal radial force, N
    "Phi": "backlash",  # backlash, arcmin
    "n1zb": "max_input_speed",  # max perm. input speed, rpm
}


@dataclass(frozen=True)
class RequirementParam:
    """One configurable requirement the sizing engine accepts."""

    filter_id: str
    name: str
    kind: str  # "range" (numeric) | "select" (enumerated)
    unit: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    options: tuple[str, ...] = ()
    gearhead_field: Optional[str] = None


@dataclass(frozen=True)
class ProductType:
    type_id: str
    name: str


@dataclass(frozen=True)
class ProductGroup:
    group_id: str
    name: str


@dataclass(frozen=True)
class Series:
    series_id: str
    name: str
    designation: str = ""


@dataclass
class GroupSelection:
    """Result of selecting a product group — the requirement schema lives here."""

    configuration_id: Optional[str]
    strip: Optional[str]
    requirements: list[RequirementParam] = field(default_factory=list)


class StoberAdapter:
    """Walk the Stober configurator and parse its typed requirement schema."""

    def __init__(self, locale: str = DEFAULT_LOCALE, shop: str = DEFAULT_SHOP) -> None:
        self.locale = locale
        self.shop = shop

    # -- session -----------------------------------------------------------

    def client(self, **kwargs: Any) -> ConfiguratorClient:
        """Build a :class:`ConfiguratorClient` pointed at Stober's origin."""
        return ConfiguratorClient(ORIGIN, api_base=API_BASE, **kwargs)

    def bootstrap_url(self) -> str:
        return f"{ORIGIN}/{self.locale}/category-selection?shop={self.shop}"

    def _ps(self, *segments: str) -> str:
        """Build a ProductSelection API path from path segments."""
        parts = [self.locale, "ProductSelection", *segments]
        return "/".join(p for p in parts if p)

    # -- walk (each takes an opened client) --------------------------------

    def product_types(self, client: ConfiguratorClient) -> list[ProductType]:
        resp = client.api("GET", self._ps(self.shop))
        return [
            ProductType(type_id=t["productTypeId"], name=t.get("productTypeName", ""))
            for t in _as_list(resp.json)
        ]

    def groups(self, client: ConfiguratorClient, type_id: str) -> list[ProductGroup]:
        resp = client.api("POST", self._ps(type_id, self.shop), body=None)
        body = resp.json if isinstance(resp.json, dict) else {}
        return [
            ProductGroup(
                group_id=g.get("categoryId", ""),
                name=g.get("categoryName", ""),
            )
            for g in body.get("productGroups", [])
        ]

    def select_group(
        self, client: ConfiguratorClient, type_id: str, group_id: str
    ) -> GroupSelection:
        """Select a group; returns its configurationId + requirement schema."""
        resp = client.api("POST", self._ps(type_id, group_id, self.shop), body=None)
        return parse_group_selection(resp.json)

    def series(
        self, client: ConfiguratorClient, type_id: str, group_id: str
    ) -> list[Series]:
        resp = client.api(
            "POST",
            self._ps(type_id, group_id, "Series", self.shop),
            body={"filterValues": []},
        )
        body = resp.json if isinstance(resp.json, dict) else {}
        return [
            Series(
                series_id=s.get("seriesId", ""),
                name=s.get("seriesName", ""),
                designation=s.get("additionalDesignation", ""),
            )
            for s in body.get("productSeries", [])
        ]

    def series_requirements(
        self, client: ConfiguratorClient, type_id: str, group_id: str
    ) -> list[RequirementParam]:
        """The typed requirement schema exposed on the series response."""
        resp = client.api(
            "POST",
            self._ps(type_id, group_id, "Series", self.shop),
            body={"filterValues": []},
        )
        body = resp.json if isinstance(resp.json, dict) else {}
        return parse_requirements(body.get("filterGroups", []))


# ---------------------------------------------------------------------------
# Pure parsers — the testable core (no browser, no network)
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _param_from_filter(f: dict[str, Any]) -> Optional[RequirementParam]:
    """Turn one API filter object into a RequirementParam, or None to skip."""
    filter_id = f.get("filterId")
    if not filter_id:
        return None
    ftype = f.get("$type", "")
    name = f.get("filterName") or _info_title(f) or filter_id
    unit = (f.get("units") or None) or None
    mapped = FILTER_TO_GEARHEAD_FIELD.get(filter_id)

    if ftype == "ParameterDoubleValue":
        return RequirementParam(
            filter_id=filter_id,
            name=name,
            kind="range",
            unit=unit,
            minimum=_as_float(f.get("minimumValue")),
            maximum=_as_float(f.get("maximumValue")),
            gearhead_field=mapped,
        )
    if ftype == "ParameterSingleSelection":
        options = tuple(
            v["key"]
            for v in f.get("values", [])
            if isinstance(v, dict) and v.get("key") not in (None, "NoSelection")
        )
        # A selection with no real options carries no information — skip it.
        if not options:
            return None
        return RequirementParam(
            filter_id=filter_id,
            name=name,
            kind="select",
            unit=unit,
            options=options,
            gearhead_field=mapped,
        )
    return None


def parse_requirements(filter_groups: Any) -> list[RequirementParam]:
    """Flatten the series response's ``filterGroups`` into requirement params."""
    params: list[RequirementParam] = []
    seen: set[str] = set()
    for group in _as_list(filter_groups):
        for f in group.get("parameters") or []:
            if not isinstance(f, dict):
                continue
            param = _param_from_filter(f)
            if param is not None and param.filter_id not in seen:
                seen.add(param.filter_id)
                params.append(param)
    return params


def parse_group_selection(payload: Any) -> GroupSelection:
    """Parse a group-select response (configurationId, strip, prefilters)."""
    body = payload if isinstance(payload, dict) else {}
    requirements: list[RequirementParam] = []
    seen: set[str] = set()
    for group in _as_list(body.get("productGroupFilters")):
        for f in group.get("filters") or []:
            if not isinstance(f, dict):
                continue
            param = _param_from_filter(f)
            if param is not None and param.filter_id not in seen:
                seen.add(param.filter_id)
                requirements.append(param)
    return GroupSelection(
        configuration_id=body.get("configurationId"),
        strip=body.get("productSelectionStrip"),
        requirements=requirements,
    )


def _info_title(f: dict[str, Any]) -> Optional[str]:
    info = f.get("infoFlyoutContents")
    return info.get("titleText") if isinstance(info, dict) else None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
