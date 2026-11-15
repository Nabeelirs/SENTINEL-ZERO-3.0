"""Conservative deterministic catalog screening, never scientific acceptance.

Geometry uses planar convex-polygon clipping only for local, nonpolar catalog
footprints. Unsupported polygons return UNKNOWN; no geodesic area is reported.
"""

from __future__ import annotations

from itertools import combinations
from math import atan2, pi

from ..contracts import (BoundingBox, CandidatePairAssessment, ObservationDescriptor,
                         PairCheck, PairOutcome, Point, Ring)
from ..errors import MetadataError


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _signed_area(points: list[Point]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1])) / 2


def _convex(ring: Ring) -> list[Point] | None:
    points = list(ring[:-1])
    if len(points) < 3 or len(set(points)) != len(points):
        return None
    if max(x for x, _ in points) - min(x for x, _ in points) >= 180 or any(abs(y) > 80 for _, y in points):
        return None
    area = _signed_area(points)
    if abs(area) < 1e-12:
        return None
    if area < 0:
        points.reverse()
    if any(_cross(points[i - 1], points[i], points[(i + 1) % len(points)]) <= 1e-12 for i in range(len(points))):
        return None
    # Reject self-intersecting stars that can have consistent local turn signs.
    angles = []
    for i in range(len(points)):
        a, b, c = points[i - 1], points[i], points[(i + 1) % len(points)]
        ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], c[0] - b[0], c[1] - b[1]
        angles.append(atan2(ux * vy - uy * vx, ux * vx + uy * vy))
    if abs(sum(angles) - 2 * pi) > 1e-7:
        return None
    return points


def _clip(subject: list[Point], boundary: list[Point]) -> list[Point]:
    for a, b in zip(boundary, boundary[1:] + boundary[:1]):
        output = []
        if not subject:
            return []
        previous = subject[-1]
        dp = _cross(a, b, previous)
        for current in subject:
            dc = _cross(a, b, current)
            if (dc >= 0) != (dp >= 0):
                fraction = dp / (dp - dc)
                output.append((previous[0] + fraction * (current[0] - previous[0]), previous[1] + fraction * (current[1] - previous[1])))
            if dc >= 0:
                output.append(current)
            previous, dp = current, dc
        subject = output
    return subject


def footprint_overlap(before: ObservationDescriptor, after: ObservationDescriptor, aoi: BoundingBox | None) -> bool | None:
    """Positive approximate polygon overlap inside query AOI; not valid-pixel area."""
    if not before.footprint or not after.footprint:
        return None
    first = [_convex(r) for r in before.footprint]
    second = [_convex(r) for r in after.footprint]
    if any(p is None for p in first + second):
        return None
    for p in first:
        for q in second:
            intersection = _clip(p or [], q or [])
            if aoi is not None:
                intersection = _clip(intersection, list(aoi.ring()[:-1]))
            if len(intersection) >= 3 and abs(_signed_area(intersection)) > 1e-12:
                return True
    return False


def assess_pair(first: ObservationDescriptor, second: ObservationDescriptor, *, frequency: str = "A", polarization: str = "HH", aoi: BoundingBox | None = None) -> CandidatePairAssessment:
    if frequency not in ("A", "B") or polarization not in ("HH", "HV", "VH", "VV", "RH", "RV"):
        raise MetadataError("Unsupported requested channel")
    before, after = sorted((first, second), key=lambda o: (o.acquisition_start, o.granule_concept_id))
    checks: list[PairCheck] = []

    def condition(name: str, x: object, y: object, passed: bool | None, reason: str) -> None:
        checks.append(PairCheck(name, "UNKNOWN" if passed is None else "PASS" if passed else "FAIL", x, y, reason))

    def same(name: str, x: object, y: object) -> None:
        condition(name, x, y, None if x is None or y is None else x == y, "Both known values must match; missing is unknown")

    condition("distinct_observations", before.granule_concept_id, after.granule_concept_id,
              before.granule_concept_id != after.granule_concept_id, "Different catalog granules required")
    condition("temporal_separation", before.acquisition_end, after.acquisition_start,
              None if before.acquisition_end is None or after.acquisition_end is None else before.acquisition_start < after.acquisition_start and before.acquisition_end < after.acquisition_start,
              "Distinct, nonoverlapping acquisition intervals required")
    same("collection", before.collection_concept_id, after.collection_concept_id)
    same("collection_version", before.collection_version, after.collection_version)
    for field in ("product_family", "product_level", "maturity", "track", "frame", "stack_id", "orbit_direction", "look_direction", "product_version", "crid", "pge_name", "pge_version", "sas_version"):
        same(field, getattr(before, field), getattr(after, field))
    for name, expected in (("product_family", "GCOV"), ("product_level", "L2")):
        x, y = getattr(before, name), getattr(after, name)
        condition("supported_" + name, x, y, None if x is None or y is None else x == y == expected, f"Initial workflow requires {expected}")
    condition("supported_maturity", before.maturity, after.maturity,
              None if before.maturity is None or after.maturity is None else before.maturity in ("VALIDATED", "PROVISIONAL") and after.maturity in ("VALIDATED", "PROVISIONAL"),
              "BETA excluded; PROVISIONAL requires later quality review")
    x = next((c for c in before.channels if c.frequency == frequency), None)
    y = next((c for c in after.channels if c.frequency == frequency), None)
    condition("frequency", tuple(c.frequency for c in before.channels), tuple(c.frequency for c in after.channels),
              None if not before.channels or not after.channels else x is not None and y is not None,
              f"Requested frequency {frequency} must be catalogued in both products")
    condition("polarization", x.polarizations if x else None, y.polarizations if y else None,
              None if x is None or y is None or not x.polarizations or not y.polarizations else polarization in x.polarizations and polarization in y.polarizations and set(x.polarizations) == set(y.polarizations),
              f"Requested {polarization} required; conservative rule requires matching polarization sets")
    same("range_bandwidth_mhz", x.range_bandwidth_mhz if x else None, y.range_bandwidth_mhz if y else None)
    condition("catalog_footprint_overlap", before.footprint, after.footprint, footprint_overlap(before, after, aoi),
              "Positive convex catalog-polygon overlap within AOI; unsupported geometry is unknown; valid pixels unverified")
    for observation in (before, after):
        condition("canonical_hdf5_reference_" + observation.granule_concept_id, None, None,
                  True if any(r.url.startswith("https://") and r.relation == "GET DATA" and r.format == "HDF5" for r in observation.product_references) else None,
                  "Advertised canonical HDF5 link only; authorization, file existence and usability untested")
    outcome = PairOutcome.INCOMPATIBLE if any(c.state == "FAIL" for c in checks) else PairOutcome.INSUFFICIENT_METADATA if any(c.state == "UNKNOWN" for c in checks) else PairOutcome.PRELIMINARY_CANDIDATE
    return CandidatePairAssessment(before.granule_concept_id, after.granule_concept_id, frequency, polarization, outcome, tuple(checks), (after.acquisition_start - before.acquisition_start).total_seconds())


def assess_candidates(observations: tuple[ObservationDescriptor, ...], *, frequency: str = "A", polarization: str = "HH", aoi: BoundingBox | None = None) -> tuple[CandidatePairAssessment, ...]:
    if len(observations) > 100:
        raise MetadataError("Pair assessment limited to 100 observations (4,950 comparisons)")
    by_id = {o.granule_concept_id: o for o in observations}
    if len(by_id) != len(observations):
        raise MetadataError("Duplicate observation IDs in candidate assessment")
    pairs = [assess_pair(a, b, frequency=frequency, polarization=polarization, aoi=aoi) for a, b in combinations(observations, 2)]

    def rank(pair: CandidatePairAssessment) -> tuple:
        after = by_id[pair.after_id]
        # Screening outcome, maturity, latest acquisition, closest predecessor.
        # This is a reproducible inspection order, not a physical-event ranking.
        return ({PairOutcome.PRELIMINARY_CANDIDATE: 0, PairOutcome.INSUFFICIENT_METADATA: 1, PairOutcome.INCOMPATIBLE: 2}[pair.outcome],
                {"VALIDATED": 0, "PROVISIONAL": 1}.get(after.maturity or "", 2),
                -after.acquisition_start.timestamp(), pair.temporal_separation_seconds, pair.before_id, pair.after_id)
    return tuple(sorted(pairs, key=rank))
