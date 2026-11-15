"""SYNTHETIC software fixtures only: never actual environmental evidence."""
from datetime import datetime, timezone
import hashlib

import pytest

from sentinel_zero.corroboration.rainfall import (assess_rainfall_hypothesis,
                                                 import_rainfall_bytes)


BEFORE = "2026-10-06T00:00:00Z"
AFTER = "2026-10-06T03:00:00Z"
CONTENT = b"validdate;precip_1h:mm\n2026-10-06T01:00:00Z;1\n2026-10-06T02:00:00Z;2\n2026-10-06T03:00:00Z;0\n"


@pytest.fixture
def metadata():
    return dict(provider="Meteomatics", source_url="https://www.meteomatics.com/en/api/",
                retrieved_at_utc="2026-11-15T00:00:00Z", latitude=6.925, longitude=79.975,
                units="mm", quantity="interval_precipitation", interval_hours=1,
                time_column="validdate", value_column="precip_1h:mm", delimiter=";",
                timestamp_position="interval_end", timezone_offset_minutes=0,
                variable="precip_1h:mm", record_basis="historical_export")


def read(metadata, content=CONTENT, before=BEFORE, after=AFTER):
    return import_rainfall_bytes(content, metadata, before, after, filename="SYNTHETIC_TEST_ONLY.csv")


def test_exact_original_hash_periods_and_no_causation(metadata):
    r = read(metadata)
    assert r["status"] == "CONTEXT_AVAILABLE"
    assert r["metadata"]["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert r["metadata"]["byte_length"] == len(CONTENT)
    assert r["accumulation_periods"] == [dict(start_utc="2026-10-06T00:00:00+00:00", end_utc="2026-10-06T03:00:00+00:00", total_mm=3, sample_count=3)]
    assert r["provider_authenticity"] == "OWNER_DECLARED_NOT_INDEPENDENTLY_AUTHENTICATED"
    assert r["missing_values"] == r["interval_gaps"] == 0
    assert "no causal" in r["interpretation"]


def test_oversized_csv_field_is_a_controlled_validation_error(metadata):
    raw=b'validdate;precip_1h:mm\n2026-10-06T01:00:00Z;'+b'1'*150_000+b'\n'
    with pytest.raises(ValueError,match='CSV field'):
        read(metadata,raw)


@pytest.mark.parametrize("field,value", [("provider", "Unknown"), ("record_basis", "forecast"),
    ("units", "mm/hr"), ("quantity", "precipitation_rate"), ("interval_hours", 0),
    ("interval_hours", True), ("interval_hours", float("nan")), ("latitude", 91),
    ("latitude", float("inf")), ("longitude", -181), ("longitude", True),
    ("timestamp_position", "unknown"), ("delimiter", "|"), ("timezone_offset_minutes", 841),
    ("timezone_offset_minutes", 330.5), ("variable", ""), ("variable", "precip:mm"),
    ("variable", "precip_3h:mm"), ("time_column", "missing"),
    ("value_column", "validdate"), ("source_url", "https://user:password@api.meteomatics.com/"),
    ("source_url", "https://api.meteomatics.com/?token=secret"),
    ("source_url", "https://evil.example/"), ("retrieved_at_utc", "2026-11-15")])
def test_bad_declared_metadata_rejected(metadata, field, value):
    metadata[field] = value
    with pytest.raises(ValueError):
        read(metadata)


def test_explicit_non_utc_offsets_normalize_without_guessing(metadata):
    metadata["timezone_offset_minutes"] = 330
    raw = CONTENT.replace(b"01:00:00Z", b"06:30:00").replace(b"02:00:00Z", b"07:30:00").replace(b"03:00:00Z", b"08:30:00")
    r = read(metadata, raw)
    assert r["rows"][0]["utc"] == "2026-10-06T01:00:00+00:00"
    assert r["accumulation_periods"][0]["total_mm"] == 3


def test_explicit_timestamp_offset_takes_precedence(metadata):
    metadata["timezone_offset_minutes"] = 0
    raw = CONTENT.replace(b"01:00:00Z", b"06:30:00+05:30").replace(b"02:00:00Z", b"07:30:00+05:30").replace(b"03:00:00Z", b"08:30:00+05:30")
    assert read(metadata, raw)["accumulation_periods"][0]["total_mm"] == 3


@pytest.mark.parametrize("raw", [b"validdate;precip_1h:mm\n06/10/2026 01:00;2\n",
    CONTENT.replace(b"02:00:00Z", b"01:00:00Z"), CONTENT.replace(b"02:00:00Z", b"00:30:00Z"),
    CONTENT.replace(b";2", b";-2"), CONTENT.replace(b";2", b";inf"),
    CONTENT.replace(b";2", b";garbage"), b"validdate;precip_1h:mm\n2026-10-06T01:00:00Z\n",
    b"validdate;validdate;precip_1h:mm\n2026-10-06T01:00:00Z;x;1\n",
    b"\xff\xff", b"binary\x00content", b"", b"a" * 5_000_001],
    ids=['ambiguous-date','duplicate-time','overlapping-time','negative','infinite','nonnumeric',
         'malformed-row','duplicate-columns','invalid-utf8','binary','empty','oversized'])
def test_malformed_values_time_order_binary_and_size_rejected(metadata, raw):
    with pytest.raises(ValueError):
        read(metadata, raw)


def test_missing_intervals_are_not_dry_and_periods_do_not_bridge_gaps(metadata):
    r = read(metadata, CONTENT.replace(b";2", b";NaN"))
    assert r["missing_values"] == r["interval_gaps"] == 1
    assert len(r["accumulation_periods"]) == 2
    assert sum(p["total_mm"] for p in r["accumulation_periods"]) == 1


def test_boundary_intervals_not_prorated(metadata):
    r = read(metadata, before="2026-10-06T00:14:46Z", after="2026-10-06T02:14:46Z")
    assert r["rows_in_acquisition_interval"] == 1
    assert r["accumulation_periods"][0]["total_mm"] == 2
    assert r["accumulation_periods"][0]["start_utc"] == "2026-10-06T01:00:00+00:00"


def test_interval_start_convention(metadata):
    metadata["timestamp_position"] = "interval_start"
    r = read(metadata)
    assert r["rows_in_acquisition_interval"] == 2
    assert r["accumulation_periods"][0]["total_mm"] == 3


@pytest.mark.parametrize("name", ["../evil.csv", "C:\\private\\secret.csv", "file.txt", "bad\nname.csv"])
def test_filename_never_becomes_arbitrary_path(metadata, name):
    with pytest.raises(ValueError):
        import_rainfall_bytes(CONTENT, metadata, BEFORE, AFTER, filename=name)


def test_outside_location_and_nonoverlap_are_not_corroboration(metadata):
    metadata["longitude"] = -60
    r = read(metadata)
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert not r["point_inside_exploratory_rectangle"]
    metadata["longitude"] = 79.975
    r = read(metadata, before="2026-10-07T00:00:00Z", after="2026-10-07T03:00:00Z")
    assert r["status"] == "INSUFFICIENT_EVIDENCE"
    assert r["rows_in_acquisition_interval"] == 0


def test_hypothesis_states_are_explicit_reversible_and_do_not_claim_cause(metadata):
    r = read(metadata)
    hypothesis = "PRECIPITATION_RECORDED_IN_COMPLETE_INTERVALS"
    judged = assess_rainfall_hypothesis(r, hypothesis)
    assert judged["status"] == "CONTEXT_POTENTIALLY_CONSISTENT"
    assert "not flooding" in judged["hypothesis_scope"]
    reset = assess_rainfall_hypothesis(judged)
    assert reset["status"] == "CONTEXT_AVAILABLE" and "hypothesis" not in reset
    with pytest.raises(ValueError):
        assess_rainfall_hypothesis(r, "FLOOD_CONFIRMED")
    dry = read(metadata, CONTENT.replace(b";1", b";0").replace(b";2", b";0"))
    assert assess_rainfall_hypothesis(dry, hypothesis)["status"] == "CONTEXT_POTENTIALLY_CONFLICTING"
    missing_dry = read(metadata, CONTENT.replace(b";1", b";0").replace(b";2", b";NaN"))
    assert assess_rainfall_hypothesis(missing_dry, hypothesis)["status"] == "INSUFFICIENT_EVIDENCE"


def test_no_data_missing_metadata_and_reversed_nisar_dates_fail(metadata):
    with pytest.raises(ValueError):
        read(metadata, CONTENT.replace(b";1", b";NaN").replace(b";2", b";NaN").replace(b";0", b";NaN"))
    with pytest.raises(ValueError):
        read(metadata, before=AFTER, after=BEFORE)
    metadata.pop("units")
    with pytest.raises(ValueError):
        read(metadata)
