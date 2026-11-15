"""Operator-supplied recorded rainfall with explicit metadata and hashes, no API."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from math import isfinite
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..science.preparation import sha256_file

MAX_UPLOAD_BYTES = 5_000_000
MAX_UPLOAD_ROWS = 10_000
CONTEXT_STATES = ("CONTEXT_AVAILABLE", "CONTEXT_POTENTIALLY_CONSISTENT",
                  "CONTEXT_POTENTIALLY_CONFLICTING", "INSUFFICIENT_EVIDENCE")


def _bounded_csv_rows(reader):
    try:
        yield from reader
    except csv.Error as exc:
        raise ValueError('Malformed or oversized rainfall CSV field') from exc


def unavailable() -> dict:
    return dict(status="INSUFFICIENT_PHYSICAL_EVIDENCE",provider="Meteomatics",rows=[],
                reason="Original historical CSV export and matching source/location/time/unit metadata were not found in the project/known runtime. Operator paths are required.",
                interpretation="No rainfall series, physical cause or global environmental context is invented.")


def _utc_timestamp(value: str, offset_minutes: int | None = None) -> datetime:
    """ISO-8601 only; naive times require an explicitly declared UTC offset."""
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("Invalid rainfall timestamp")
    stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        if offset_minutes is None:
            raise ValueError("Naive timestamps need an explicit timezone offset")
        stamp = stamp.replace(tzinfo=timezone(timedelta(minutes=offset_minutes)))
    return stamp.astimezone(timezone.utc)


def _number(value, low: float, high: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not low <= value <= high:
        raise ValueError("Invalid rainfall " + name)
    return float(value)


def import_rainfall_bytes(content: bytes, metadata: dict, before_utc: str,
                          after_utc: str, *, filename: str,
                          study_bounds: tuple[float, float, float, float] = (79.95, 6.90, 80.00, 6.95)) -> dict:
    """Validate an owner's original CSV/TSV in memory; never write arbitrary paths.

    This records owner-declared provider metadata, not provider authentication.
    Each row is a recorded interval amount in mm, not a rate or a forecast.
    The original byte hash is retained; no interpolation or missing-value fill.
    """
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_UPLOAD_BYTES:
        raise ValueError("Rainfall upload must contain 1 to 5,000,000 bytes")
    if (not isinstance(filename, str) or len(filename) > 180 or Path(filename).name != filename
            or any(c in filename for c in ("/", "\\", ":")) or any(ord(c) < 32 for c in filename)
            or Path(filename).suffix.lower() not in (".csv", ".tsv")):
        raise ValueError("Rainfall upload needs a safe CSV/TSV basename")
    required = {"provider", "source_url", "retrieved_at_utc", "latitude", "longitude", "units",
                "quantity", "interval_hours", "time_column", "value_column", "delimiter",
                "timestamp_position", "timezone_offset_minutes", "variable", "record_basis"}
    if not isinstance(metadata, dict) or not required <= metadata.keys():
        raise ValueError("Rainfall provenance and explicit column/time/unit mapping are required")
    if metadata["provider"] != "Meteomatics" or metadata["record_basis"] != "historical_export":
        raise ValueError("This import supports owner-declared historical Meteomatics exports only")
    url = urlsplit(str(metadata["source_url"]))
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment
            or not (url.hostname == "meteomatics.com" or url.hostname.endswith(".meteomatics.com"))):
        raise ValueError("Use an unsigned public Meteomatics attribution URL; never an API credential URL")
    if metadata["units"] != "mm" or metadata["quantity"] != "interval_precipitation":
        raise ValueError("Only explicit interval precipitation amounts in mm are supported")
    interval = _number(metadata["interval_hours"], 1 / 60, 24, "interval")
    lat = _number(metadata["latitude"], -90, 90, "latitude")
    lon = _number(metadata["longitude"], -180, 180, "longitude")
    offset = metadata["timezone_offset_minutes"]
    if isinstance(offset, bool) or not isinstance(offset, int) or not -840 <= offset <= 840:
        raise ValueError("Explicit timezone offset must be integer minutes within +/-14 hours")
    position, delimiter = metadata["timestamp_position"], metadata["delimiter"]
    if position not in ("interval_start", "interval_end") or delimiter not in (",", ";", "\t"):
        raise ValueError("Explicit interval timestamp convention and CSV delimiter required")
    for key in ("time_column", "value_column", "variable"):
        if not isinstance(metadata[key], str) or not 0 < len(metadata[key]) <= 160 or any(ord(c) < 32 for c in metadata[key]):
            raise ValueError("Invalid rainfall column/variable declaration")
    variable = re.fullmatch(r"precip_(\d+(?:\.\d+)?)(h|min):mm", metadata["variable"])
    if variable is None:
        raise ValueError("Unsupported provider variable; declare a precip_<interval>h:mm or precip_<interval>min:mm amount")
    declared_hours = float(variable.group(1)) / (60 if variable.group(2) == "min" else 1)
    if abs(declared_hours - interval) > 1e-9:
        raise ValueError("Provider variable accumulation interval disagrees with the declared interval")
    retrieved = _utc_timestamp(metadata["retrieved_at_utc"])
    start, end = _utc_timestamp(before_utc), _utc_timestamp(after_utc)
    if start >= end:
        raise ValueError("Rainfall comparison requires ordered NISAR UTC acquisitions")
    west, south, east, north = study_bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Unsupported study bounds for rainfall context")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Rainfall upload must be UTF-8 CSV/TSV") from exc
    if "\x00" in text:
        raise ValueError("Rainfall upload contains binary data")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        fields = reader.fieldnames or []
    except csv.Error as exc:
        raise ValueError('Malformed or oversized rainfall CSV header') from exc
    if (len(fields) != len(set(fields)) or not {metadata["time_column"], metadata["value_column"]} <= set(fields)
            or metadata["time_column"] == metadata["value_column"]):
        raise ValueError("Rainfall columns missing, duplicated or ambiguous")
    rows, missing, previous = [], 0, None
    step = timedelta(hours=interval)
    for index, row in enumerate(_bounded_csv_rows(reader)):
        if index >= MAX_UPLOAD_ROWS:
            raise ValueError("Rainfall upload exceeds 10,000 rows")
        if None in row or row.get(metadata["time_column"]) is None or row.get(metadata["value_column"]) is None:
            raise ValueError("Malformed rainfall row")
        stamp = _utc_timestamp(row[metadata["time_column"]], offset)
        if previous is not None and stamp <= previous:
            raise ValueError("Rainfall times must be unique and increasing")
        if previous is not None and stamp - previous < step:
            raise ValueError("Rainfall accumulation intervals overlap")
        previous = stamp
        value_text = row[metadata["value_column"]].strip()
        if not value_text or value_text.lower() in ("nan", "na", "null", "none"):
            missing += 1
            continue
        try:
            value = float(value_text)
        except ValueError as exc:
            raise ValueError("Invalid rainfall measurement") from exc
        if not isfinite(value) or value < 0:
            raise ValueError("Rainfall amounts must be finite and nonnegative")
        a, b = (stamp, stamp + step) if position == "interval_start" else (stamp - step, stamp)
        rows.append(dict(utc=stamp.isoformat(), precipitation_mm=value,
                         interval_start_utc=a.isoformat(), interval_end_utc=b.isoformat()))
    if not rows:
        raise ValueError("No recorded finite rainfall values")
    within = [r for r in rows if _utc_timestamp(r["interval_start_utc"]) >= start and _utc_timestamp(r["interval_end_utc"]) <= end]
    periods = []
    for row in within:
        if not periods or periods[-1]["end_utc"] != row["interval_start_utc"]:
            periods.append(dict(start_utc=row["interval_start_utc"], end_utc=row["interval_end_utc"], total_mm=row["precipitation_mm"], sample_count=1))
        else:
            periods[-1]["end_utc"] = row["interval_end_utc"]
            periods[-1]["total_mm"] += row["precipitation_mm"]
            periods[-1]["sample_count"] += 1
    gaps = sum(_utc_timestamp(b["interval_start_utc"]) != _utc_timestamp(a["interval_end_utc"]) for a, b in zip(rows, rows[1:]))
    inside = west <= lon <= east and south <= lat <= north
    covers = _utc_timestamp(rows[0]["interval_start_utc"]) <= start and _utc_timestamp(rows[-1]["interval_end_utc"]) >= end
    available = bool(inside and within)
    canonical = {key: metadata[key] for key in sorted(required)}
    canonical.update(schema_version="1.1", filename=filename, sha256=hashlib.sha256(content).hexdigest(),
                     byte_length=len(content), retrieved_at_utc=retrieved.isoformat())
    reason = "Recorded context overlaps the study interval; inspect sampling limitations."
    if not inside:
        reason = "Observation point is outside the selected exploratory study rectangle; no local corroboration is assigned."
    elif not within:
        reason = "No complete valid rainfall accumulation interval lies within the two NISAR acquisitions."
    return dict(schema_version="1.1", status="CONTEXT_AVAILABLE" if available else "INSUFFICIENT_EVIDENCE",
                base_status="CONTEXT_AVAILABLE" if available else "INSUFFICIENT_EVIDENCE",
                provider="Meteomatics", provider_authenticity="OWNER_DECLARED_NOT_INDEPENDENTLY_AUTHENTICATED",
                metadata=canonical, rows=rows, missing_values=missing, interval_gaps=gaps,
                rows_in_acquisition_interval=len(within), point_inside_exploratory_rectangle=inside,
                covers_acquisition_interval=covers, acquisition_before_utc=start.isoformat(), acquisition_after_utc=end.isoformat(),
                accumulation_periods=periods, accumulation_rule="Complete valid recorded intervals wholly inside [before, after]; no boundary prorating or gap filling.",
                reason=reason, interpretation="Recorded rainfall context only; no causal, flood or SAR validation claim. Point sampling differs from radar pixels.",
                limitations=["Owner-declared source metadata is not independent provider authentication.",
                             "A point export is not spatially equivalent to the radar raster.",
                             "Boundary intervals are not prorated; missing intervals are not assumed dry.",
                             "Rainfall does not establish flooding or explain the physical cause of radar change."])


def assess_rainfall_hypothesis(context: dict, hypothesis: str = "NOT_ASSESSED") -> dict:
    """Optional transparent descriptor for a narrow hypothesis; never causation.

    Only the literal proposition that some precipitation occurred in complete
    sampled intervals is evaluated. A dry record can conflict only when the
    entire acquisition window is covered contiguously, without missing values.
    """
    result = dict(context)
    result["status"] = context.get("base_status", context.get("status", "INSUFFICIENT_EVIDENCE"))
    for field in ("hypothesis", "hypothesis_scope", "hypothesis_criterion"):
        result.pop(field, None)
    if hypothesis == "NOT_ASSESSED":
        return result
    if hypothesis != "PRECIPITATION_RECORDED_IN_COMPLETE_INTERVALS":
        raise ValueError("Unsupported environmental hypothesis")
    result["hypothesis"] = hypothesis
    result["hypothesis_scope"] = "Recorded point precipitation only; not flooding, rainfall causation or SAR validation."
    if result["status"] not in CONTEXT_STATES[:3]:
        result["status"] = "INSUFFICIENT_EVIDENCE"
        return result
    periods = context.get("accumulation_periods", [])
    if not periods:
        result["status"] = "INSUFFICIENT_EVIDENCE"
    elif any(p["total_mm"] > 0 for p in periods):
        result["status"] = "CONTEXT_POTENTIALLY_CONSISTENT"
        result["hypothesis_criterion"] = "At least one complete recorded interval contains >0 mm."
    else:
        complete = (len(periods) == 1 and periods[0]["start_utc"] == context.get("acquisition_before_utc")
                    and periods[0]["end_utc"] == context.get("acquisition_after_utc")
                    and context.get("missing_values") == 0 and context.get("interval_gaps") == 0)
        result["status"] = "CONTEXT_POTENTIALLY_CONFLICTING" if complete else "INSUFFICIENT_EVIDENCE"
        result["hypothesis_criterion"] = "All complete intervals are zero; conflicting only with exact contiguous acquisition-window coverage."
    return result


def load_recorded_rainfall(manifest: Path, before_utc: str, after_utc: str) -> dict:
    """Small explicit normalized CSV adapter; never guess units/coordinates/columns.

    Sidecar schema 1.0 maps time/value columns and requires original export hash,
    UTC time basis, interval/quantity, coordinates, retrieval date and source URL.
    New undocumented formats fail visibly rather than being silently converted.
    """
    if manifest.stat().st_size > 65536: raise ValueError("Witness sidecar exceeds limit")
    m = json.loads(manifest.read_text(encoding="utf-8"))
    required = {"schema_version","provider","filename","sha256","source_url","retrieved_at_utc","latitude","longitude",
                "units","time_basis","interval_hours","quantity","time_column","value_column"}
    if not required <= m.keys() or m["schema_version"] != "1.0" or m["provider"] != "Meteomatics":
        raise ValueError("Missing/unsupported witness source metadata")
    if (m["units"] != "mm" or m["time_basis"] != "UTC" or m["quantity"] != "interval_precipitation"
            or isinstance(m["interval_hours"],bool) or not isinstance(m["interval_hours"],(int,float)) or not 0 < m["interval_hours"] <= 24):
        raise ValueError("Unverified rainfall units, interval or quantity")
    if not -90 <= m["latitude"] <= 90 or not -180 <= m["longitude"] <= 180:
        raise ValueError("Invalid witness location")
    reference = urlsplit(m["source_url"])
    if reference.scheme != "https" or not reference.hostname or reference.username or reference.password or reference.query or reference.fragment:
        raise ValueError("Witness attribution must be an unsigned public HTTPS source reference")
    filename = m["filename"]
    if Path(filename).name != filename or ":" in filename or "\\" in filename or filename in (".",".."):
        raise ValueError("Witness file must be a local basename")
    path = manifest.parent/filename
    if path.is_symlink() or path.stat().st_size > 5_000_000 or sha256_file(path) != m["sha256"]:
        raise ValueError("Witness file integrity/size mismatch")
    def utc(value):
        d = datetime.fromisoformat(value.replace("Z","+00:00"))
        if d.tzinfo is None or d.utcoffset().total_seconds() != 0: raise ValueError("Explicit UTC timestamps required")
        return d
    start,end = utc(before_utc),utc(after_utc)
    if start >= end: raise ValueError("Invalid observation interval")
    rows, missing, previous = [],0,None
    with path.open(encoding="utf-8-sig",newline="") as stream:
        reader = csv.DictReader(stream)
        if not {m["time_column"],m["value_column"]} <= set(reader.fieldnames or []): raise ValueError("Witness columns missing")
        for row in reader:
            if len(rows)+missing >= 10000: raise ValueError("Witness row limit")
            t = utc(row[m["time_column"]])
            if previous is not None and t <= previous: raise ValueError("Witness times must be unique/increasing")
            previous = t
            text = row[m["value_column"]].strip()
            if not text or text.lower() in ("nan","null","na"):
                missing += 1
                continue
            value = float(text)
            if not isfinite(value) or value < 0: raise ValueError("Invalid precipitation")
            rows.append(dict(utc=t.isoformat(),precipitation_mm=value))
    if not rows: raise ValueError("No measured witness values")
    gaps = sum(abs((utc(b["utc"])-utc(a["utc"])).total_seconds()/3600-m["interval_hours"]) > 1e-6 for a,b in zip(rows,rows[1:]))
    in_window = [r for r in rows if start <= utc(r["utc"]) <= end]
    inside = 79.95 <= m["longitude"] <= 80.00 and 6.90 <= m["latitude"] <= 6.95
    covers = utc(rows[0]["utc"]) <= start and utc(rows[-1]["utc"]) >= end
    status = "CONTEXT_AVAILABLE" if inside and covers and in_window else "INSUFFICIENT_PHYSICAL_EVIDENCE"
    return dict(schema_version="1.0",status=status,provider=m["provider"],metadata=m,rows=rows,
                missing_values=missing,interval_gaps=gaps,rows_in_acquisition_interval=len(in_window),
                point_inside_exploratory_rectangle=inside,covers_acquisition_interval=covers,
                interpretation="Recorded precipitation context only; no causal, flood or SAR validation claim. Point sampling differs from radar pixels.")
