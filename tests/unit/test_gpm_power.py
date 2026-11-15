"""Explicit SYNTHETIC NASA-shaped JSON for software tests, not precipitation."""
import copy
import hashlib
import json

import pytest

from sentinel_zero.corroboration.gpm_power import parse_power_imerg_daily


@pytest.fixture
def payload():
    return dict(type="Feature", geometry=dict(type="Point", coordinates=[79.975, 6.925, 10]),
                header=dict(time_standard="UTC", sources=["IMERG"], fill_value=-999,
                            start="20261005", end="20261008", api=dict(version="SYNTHETIC_API_ONLY")),
                properties=dict(parameter=dict(IMERG_PRECTOT={"20261005": 1, "20261006": 2,
                                                            "20261007": 3, "20261008": 4})),
                parameters=dict(IMERG_PRECTOT=dict(units="mm", longname="SYNTHETIC software fixture")))


def read(payload):
    return parse_power_imerg_daily(json.dumps(payload).encode(), latitude=6.925, longitude=79.975,
                                  start_date="2026-10-05", end_date="2026-10-08",
                                  retrieved_at_utc="2026-11-15T00:00:00Z",
                                  before_utc="2026-10-06T00:14:46Z", after_utc="2026-10-08T00:14:46Z")


def test_recorded_bytes_source_timing_and_unknown_product_version(payload):
    r = read(payload)
    assert r["status"] == "CONTEXT_AVAILABLE"
    assert r["provider"] == "NASA POWER / GPM IMERG"
    assert r["metadata"]["sha256"] == hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    assert r["metadata"]["processing_version"] == "UNVERIFIED"
    assert r["metadata"]["provider_api"]["version"] == "SYNTHETIC_API_ONLY"
    assert r["metadata"]["source_grid_verified_from_response"] is False
    assert r["rows_in_acquisition_interval"] == 1
    assert r["accumulation_periods"] == [dict(start_utc="2026-10-07T00:00:00+00:00",
                                             end_utc="2026-10-08T00:00:00+00:00", total_mm=3, sample_count=1)]
    assert "no causal" in r["interpretation"]


@pytest.mark.parametrize("header", [{}, dict(time_standard="LST", sources=["IMERG"], fill_value=-999),
    dict(time_standard="UTC", sources=["MERRA2"], fill_value=-999),
    dict(time_standard="UTC", sources=["IMERG"], fill_value=0),
    dict(time_standard="UTC", sources=["IMERG"], fill_value=-999, start="20261001")])
def test_missing_or_inconsistent_header_blocks(payload, header):
    payload["header"] = header
    with pytest.raises(ValueError):
        read(payload)


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "2"])
def test_invalid_nonfill_precipitation_blocks(payload, value):
    payload["properties"]["parameter"]["IMERG_PRECTOT"]["20261007"] = value
    with pytest.raises(ValueError):
        read(payload)


def test_declared_missing_values_and_missing_dates_stay_missing(payload):
    payload["properties"]["parameter"]["IMERG_PRECTOT"]["20261006"] = -999
    del payload["properties"]["parameter"]["IMERG_PRECTOT"]["20261007"]
    r = read(payload)
    assert r["missing_values"] == 2
    assert r["missing_dates"] == ["20261006", "20261007"]
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["accumulation_periods"] == []


@pytest.mark.parametrize("field,value", [("type", "Error"), ("geometry", None), ("parameters", None),
    ("properties", None), ("properties", {"parameter": []}), ("parameters", {"PRECTOTCORR": {"units": "mm"}})])
def test_malformed_response_or_wrong_parameter_blocks(payload, field, value):
    payload[field] = value
    with pytest.raises(ValueError):
        read(payload)


def test_wrong_point_units_dates_and_oversized_bytes_block(payload):
    for change in ("point", "units", "dates"):
        p = copy.deepcopy(payload)
        if change == "point": p["geometry"]["coordinates"][0] = -60
        if change == "units": p["parameters"]["IMERG_PRECTOT"]["units"] = "m/hour"
        if change == "dates": p["properties"]["parameter"]["IMERG_PRECTOT"]["20261001"] = 1
        with pytest.raises(ValueError): read(p)
    with pytest.raises(ValueError):
        parse_power_imerg_daily(b"x" * 262145, latitude=6.925, longitude=79.975,
                               start_date="2026-10-05", end_date="2026-10-08", retrieved_at_utc="2026-11-15T00:00:00Z",
                               before_utc="2026-10-06T00:00:00Z", after_utc="2026-10-07T00:00:00Z")


def test_all_fill_is_not_no_rainfall(payload):
    for key in payload["properties"]["parameter"]["IMERG_PRECTOT"]:
        payload["properties"]["parameter"]["IMERG_PRECTOT"][key] = -999
    with pytest.raises(ValueError, match="no valid historical"):
        read(payload)


def test_daily_rate_numerically_integrated_for_full_day_only(payload):
    payload["parameters"]["IMERG_PRECTOT"]["units"] = "mm/day"
    assert read(payload)["accumulation_periods"][0]["total_mm"] == 3
