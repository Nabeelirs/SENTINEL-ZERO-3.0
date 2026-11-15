"""Bounded recorded NASA POWER IMERG daily JSON; no network or forecasts.

Only IMERG_PRECTOT is accepted. POWER's differently sourced PRECTOTCORR is
explicitly outside this adapter. Source product run/version is unknown unless
the actual provider payload supplies it; documentation does not invent it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from math import isfinite

from .rainfall import _number, _utc_timestamp


MAX_RESPONSE_BYTES = 262_144
SOURCE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMETER = "IMERG_PRECTOT"


def parse_power_imerg_daily(content: bytes, *, latitude: float, longitude: float,
                            start_date: str, end_date: str, retrieved_at_utc: str,
                            before_utc: str, after_utc: str,
                            study_bounds: tuple[float, float, float, float] = (79.95, 6.90, 80.00, 6.95)) -> dict:
    """Parse actual downloaded bytes and explicit public request provenance.

    Inputs start/end are ISO dates; POWER properties use YYYYMMDD keys. Dates
    absent from the response and declared fill values stay missing. Whole UTC
    days are the only accumulation periods: no hourly interpolation/prorating.
    """
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_RESPONSE_BYTES:
        raise ValueError("NASA POWER response must be 1 to 262,144 bytes")
    lat = _number(latitude, -90, 90, "request latitude")
    lon = _number(longitude, -180, 180, "request longitude")
    first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if first > last or (last - first).days > 366:
        raise ValueError("Invalid or oversized NASA daily request interval")
    before, after = _utc_timestamp(before_utc), _utc_timestamp(after_utc)
    retrieved = _utc_timestamp(retrieved_at_utc)
    if before >= after:
        raise ValueError("NISAR acquisitions must be ordered")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("NASA POWER response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("type") != "Feature":
        raise ValueError("NASA POWER service error or missing Feature response")
    header = payload.get("header", {})
    if not isinstance(header, dict) or header.get("time_standard") != "UTC":
        raise ValueError("NASA POWER response does not establish daily UTC time basis")
    sources = header.get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(s, str) for s in sources) or not any("IMERG" in s.upper() for s in sources):
        raise ValueError("NASA POWER response does not identify IMERG as its source")
    fill = header.get("fill_value")
    if isinstance(fill, bool) or not isinstance(fill, (int, float)) or not isfinite(fill) or fill >= 0:
        raise ValueError("NASA POWER fill-value convention missing or unsupported")
    for name, wanted in (("start", first.strftime("%Y%m%d")), ("end", last.strftime("%Y%m%d"))):
        if name in header and str(header[name]) != wanted:
            raise ValueError("NASA POWER returned date metadata disagrees with the request")
    geometry = payload.get("geometry", {})
    coords = geometry.get("coordinates") if isinstance(geometry, dict) and geometry.get("type") == "Point" else None
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError("NASA POWER response point identity missing")
    x = _number(coords[0], -180, 180, "response longitude")
    y = _number(coords[1], -90, 90, "response latitude")
    if abs(x - lon) > 1e-5 or abs(y - lat) > 1e-5:
        raise ValueError("NASA POWER response point differs from the recorded request")
    parameters = payload.get("parameters")
    parameter_metadata = parameters.get(PARAMETER) if isinstance(parameters, dict) else None
    if not isinstance(parameter_metadata, dict) or parameter_metadata.get("units") not in ("mm", "mm/day"):
        raise ValueError("NASA POWER IMERG daily precipitation units are missing/unsupported")
    properties = payload.get("properties")
    measurements = properties.get("parameter") if isinstance(properties, dict) else None
    values = measurements.get(PARAMETER) if isinstance(measurements, dict) else None
    if not isinstance(values, dict) or len(values) > 367:
        raise ValueError("NASA POWER IMERG daily measurements missing or oversized")
    expected = { (first + timedelta(days=i)).strftime("%Y%m%d") for i in range((last - first).days + 1) }
    if not set(values) <= expected:
        raise ValueError("NASA POWER returned unexpected dates")
    rows, missing_dates = [], []
    for key in sorted(expected):
        value = values.get(key)
        if value is None or value == fill:
            missing_dates.append(key)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError("NASA POWER precipitation includes invalid non-fill values")
        instant = datetime.strptime(key, "%Y%m%d").replace(tzinfo=timezone.utc)
        rows.append(dict(utc=instant.isoformat(), precipitation_mm=float(value),
                         interval_start_utc=instant.isoformat(), interval_end_utc=(instant + timedelta(days=1)).isoformat()))
    if not rows:
        raise ValueError("NASA POWER response has no valid historical IMERG precipitation")
    within = [r for r in rows if _utc_timestamp(r["interval_start_utc"]) >= before and _utc_timestamp(r["interval_end_utc"]) <= after]
    periods = []
    for row in within:
        if not periods or periods[-1]["end_utc"] != row["interval_start_utc"]:
            periods.append(dict(start_utc=row["interval_start_utc"], end_utc=row["interval_end_utc"], total_mm=row["precipitation_mm"], sample_count=1))
        else:
            periods[-1]["end_utc"] = row["interval_end_utc"]
            periods[-1]["total_mm"] += row["precipitation_mm"]
            periods[-1]["sample_count"] += 1
    west, south, east, north = study_bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Unsupported study bounds for NASA rainfall context")
    inside = west <= lon <= east and south <= lat <= north
    gaps = sum(_utc_timestamp(a["interval_end_utc"]) != _utc_timestamp(b["interval_start_utc"]) for a, b in zip(rows, rows[1:]))
    status = "CONTEXT_AVAILABLE" if inside and within else "INSUFFICIENT_EVIDENCE"
    limitations = [
        "NASA POWER-served GPM IMERG is a satellite precipitation estimate, not a local rain-gauge observation.",
        "Documented 0.1 degree IMERG source grid differs from the 5.53 km radar window and 10 m radar posting.",
        "Daily UTC accumulations cannot resolve exact hourly timing around NISAR acquisitions; boundary days are not prorated.",
        "Recent dates may use Late Run and older dates Final Run; per-day run/version is unverified unless identified in the response.",
        "No rainfall record establishes flooding or the physical cause of radar backscatter change.",
    ]
    return dict(schema_version="1.1", status=status, base_status=status, provider="NASA POWER / GPM IMERG",
                provider_authenticity="PUBLIC_PROVIDER_RESPONSE_RECORDED;_LOCAL_HASH_IS_NOT_EXTERNAL_AUTHENTICATION",
                metadata=dict(schema_version="1.1", provider="NASA POWER / GPM IMERG", source_url=SOURCE_URL,
                              sha256=hashlib.sha256(content).hexdigest(), byte_length=len(content),
                              retrieved_at_utc=retrieved.isoformat(), latitude=lat, longitude=lon,
                              request_parameters=dict(parameters=PARAMETER, community="AG", latitude=lat, longitude=lon,
                                                      start=first.strftime("%Y%m%d"), end=last.strftime("%Y%m%d"), format="JSON", **{"time-standard": "UTC"}),
                              units="mm", response_units=parameter_metadata["units"], quantity="interval_precipitation",
                              interval_hours=24, timestamp_position="interval_start", variable=PARAMETER,
                              time_basis="UTC", provider_sources=sources, provider_api=header.get("api"),
                              product_run="UNVERIFIED", processing_version="UNVERIFIED",
                              documented_source_grid_degrees=0.1, source_grid_verified_from_response=False),
                rows=rows, missing_values=len(missing_dates), missing_dates=missing_dates, interval_gaps=gaps,
                rows_in_acquisition_interval=len(within), point_inside_exploratory_rectangle=inside,
                covers_acquisition_interval=(_utc_timestamp(rows[0]["interval_start_utc"]) <= before and _utc_timestamp(rows[-1]["interval_end_utc"]) >= after),
                acquisition_before_utc=before.isoformat(), acquisition_after_utc=after.isoformat(), accumulation_periods=periods,
                accumulation_rule="Complete valid UTC days wholly inside [before, after]; no hourly interpolation, boundary prorating or missing-day fill.",
                reason="Recorded NASA IMERG daily context; inspect spatial/temporal sampling limitations." if status == "CONTEXT_AVAILABLE" else "NASA point/time coverage does not support this study window.",
                interpretation="Independent precipitation context only; no causal, flood or SAR validation claim.", limitations=limitations)
