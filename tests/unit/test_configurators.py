"""Unit tests for the configurator-harvest surface.

The live browser walk can't run in CI (network + WAF + no Chromium session),
so the contract is pinned against **captured Stober API responses**
(``tests/unit/fixtures/configurators/stober_walk.json``, recorded live
2026-07-25). Tests exercise:

* the pure parsers against real payloads (the requirement schema),
* the adapter walk methods against a fake in-memory client,
* :class:`ConfiguratorClient` guardrails (SSRF, cross-origin, lifecycle)
  without launching a real browser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specodex.configurators.base import (
    ApiResponse,
    ConfiguratorClient,
    ConfiguratorError,
)
from specodex.configurators.stober import (
    FILTER_TO_GEARHEAD_FIELD,
    GEARBOX_TYPE,
    RequirementParam,
    StoberAdapter,
    parse_group_selection,
    parse_requirements,
)
from specodex.url_safety import UnsafeURLError

FIXTURE = Path(__file__).parent / "fixtures" / "configurators" / "stober_walk.json"


@pytest.fixture(scope="module")
def walk() -> dict:
    return json.loads(FIXTURE.read_text())


# ---------------------------------------------------------------------------
# Fake client — routes fixture responses by path, no browser/network.
# ---------------------------------------------------------------------------


class FakeClient:
    """Stand-in for ConfiguratorClient that replays captured responses."""

    def __init__(self, walk: dict) -> None:
        self._walk = walk
        self.calls: list[tuple[str, str, object]] = []

    def api(self, method: str, path: str, body=None) -> ApiResponse:
        self.calls.append((method, path, body))
        if path.endswith("Series/SDI"):
            key = "seriesPOST"
        elif path.endswith("SynchronGetriebe/SDI"):
            key = "groupSel"
        elif path.endswith("Getriebe/SDI"):
            key = "groups"
        elif path.endswith("ProductSelection/SDI"):
            key = "types"
        else:  # pragma: no cover - guard for an unmapped path
            raise AssertionError(f"unmapped fixture path: {path}")
        entry = self._walk[key]
        return ApiResponse(status=entry["status"], json=entry["json"])


# ---------------------------------------------------------------------------
# Requirement-schema parsing (the valuable core)
# ---------------------------------------------------------------------------


def test_parse_requirements_maps_numeric_sliders(walk: dict) -> None:
    reqs = parse_requirements(walk["seriesPOST"]["json"]["filterGroups"])
    by_id = {r.filter_id: r for r in reqs}

    # Every mapped numeric requirement is present, typed, and unit-tagged.
    assert by_id["ig"].kind == "range"
    assert by_id["ig"].gearhead_field == "gear_ratio"
    assert (by_id["ig"].minimum, by_id["ig"].maximum) == (1.97, 1762.58)

    assert by_id["Macc"].unit == "Nm"
    assert by_id["Macc"].gearhead_field == "max_peak_torque"
    assert by_id["Macc"].maximum == 43000.0

    assert by_id["Fr"].unit == "N" and by_id["Fr"].gearhead_field == "max_radial_load"
    assert by_id["Phi"].unit == "arcmin" and by_id["Phi"].gearhead_field == "backlash"
    assert by_id["n1zb"].unit == "rpm"
    assert by_id["n1zb"].gearhead_field == "max_input_speed"


def test_parse_requirements_captures_selections(walk: dict) -> None:
    reqs = parse_requirements(walk["seriesPOST"]["json"]["filterGroups"])
    selects = {r.filter_id: r for r in reqs if r.kind == "select"}
    # Enumerated filters are kept, with the "NoSelection" sentinel stripped.
    assert "PACKAGE" in selects
    assert "NoSelection" not in selects["PACKAGE"].options
    assert selects["PACKAGE"].options  # non-empty after stripping sentinel


def test_every_mapped_field_is_a_real_gearhead_field() -> None:
    from specodex.models.gearhead import Gearhead

    fields = set(Gearhead.model_fields)
    for filter_id, field_name in FILTER_TO_GEARHEAD_FIELD.items():
        assert field_name in fields, (
            f"{filter_id} -> unknown Gearhead field {field_name}"
        )


def test_parse_group_selection_extracts_config_id(walk: dict) -> None:
    sel = parse_group_selection(walk["groupSel"]["json"])
    assert sel.configuration_id == "66f3f2e6-b7ca-4e3b-8c3f-ee70a016324d"
    assert sel.strip  # a productSelectionStrip token is present


# ---------------------------------------------------------------------------
# Adversarial inputs — parsers must never raise on malformed payloads.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        "not a dict",
        42,
        [{"parameters": None}],
        [{"parameters": [{"$type": "ParameterDoubleValue"}]}],  # no filterId
        [{"parameters": [{"filterId": "x", "$type": "Unknown"}]}],
        [{"parameters": [None, 7, {"filterId": True}]}],
        [
            {
                "parameters": [
                    {
                        "filterId": "ig",
                        "$type": "ParameterDoubleValue",
                        "minimumValue": "nan",
                        "maximumValue": None,
                    }
                ]
            }
        ],
    ],
)
def test_parse_requirements_never_raises(payload) -> None:
    result = parse_requirements(payload)
    assert isinstance(result, list)


@pytest.mark.parametrize("payload", [None, [], {}, "x", 3, {"configurationId": None}])
def test_parse_group_selection_never_raises(payload) -> None:
    sel = parse_group_selection(payload)
    assert isinstance(sel.requirements, list)


def test_double_value_bad_numbers_drop_to_none() -> None:
    reqs = parse_requirements(
        [
            {
                "parameters": [
                    {
                        "filterId": "ig",
                        "$type": "ParameterDoubleValue",
                        "minimumValue": "not-a-number",
                        "maximumValue": [1, 2],
                    }
                ]
            }
        ]
    )
    assert len(reqs) == 1
    assert reqs[0].minimum is None and reqs[0].maximum is None


def test_duplicate_filter_ids_deduped() -> None:
    reqs = parse_requirements(
        [
            {"parameters": [{"filterId": "ig", "$type": "ParameterDoubleValue"}]},
            {"parameters": [{"filterId": "ig", "$type": "ParameterDoubleValue"}]},
        ]
    )
    assert [r.filter_id for r in reqs] == ["ig"]


# ---------------------------------------------------------------------------
# Regression cases for the four bugs Hypothesis surfaced (2026-09-02).
# The property companion (``test_configurators_property.py``) pins the
# contract; these pin the exact payload shapes so they can't regress even
# if the strategies drift.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [{}, {"a": 1}, [], [1], frozenset({1})])
def test_unhashable_filter_id_is_skipped_not_raised(bad_id) -> None:
    """``FILTER_TO_GEARHEAD_FIELD.get(filter_id)`` raised TypeError."""
    payload = [{"parameters": [{"filterId": bad_id, "$type": "ParameterDoubleValue"}]}]
    assert parse_requirements(payload) == []
    sel = parse_group_selection(
        {"productGroupFilters": [{"filters": payload[0]["parameters"]}]}
    )
    assert sel.requirements == []


@pytest.mark.parametrize("scalar", [True, False, 0, 1, 1.5, "x"])
def test_non_list_parameters_is_skipped_not_raised(scalar) -> None:
    """``group.get("parameters") or []`` raised TypeError on truthy scalars."""
    assert parse_requirements([{"parameters": scalar}]) == []
    assert (
        parse_group_selection(
            {"productGroupFilters": [{"filters": scalar}]}
        ).requirements
        == []
    )


@pytest.mark.parametrize("bad_id", [7, True, 1.5, ""])
def test_non_string_filter_id_is_skipped(bad_id) -> None:
    """``filter_id`` is a dict key and set member — must be a real string."""
    payload = [{"parameters": [{"filterId": bad_id, "$type": "ParameterDoubleValue"}]}]
    assert parse_requirements(payload) == []


def test_non_string_name_falls_back_to_a_string() -> None:
    reqs = parse_requirements(
        [
            {
                "parameters": [
                    {
                        "filterId": "ig",
                        "$type": "ParameterDoubleValue",
                        "filterName": 123,
                        "infoFlyoutContents": {"titleText": ["nope"]},
                        "units": 7,
                    }
                ]
            }
        ]
    )
    assert len(reqs) == 1
    assert reqs[0].name == "ig"
    assert reqs[0].unit is None


@pytest.mark.parametrize(
    "raw", [True, False, "nan", "inf", "-inf", float("nan"), float("inf")]
)
def test_bool_and_non_finite_bounds_drop_to_none(raw) -> None:
    """``float(True) == 1.0``, and NaN/inf bounds break every comparison."""
    reqs = parse_requirements(
        [
            {
                "parameters": [
                    {
                        "filterId": "Macc",
                        "$type": "ParameterDoubleValue",
                        "minimumValue": raw,
                        "maximumValue": raw,
                    }
                ]
            }
        ]
    )
    assert len(reqs) == 1
    assert reqs[0].minimum is None and reqs[0].maximum is None


def test_non_string_option_keys_are_dropped() -> None:
    reqs = parse_requirements(
        [
            {
                "parameters": [
                    {
                        "filterId": "shaft",
                        "$type": "ParameterSingleSelection",
                        "values": [
                            {"key": 7},
                            {"key": ["a"]},
                            {"key": ""},
                            {"key": "NoSelection"},
                            {"key": "SK"},
                        ],
                    }
                ]
            }
        ]
    )
    assert len(reqs) == 1
    assert reqs[0].options == ("SK",)


# ---------------------------------------------------------------------------
# Adapter walk against the fake client
# ---------------------------------------------------------------------------


def test_adapter_product_types(walk: dict) -> None:
    adapter = StoberAdapter()
    types = adapter.product_types(FakeClient(walk))  # type: ignore[arg-type]
    ids = [t.type_id for t in types]
    assert GEARBOX_TYPE in ids
    assert types[0].name == "Gearboxes"


def test_adapter_groups_and_series(walk: dict) -> None:
    adapter = StoberAdapter()
    client = FakeClient(walk)
    groups = adapter.groups(client, GEARBOX_TYPE)  # type: ignore[arg-type]
    assert any(g.group_id == "SynchronGetriebe" for g in groups)

    series = adapter.series(client, GEARBOX_TYPE, "SynchronGetriebe")  # type: ignore[arg-type]
    assert len(series) == 16
    assert any(s.series_id == "P_ME" for s in series)


def test_adapter_builds_expected_paths(walk: dict) -> None:
    adapter = StoberAdapter(locale="en-US", shop="SDI")
    client = FakeClient(walk)
    adapter.series_requirements(client, GEARBOX_TYPE, "SynchronGetriebe")  # type: ignore[arg-type]
    method, path, body = client.calls[-1]
    assert method == "POST"
    assert path == "en-US/ProductSelection/Getriebe/SynchronGetriebe/Series/SDI"
    assert body == {"filterValues": []}


# ---------------------------------------------------------------------------
# ConfiguratorClient guardrails (no real browser launched)
# ---------------------------------------------------------------------------


def test_client_rejects_non_https_origin() -> None:
    with pytest.raises(UnsafeURLError):
        ConfiguratorClient("http://configurator.stober.com")


def test_client_rejects_internal_origin() -> None:
    with pytest.raises(UnsafeURLError):
        ConfiguratorClient("https://169.254.169.254")


def test_api_outside_context_manager_raises() -> None:
    client = ConfiguratorClient("https://configurator.stober.com")
    with pytest.raises(ConfiguratorError):
        client.api("GET", "en-US/ProductSelection/SDI")


def test_open_outside_context_manager_raises() -> None:
    client = ConfiguratorClient("https://configurator.stober.com", respect_robots=False)
    with pytest.raises(ConfiguratorError):
        client.open("https://configurator.stober.com/en-US/category-selection")


def test_api_base_normalised() -> None:
    # A resolvable public host — the constructor SSRF-checks the origin via
    # validate_url, which resolves DNS (matches browser.py / vendor_facts).
    host = "https://configurator.stober.com"
    assert ConfiguratorClient(host).api_base == "/api/"
    assert ConfiguratorClient(host, api_base="config/v2").api_base == "/config/v2/"


def test_requirement_param_is_hashable_frozen() -> None:
    r = RequirementParam(filter_id="ig", name="Gear ratio", kind="range")
    with pytest.raises(AttributeError):
        r.unit = "x"  # type: ignore[misc]
