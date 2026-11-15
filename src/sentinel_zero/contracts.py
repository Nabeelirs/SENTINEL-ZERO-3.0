"""Version 1.0 JSON contracts. Unknown catalog fields are None or empty tuples.

Spatial coordinates are longitude/latitude degrees. Initial comparison supports
simple convex catalog polygons away from the antimeridian, not raster coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
import re
from typing import Any
from urllib.parse import urlsplit

from .errors import MetadataError

SCHEMA_VERSION = "1.0"
Point = tuple[float, float]
Ring = tuple[Point, ...]


def utc(value: str | datetime) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise MetadataError("Expected an ISO datetime with an explicit timezone") from exc


def jsonable(value: Any) -> Any:
    """Convert dataclasses, enums and UTC datetimes without lossy str(default)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return utc(value).isoformat().replace("+00:00", "Z")
    if hasattr(value, "__dataclass_fields__"):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def identifier(value: str, prefix: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"\d+-[A-Za-z0-9_]+", value):
        raise MetadataError(f"Invalid {prefix} concept identifier")


def coordinate(point: Point) -> None:
    if len(point) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not isfinite(x) for x in point):
        raise MetadataError("Coordinates must be two finite numbers")
    if not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
        raise MetadataError("Longitude/latitude outside valid ranges")


@dataclass(frozen=True)
class BoundingBox:
    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        coordinate((self.west, self.south))
        coordinate((self.east, self.north))
        if self.west >= self.east or self.south >= self.north:
            raise MetadataError("Expected west < east and south < north; split antimeridian AOIs")

    def parameter(self) -> str:
        return ",".join(str(x) for x in (self.west, self.south, self.east, self.north))

    def ring(self) -> Ring:
        return ((self.west, self.south), (self.east, self.south), (self.east, self.north), (self.west, self.north), (self.west, self.south))


@dataclass(frozen=True)
class CollectionDescriptor:
    concept_id: str
    short_name: str
    title: str
    version: str | None
    product_level: str | None
    maturity: str | None
    schema_version: str = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        identifier(self.concept_id, "C")
        if not isinstance(self.short_name, str) or not self.short_name.strip() or not isinstance(self.title, str) or not self.title.strip():
            raise MetadataError("Collection short name and title are required")
        if self.maturity not in (None, "VALIDATED", "PROVISIONAL", "BETA"):
            raise MetadataError("Unknown collection maturity")


@dataclass(frozen=True)
class Channel:
    frequency: str
    polarizations: tuple[str, ...]
    range_bandwidth_mhz: float | None

    def __post_init__(self) -> None:
        if self.frequency not in ("A", "B"):
            raise MetadataError("Unsupported NISAR frequency identifier")
        if any(x not in ("HH", "HV", "VH", "VV", "RH", "RV") for x in self.polarizations):
            raise MetadataError("Unsupported polarization identifier")
        if len(set(self.polarizations)) != len(self.polarizations):
            raise MetadataError("Duplicate polarization")
        if self.range_bandwidth_mhz is not None and (not isfinite(self.range_bandwidth_mhz) or self.range_bandwidth_mhz <= 0):
            raise MetadataError("Bandwidth must be finite and positive")


@dataclass(frozen=True)
class ProductReference:
    url: str
    relation: str
    format: str | None = None

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        # Preserve only unsigned canonical references. No URL is followed here.
        if parts.scheme not in ("https", "s3") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise MetadataError("Only unsigned canonical https/s3 references are allowed")
        if "credential" in parts.path.lower():
            raise MetadataError("Credential endpoints are not product references")


@dataclass(frozen=True)
class ObservationDescriptor:
    collection_concept_id: str
    granule_concept_id: str
    producer_granule_id: str
    acquisition_start: datetime
    acquisition_end: datetime | None
    collection_short_name: str | None = None
    collection_version: str | None = None
    product_family: str | None = None
    product_level: str | None = None
    maturity: str | None = None
    footprint: tuple[Ring, ...] = ()
    query_aoi: BoundingBox | None = None
    track: int | None = None
    frame: int | None = None
    stack_id: str | None = None
    orbit_direction: str | None = None
    look_direction: str | None = None
    orbit_numbers: tuple[int, ...] = ()
    channels: tuple[Channel, ...] = ()
    product_version: str | None = None
    crid: str | None = None
    pge_name: str | None = None
    pge_version: str | None = None
    sas_version: str | None = None
    product_references: tuple[ProductReference, ...] = ()
    missing_fields: tuple[str, ...] = ()
    metadata_warnings: tuple[str, ...] = ()
    source_metadata_url: str | None = None
    catalog_revision: int | None = None
    schema_version: str = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        identifier(self.collection_concept_id, "C")
        identifier(self.granule_concept_id, "G")
        if not isinstance(self.producer_granule_id, str) or not self.producer_granule_id.strip():
            raise MetadataError("Producer granule identifier is required")
        object.__setattr__(self, "acquisition_start", utc(self.acquisition_start))
        if self.acquisition_end is not None:
            object.__setattr__(self, "acquisition_end", utc(self.acquisition_end))
            if self.acquisition_end < self.acquisition_start:
                raise MetadataError("Acquisition end precedes start")
        for name in ("track", "frame", "catalog_revision"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise MetadataError(f"{name} must be a nonnegative integer")
        if self.orbit_direction not in (None, "ASCENDING", "DESCENDING") or self.look_direction not in (None, "LEFT", "RIGHT"):
            raise MetadataError("Invalid orbit/look direction")
        if self.maturity not in (None, "VALIDATED", "PROVISIONAL", "BETA"):
            raise MetadataError("Unknown product maturity")
        if len({c.frequency for c in self.channels}) != len(self.channels):
            raise MetadataError("Duplicate frequency channel")
        if any(type(value) is not int or value < 0 for value in self.orbit_numbers):
            raise MetadataError("Orbit numbers must be nonnegative integers")
        for name in ("collection_short_name", "collection_version", "product_family", "product_level", "stack_id", "product_version", "crid", "pge_name", "pge_version", "sas_version", "source_metadata_url"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise MetadataError(f"{name} must be nonempty text or unknown")
        if self.stack_id is not None and self.track is not None and self.frame is not None and self.orbit_direction is not None:
            expected_stack = f"{self.track:03d}_{self.orbit_direction[0]}_{self.frame:03d}"
            if self.stack_id != expected_stack:
                raise MetadataError("Stack identity disagrees with track, direction or frame")
        for ring in self.footprint:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise MetadataError("Catalog polygon must be closed with at least three vertices")
            for point in ring:
                coordinate(point)


@dataclass(frozen=True)
class DiscoveryQuery:
    collection_concept_id: str | None = None
    short_name: str | None = None
    bounding_box: BoundingBox | None = None
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    page_size: int = 10
    max_pages: int = 10
    max_results: int = 100
    sort_key: str = "-start_date"
    schema_version: str = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        if (self.collection_concept_id is None) == (self.short_name is None):
            raise MetadataError("Specify exactly one collection ID or collection short-name pattern")
        if self.collection_concept_id:
            identifier(self.collection_concept_id, "C")
        if self.short_name is not None and not re.fullmatch(r"[A-Za-z0-9_*?]{1,160}", self.short_name):
            raise MetadataError("Invalid short-name pattern")
        for name, upper in (("page_size", 2000), ("max_pages", 100), ("max_results", 1000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise MetadataError(f"{name} must be an integer in 1..{upper}")
        if self.page_size > self.max_results:
            raise MetadataError("page_size exceeds max_results")
        if self.sort_key not in ("-start_date", "start_date"):
            raise MetadataError("Unsupported granule sort key")
        if (self.temporal_start is None) != (self.temporal_end is None):
            raise MetadataError("Both temporal bounds are required")
        if self.temporal_start is not None and self.temporal_end is not None:
            object.__setattr__(self, "temporal_start", utc(self.temporal_start))
            object.__setattr__(self, "temporal_end", utc(self.temporal_end))
            if self.temporal_start >= self.temporal_end:
                raise MetadataError("Query temporal start must precede end")


@dataclass(frozen=True)
class DiscoveryFailure:
    code: str
    message: str
    page: int | None = None
    record_id: str | None = None
    http_status: int | None = None
    schema_version: str = field(default=SCHEMA_VERSION, init=False)


@dataclass(frozen=True)
class PageRecord:
    url: str
    requested_at: datetime
    retrieved_at: datetime
    http_status: int
    attempts: int
    hits: int
    records: int
    body_sha256: str
    search_after_sent: str | None
    search_after_received: str | None


@dataclass(frozen=True)
class DiscoveryResult:
    query: DiscoveryQuery
    started_at: datetime
    finished_at: datetime
    matching_count: int | None
    retrieved_count: int
    pagination_complete: bool
    observations: tuple[ObservationDescriptor, ...] = ()
    collections: tuple[CollectionDescriptor, ...] = ()
    pages: tuple[PageRecord, ...] = ()
    failures: tuple[DiscoveryFailure, ...] = ()
    schema_version: str = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        if utc(self.finished_at) < utc(self.started_at) or self.retrieved_count < 0:
            raise MetadataError("Invalid result time/count")
        if self.retrieved_count != len(self.observations) + len(self.collections):
            raise MetadataError("Retrieved count must equal successfully parsed unique records")
        if self.pagination_complete and (self.failures or self.matching_count != self.retrieved_count):
            raise MetadataError("Complete retrieval requires consistent counts and no failures")


class PairOutcome(str, Enum):
    PRELIMINARY_CANDIDATE = "PRELIMINARY_CANDIDATE"
    INCOMPATIBLE = "INCOMPATIBLE"
    INSUFFICIENT_METADATA = "INSUFFICIENT_METADATA"


@dataclass(frozen=True)
class PairCheck:
    name: str
    state: str
    before: Any
    after: Any
    reason: str

    def __post_init__(self) -> None:
        if self.state not in ("PASS", "FAIL", "UNKNOWN"):
            raise MetadataError("Invalid pair-check state")


@dataclass(frozen=True)
class CandidatePairAssessment:
    before_id: str
    after_id: str
    frequency: str
    polarization: str
    outcome: PairOutcome
    checks: tuple[PairCheck, ...]
    temporal_separation_seconds: float
    scientific_acceptance: bool = field(default=False, init=False)
    limitations: tuple[str, ...] = ("Catalog comparison only; full GCOV content, quality, grid, radiometry and valid-pixel overlap remain uninspected.",)
    schema_version: str = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        identifier(self.before_id, "G")
        identifier(self.after_id, "G")
        if not isinstance(self.outcome, PairOutcome) or not self.checks:
            raise MetadataError("Pair outcome and checks required")
        if not isfinite(self.temporal_separation_seconds) or self.temporal_separation_seconds < 0:
            raise MetadataError("Invalid temporal separation")
        expected = PairOutcome.INCOMPATIBLE if any(c.state == "FAIL" for c in self.checks) else PairOutcome.INSUFFICIENT_METADATA if any(c.state == "UNKNOWN" for c in self.checks) else PairOutcome.PRELIMINARY_CANDIDATE
        if self.outcome != expected:
            raise MetadataError("Pair outcome disagrees with its checks")
