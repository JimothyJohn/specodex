"""Cross-field plausibility checks (``spec_rules.plausibility_flags``) and
the read-only ``admin audit-specs`` scan built on them.

Fixtures are real rows from the live catalog (2026-11-24) — both the
extremes that must PASS (they are genuine products) and the slips that
must be flagged.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cli.audit_specs import audit_items
from specodex.ingest_log import build_record
from specodex.models.gearhead import Gearhead
from specodex.models.motor import Motor
from specodex.spec_rules import (
    POWER_BALANCE_BAND,
    partition_plausible,
    plausibility_flags,
    power_balance_ratio,
)


def _motor(power=None, torque=None, speed=None, **kw) -> Motor:
    data = {
        "product_name": kw.pop("product_name", "M"),
        "manufacturer": kw.pop("manufacturer", "TestMfg"),
        "part_number": kw.pop("part_number", "P-1"),
    }
    if power is not None:
        data["rated_power"] = {"value": power, "unit": "W"}
    if torque is not None:
        data["rated_torque"] = {"value": torque, "unit": "Nm"}
    if speed is not None:
        data["rated_speed"] = {"value": speed, "unit": "rpm"}
    data.update(kw)
    return Motor(**data)


# --- Real extremes that are NOT errors --------------------------------------


def test_subfractional_gearmotor_passes():
    # Bodine 0767: 1/2000 HP input, 0.85 Nm at 1 rpm output (ratio ≈ 0.24).
    m = _motor(0.37285, 0.847386, 1, part_number="0767")
    assert plausibility_flags(m) == []


def test_megawatt_motor_passes():
    # WEG W60-5300HP-900F-2300V: 5300 HP = 3.95 MW, 21.2 kNm at 1790 rpm.
    m = _motor(3952209, 21241.6, 1790, part_number="W60-5300HP-900F-2300V")
    assert plausibility_flags(m) == []
    assert power_balance_ratio(m) == pytest.approx(1.0075, rel=1e-3)


def test_servo_rated_vs_peak_mix_passes():
    # Kollmorgen AKM42C: 490 W, 3.1 Nm, 6000 rpm → ratio ≈ 3.98 (vendor
    # quotes rated torque at stall). Inside the band on purpose.
    assert plausibility_flags(_motor(490, 3.1, 6000)) == []


# --- Unit slips that must be flagged ----------------------------------------


def test_watt_kilowatt_slip_is_flagged():
    # Beckhoff AM 3111: stored 50,000 W for 0.16 Nm @ 3000 rpm (≈ 50 W).
    m = _motor(50000, 0.16, 3000, part_number="AM 3111")
    flags = plausibility_flags(m)
    assert len(flags) == 1
    assert "AM 3111" in flags[0] and "power_balance" in flags[0]


def test_millinewton_metre_slip_is_flagged():
    # 4.8 W motor whose 42 mNm torque was stored as 42 Nm (ratio ≈ 1970).
    assert plausibility_flags(_motor(4.8, 42, 2150))


def test_kw_unit_is_canonicalised_before_the_check():
    # A correctly-labelled kW value is converted by the model and passes.
    m = Motor(
        product_name="M",
        manufacturer="X",
        part_number="P",
        rated_power={"value": 0.75, "unit": "kW"},
        rated_torque={"value": 2.39, "unit": "Nm"},
        rated_speed={"value": 3000, "unit": "rpm"},
    )
    assert m.rated_power.unit == "W"
    assert plausibility_flags(m) == []


# --- Not enough data / not a motor -------------------------------------------


def test_missing_field_is_not_judged():
    assert power_balance_ratio(_motor(50000, 0.16, None)) is None
    assert plausibility_flags(_motor(50000, None, 3000)) == []


def test_non_motor_is_not_judged():
    g = Gearhead(product_name="G", manufacturer="X", part_number="G-1")
    assert power_balance_ratio(g) is None
    assert plausibility_flags(g) == []


def test_partition_holds_only_flagged_rows():
    good = _motor(750, 2.39, 3000, part_number="good")
    bad = _motor(50000, 0.16, 3000, part_number="bad")
    ok, held = partition_plausible([good, bad])
    assert ok == [good]
    assert [m for m, _ in held] == [bad]
    assert held[0][1]


# --- Ingest log ---------------------------------------------------------------


def test_ingest_record_carries_spec_flags_only_when_present():
    base = dict(
        url="https://x/y.pdf", manufacturer="X", product_type="motor", status="success"
    )
    assert "spec_flags" not in build_record(**base)
    assert "spec_flags" not in build_record(**base, spec_flags=[])
    rec = build_record(**base, spec_flags=["[P] power_balance: ..."])
    assert rec["spec_flags"] == ["[P] power_balance: ..."]


# --- audit-specs ----------------------------------------------------------------


ID2 = "00000000-0000-4000-8000-000000000002"
ID3 = "00000000-0000-4000-8000-000000000003"


def test_audit_items_reports_slips_and_noncanonical_units_only():
    items = [
        {  # clean
            "product_id": "00000000-0000-4000-8000-000000000001",
            "manufacturer": "WEG",
            "part_number": "W60",
            "product_name": "W60",
            "rated_power": {"value": 3952209, "unit": "W"},
            "rated_torque": {"value": 21241.6, "unit": "Nm"},
            "rated_speed": {"value": 1790, "unit": "rpm"},
        },
        {  # unit slip
            "product_id": "00000000-0000-4000-8000-000000000002",
            "manufacturer": "Beckhoff",
            "part_number": "AM 3111",
            "product_name": "AM",
            "datasheet_url": "https://x/am.pdf",
            "rated_power": {"value": 50000, "unit": "W"},
            "rated_torque": {"value": 0.16, "unit": "Nm"},
            "rated_speed": {"value": 3000, "unit": "rpm"},
        },
        {  # legacy non-canonical unit (canonicalises fine, so no power flag)
            "product_id": "00000000-0000-4000-8000-000000000003",
            "manufacturer": "Nidec",
            "part_number": "22H",
            "product_name": "22H",
            "rated_power": {"value": 4.8, "unit": "W"},
            "rated_torque": {"value": 22, "unit": "mN-m"},
            "rated_speed": {"value": 2150, "unit": "RPM"},
        },
    ]
    out = {f["product_id"]: f for f in audit_items(items)}
    assert set(out) == {ID2, ID3}
    assert [f["check"] for f in out[ID2]["findings"]] == ["power_balance"]
    assert out[ID2]["datasheet_url"] == "https://x/am.pdf"
    assert sorted(f["check"] for f in out[ID3]["findings"]) == [
        "noncanonical_unit",
        "noncanonical_unit",
    ]


def test_audit_items_reports_unparseable_rows_instead_of_skipping():
    out = list(audit_items([{"product_id": "not-a-uuid", "part_number": "X"}]))
    assert [f["check"] for f in out[0]["findings"]] == ["model_invalid"]


# --- Property: never raises, flag iff ratio leaves the band ---------------------

_pos = st.floats(min_value=1e-6, max_value=1e9, allow_nan=False, allow_infinity=False)


@settings(max_examples=300, deadline=None)
@given(p=_pos, t=_pos, n=_pos)
def test_flag_iff_ratio_outside_band(p, t, n):
    m = _motor(p, t, n)
    flags = plausibility_flags(m)
    assert isinstance(flags, list) and all(isinstance(f, str) for f in flags)
    ratio = power_balance_ratio(m)
    if ratio is None:  # model rejected/rounded a value away
        assert flags == []
        return
    assert math.isfinite(ratio)
    lo, hi = POWER_BALANCE_BAND
    assert bool(flags) == (not (lo <= ratio <= hi))
